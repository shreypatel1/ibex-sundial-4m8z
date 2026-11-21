#!/usr/bin/env python3
"""Upload a Machina - CAD De-ID attempt to your team's Builders folder.

This is for your OUTPUT only. The track page says how to get the input.

Uploads corpus/, code/ and README.txt into one attempt folder of your team's
private folder, using the 1-hour upload token from the track page. Python 3,
nothing to install. Files already uploaded (same size and MD5) are skipped, so
running it again resumes. When the token runs out mid-upload, it asks for a
new one (press the button on the track page again and paste it) and carries
on. With --token-file it waits for a new token in that file instead, for runs
with no terminal.

Usage:
    export MACHINA_UPLOAD_TOKEN="<token from the track page>"
    python3 upload.py \\
      --dest gs://builders-machina-submissions-1120/teams/<your-team-id>/attempt-1/ \\
      --corpus ./cleaned-corpus \\
      --code ./code \\
      --readme ./README.txt
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import getpass
import hashlib
import http.client
import os
from pathlib import Path
import sys
import threading
import time
import urllib.parse
import xml.etree.ElementTree as ET

HOST = "storage.googleapis.com"
CHUNK_BYTES = 1024 * 1024
MAX_RETRIES = 4
# How long --token-file waits for a new token before giving up.
TOKEN_FILE_WAIT_S = 2 * 3600


class TokenExpired(Exception):
    pass


def token_expired(status: int, body: bytes) -> bool:
    return status == 401 or (status in (400, 403) and b"xpired" in body)


class Tokens:
    """The current upload token, renewed once for every thread when it runs out."""

    def __init__(self, token: str, token_file: Path | None) -> None:
        self.token = token
        self.token_file = token_file
        self.lock = threading.Lock()

    def current(self) -> str:
        return self.token

    def renew(self, stale: str) -> str:
        with self.lock:
            if self.token != stale:
                return self.token  # another thread already renewed it
            fresh = self._ask(stale)
            if not fresh or fresh == stale:
                raise TokenExpired()
            self.token = fresh
            print("Got a new token; carrying on.", flush=True)
            return fresh

    def _ask(self, stale: str) -> str:
        message = "The upload token ran out. Press 'Get a new upload token' on the track page"
        if self.token_file is not None:
            print(f"{message} and write it to {self.token_file}; waiting ...", flush=True)
            deadline = time.monotonic() + TOKEN_FILE_WAIT_S
            while time.monotonic() < deadline:
                try:
                    fresh = self.token_file.read_text().strip()
                except OSError:
                    fresh = ""
                if fresh and fresh != stale:
                    return fresh
                time.sleep(3)
            return ""
        if sys.stdin.isatty():
            try:
                return getpass.getpass(f"{message} and paste it here: ").strip()
            except (EOFError, KeyboardInterrupt):
                return ""
        return ""


def parse_dest(dest: str) -> tuple[str, str]:
    if not dest.startswith("gs://"):
        raise ValueError("Destination must start with gs://")
    rest = dest[len("gs://") :]
    if "/" not in rest:
        raise ValueError("Destination must include a folder prefix ending in /")
    bucket, prefix = rest.split("/", 1)
    if not prefix.endswith("/"):
        prefix += "/"
    return bucket, prefix


def encode_key(key: str) -> str:
    return "/".join(urllib.parse.quote(part, safe="") for part in key.split("/"))


def list_existing(bucket: str, prefix: str, tokens: Tokens) -> dict[str, tuple[int, str]]:
    """Returns {object_key: (size, etag_md5_hex)} already uploaded under prefix."""
    found: dict[str, tuple[int, str]] = {}
    continuation: str | None = None
    conn = http.client.HTTPSConnection(HOST, timeout=60)
    try:
        while True:
            params = {
                "list-type": "2",
                "prefix": prefix,
                "max-keys": "1000",
                "encoding-type": "url",
            }
            if continuation:
                params["continuation-token"] = continuation
            path = f"/{bucket}/?{urllib.parse.urlencode(params)}"
            token = tokens.current()
            conn.request("GET", path, headers={"Authorization": f"Bearer {token}"})
            resp = conn.getresponse()
            body = resp.read()
            if token_expired(resp.status, body):
                tokens.renew(token)
                continue
            if resp.status != 200:
                raise RuntimeError(f"List failed ({resp.status}): {body.decode('utf-8', 'replace')[:400]}")
            root = ET.fromstring(body)
            ns = ""
            if root.tag.startswith("{"):
                ns = root.tag.split("}", 1)[0] + "}"
            for item in root.findall(f"{ns}Contents"):
                raw_key = item.findtext(f"{ns}Key") or ""
                key = urllib.parse.unquote_plus(raw_key)
                size = int(item.findtext(f"{ns}Size") or "0")
                etag = (item.findtext(f"{ns}ETag") or "").strip('"').lower()
                found[key] = (size, etag)
            if root.findtext(f"{ns}IsTruncated") != "true":
                break
            continuation = root.findtext(f"{ns}NextContinuationToken")
            if not continuation:
                break
    finally:
        conn.close()
    return found


def file_md5(path: Path) -> str:
    digest = hashlib.md5()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(CHUNK_BYTES)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def collect_files(
    prefix: str, corpus: Path | None, code: Path | None, readme: Path | None
) -> list[tuple[Path, str]]:
    items: list[tuple[Path, str]] = []
    for folder, sub in ((corpus, "corpus"), (code, "code")):
        if folder is None:
            continue
        if not folder.is_dir():
            raise SystemExit(f"Not a directory: {folder}")
        for root, _dirs, files in os.walk(folder):
            for name in sorted(files):
                local = Path(root) / name
                if local.is_symlink() or not local.is_file():
                    continue
                rel = local.relative_to(folder).as_posix()
                items.append((local, f"{prefix}{sub}/{rel}"))
    if readme is not None:
        if not readme.is_file():
            raise SystemExit(f"Not a file: {readme}")
        items.append((readme, f"{prefix}README.txt"))
    return items


class WorkerClient:
    def __init__(self, bucket: str, tokens: Tokens) -> None:
        self.bucket = bucket
        self.tokens = tokens
        self.stop = threading.Event()
        self.local = threading.local()

    def _conn(self) -> http.client.HTTPSConnection:
        conn = getattr(self.local, "conn", None)
        if conn is None:
            conn = http.client.HTTPSConnection(HOST, timeout=120)
            self.local.conn = conn
        return conn

    def _reset(self) -> None:
        conn = getattr(self.local, "conn", None)
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
            self.local.conn = None

    def put(self, local_path: Path, object_key: str) -> int:
        if self.stop.is_set():
            raise TokenExpired()
        size = local_path.stat().st_size
        url_path = f"/{self.bucket}/{encode_key(object_key)}"
        attempt = 0
        while attempt < MAX_RETRIES:
            attempt += 1
            token = self.tokens.current()
            try:
                conn = self._conn()
                conn.putrequest("PUT", url_path)
                conn.putheader("Authorization", f"Bearer {token}")
                conn.putheader("Content-Length", str(size))
                conn.putheader("Content-Type", "application/octet-stream")
                conn.endheaders()
                with local_path.open("rb") as handle:
                    while True:
                        chunk = handle.read(CHUNK_BYTES)
                        if not chunk:
                            break
                        conn.send(chunk)
                resp = conn.getresponse()
                body = resp.read()
                if token_expired(resp.status, body):
                    try:
                        self.tokens.renew(token)
                    except TokenExpired:
                        self.stop.set()
                        raise
                    attempt -= 1  # a renewed token is not a failed try
                    continue
                if 200 <= resp.status < 300:
                    return size
                if resp.status >= 500 or resp.status == 429:
                    self._reset()
                    if attempt < MAX_RETRIES:
                        time.sleep(0.5 * (2**attempt))
                        continue
                raise RuntimeError(
                    f"PUT {object_key} failed ({resp.status}): {body.decode('utf-8', 'replace')[:300]}"
                )
            except TokenExpired:
                raise
            except (OSError, http.client.HTTPException):
                self._reset()
                if attempt >= MAX_RETRIES:
                    raise
                time.sleep(0.5 * (2**attempt))
        raise RuntimeError(f"PUT {object_key} failed after {MAX_RETRIES} retries")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dest", required=True, help="gs://<bucket>/teams/<team-id>/attempt-<N>/")
    parser.add_argument("--corpus", type=Path, help="Path to cleaned corpus/ directory")
    parser.add_argument("--code", type=Path, help="Path to code/ directory")
    parser.add_argument("--readme", type=Path, help="Path to README.txt")
    parser.add_argument(
        "--token",
        default=os.environ.get("MACHINA_UPLOAD_TOKEN", ""),
        help="Upload token from the track page (or set MACHINA_UPLOAD_TOKEN)",
    )
    parser.add_argument(
        "--token-file",
        type=Path,
        help="When the token runs out, wait for a new one in this file instead of asking",
    )
    parser.add_argument("--workers", type=int, default=16, help="Parallel upload threads (default: 16)")
    parser.add_argument(
        "--no-resume",
        action="store_true",
        help="Upload every file without checking already-uploaded objects first",
    )
    args = parser.parse_args()

    token = args.token.strip()
    if not token:
        raise SystemExit("Missing upload token: pass --token or set MACHINA_UPLOAD_TOKEN.")
    if not (args.corpus or args.code or args.readme):
        raise SystemExit("Pass at least one of --corpus, --code, or --readme.")

    tokens = Tokens(token, args.token_file)
    bucket, prefix = parse_dest(args.dest.strip())
    items = collect_files(prefix, args.corpus, args.code, args.readme)
    if not items:
        raise SystemExit("No files found to upload.")

    existing: dict[str, tuple[int, str]] = {}
    if not args.no_resume:
        print(f"Checking already-uploaded files under gs://{bucket}/{prefix} ...", flush=True)
        try:
            existing = list_existing(bucket, prefix, tokens)
        except TokenExpired:
            raise SystemExit(
                "The upload token ran out. Press 'Get a new upload token' on the track page and run this again."
            )

    to_upload: list[tuple[Path, str]] = []
    skipped = 0
    for local_path, object_key in items:
        remote = existing.get(object_key)
        if remote is not None:
            r_size, r_etag = remote
            # Only an MD5 match counts: an ETag that is not an MD5 (a composite
            # upload's) says nothing about the content, so upload it again.
            if (
                local_path.stat().st_size == r_size
                and len(r_etag) == 32
                and file_md5(local_path) == r_etag
            ):
                skipped += 1
                continue
        to_upload.append((local_path, object_key))

    total = len(to_upload)
    print(f"Found {len(items)} files ({skipped} already uploaded, {total} to upload).", flush=True)
    if total == 0:
        print(f"Done! Submit this location: gs://{bucket}/{prefix}")
        return

    client = WorkerClient(bucket, tokens)
    done = 0
    uploaded_bytes = 0
    started = time.monotonic()
    try:
        with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
            futures = {pool.submit(client.put, p, k): k for p, k in to_upload}
            for future in as_completed(futures):
                uploaded_bytes += future.result()
                done += 1
                if done % 250 == 0 or done == total:
                    elapsed = max(0.1, time.monotonic() - started)
                    mb_s = uploaded_bytes / (1024 * 1024) / elapsed
                    print(
                        f"  {done}/{total} files ({uploaded_bytes / 1e9:.2f} GB, {mb_s:.1f} MB/s)",
                        flush=True,
                    )
    except TokenExpired:
        raise SystemExit(
            f"\nThe upload token ran out after {done}/{total} files. "
            "Press 'Get a new upload token' on the track page, set MACHINA_UPLOAD_TOKEN "
            "and run the same command again; files already uploaded are skipped."
        )

    print(f"\nDone! Submit this location on the track page:\n  gs://{bucket}/{prefix}")


if __name__ == "__main__":
    main()
