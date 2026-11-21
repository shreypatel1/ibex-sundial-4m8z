"""Full-corpus driver: download -> rewrite -> upload in batches (bounded disk).

python3 -m deid.driver IDS.json MANIFEST.tsv WORKDIR gs://.../attempt-N/ [BATCH_GB]
Token: WORKDIR/token.txt (upload.py --token-file renews from it).
Done rows are recorded in WORKDIR/done.txt so the run can resume.
"""
import os, sys, json, shutil, subprocess, threading, time, urllib.request
from . import runlocal

BASE = "https://machina-input.joinbuilders.org/"
UPLOAD = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "upload.py")


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def fetch(rows, dest, jobs=32):
    from concurrent.futures import ThreadPoolExecutor
    def get(r):
        p, b, _, u = r
        out = os.path.join(dest, p)
        if os.path.exists(out) and os.path.getsize(out) == int(b):
            return 0
        os.makedirs(os.path.dirname(out), exist_ok=True)
        if int(b) == 0:
            open(out, "wb").close(); return 0
        for i in range(6):
            try:
                with urllib.request.urlopen(BASE + u, timeout=300) as resp, open(out, "wb") as f:
                    shutil.copyfileobj(resp, f, 1 << 20)
                if os.path.getsize(out) == int(b):
                    return 0
            except Exception as e:
                err = e
                time.sleep(2 * (i + 1))
        log("FETCH FAIL", p); return 1
    with ThreadPoolExecutor(jobs) as ex:
        return sum(ex.map(get, rows))


def upload(corpus_dir, dest, workdir):
    tok = open(os.path.join(workdir, "token.txt")).read().strip()
    env = dict(os.environ, MACHINA_UPLOAD_TOKEN=tok)
    for attempt in range(20):
        tok = open(os.path.join(workdir, "token.txt")).read().strip()
        env["MACHINA_UPLOAD_TOKEN"] = tok
        r = subprocess.run([sys.executable, UPLOAD, "--dest", dest, "--corpus", corpus_dir,
                            "--token-file", os.path.join(workdir, "token.txt"), "--workers", "32"],
                           env=env, capture_output=True, text=True)
        tail = (r.stdout + r.stderr)[-600:]
        log("upload rc", r.returncode, tail.replace("\n", " | ")[-300:])
        if r.returncode == 0:
            return True
        log("UPLOAD RETRY (need fresh token?)")
        time.sleep(30)
    return False


def _harvest_one(item):
    from .harvest import harvest_bytes
    root, rel = item
    seen = []
    def emit(v, k):
        if v and v.strip():
            seen.append((v.strip(), k))
    try:
        with open(os.path.join(root, rel), "rb") as fh:
            harvest_bytes(rel, fh.read(), emit)
    except Exception:
        pass
    return rel, sorted(set(seen))


def harvest_main():
    """python3 -m deid.driver harvest MANIFEST WORKDIR OUT.jsonl [BATCH_GB]"""
    import multiprocessing as mp
    manifest, workdir, outp = sys.argv[2:5]
    batch_gb = float(sys.argv[5]) if len(sys.argv) > 5 else 8
    os.makedirs(workdir, exist_ok=True)
    donef = os.path.join(workdir, "hdone.txt")
    done = set(open(donef).read().splitlines()) if os.path.exists(donef) else set()
    rows = [l.rstrip("\n").split("\t") for l in open(manifest, encoding="utf-8") if not l.startswith("#")]
    rows = [r for r in rows if r[0] not in done]
    batches, cur, sz = [], [], 0
    for r in rows:
        cur.append(r); sz += int(r[1])
        if sz >= batch_gb * 1e9:
            batches.append(cur); cur, sz = [], 0
    if cur:
        batches.append(cur)
    log(len(rows), "rows to harvest in", len(batches), "batches")
    def prefetch(k):
        fetch(batches[k], os.path.join(workdir, f"h_{k}"))
    th = threading.Thread(target=prefetch, args=(0,)); th.start()
    out = open(outp, "a", encoding="utf-8")
    for k, b in enumerate(batches):
        th.join()
        if k + 1 < len(batches):
            th = threading.Thread(target=prefetch, args=(k + 1,)); th.start()
        d = os.path.join(workdir, f"h_{k}")
        items = [(d, r[0]) for r in b if os.path.exists(os.path.join(d, r[0]))]
        t = time.time()
        with mp.Pool(4) as pool:
            for rel, seen in pool.imap_unordered(_harvest_one, items, chunksize=4):
                for v, kk in seen:
                    out.write(json.dumps({"v": v, "k": kk, "f": rel}, ensure_ascii=False) + "\n")
        out.flush()
        with open(donef, "a") as f:
            f.write("\n".join(r[0] for r in b) + "\n")
        shutil.rmtree(d, ignore_errors=True)
        log(f"hbatch {k}: {len(items)} files in {time.time()-t:.0f}s")
    log("HARVEST DONE")


def main():
    if sys.argv[1] == "harvest":
        return harvest_main()
    ids, manifest, workdir, dest = sys.argv[1:5]
    batch_gb = float(sys.argv[5]) if len(sys.argv) > 5 else 8
    os.makedirs(workdir, exist_ok=True)
    donef = os.path.join(workdir, "done.txt")
    done = set(open(donef).read().splitlines()) if os.path.exists(donef) else set()
    rows = [l.rstrip("\n").split("\t") for l in open(manifest, encoding="utf-8") if not l.startswith("#")]
    rows = [r for r in rows if r[0] not in done]
    batches, cur, sz = [], [], 0
    for r in rows:
        cur.append(r); sz += int(r[1])
        if sz >= batch_gb * 1e9:
            batches.append(cur); cur, sz = [], 0
    if cur:
        batches.append(cur)
    log(len(rows), "rows to do in", len(batches), "batches")
    pre = {}
    def prefetch(k):
        d = os.path.join(workdir, f"in_{k}")
        pre[k] = fetch(batches[k], d)
    th = threading.Thread(target=prefetch, args=(0,)); th.start()
    up = None
    for k, b in enumerate(batches):
        th.join()
        if k + 1 < len(batches):
            th = threading.Thread(target=prefetch, args=(k + 1,)); th.start()
        ind = os.path.join(workdir, f"in_{k}")
        outd = os.path.join(workdir, f"out_{k}", "corpus")
        t = time.time()
        runlocal.main(ids, ind, outd, [r[0] for r in b if os.path.exists(os.path.join(ind, r[0]))],
                      procs=4, report=os.path.join(workdir, "report.jsonl"))
        log(f"batch {k}: processed {len(b)} files in {time.time()-t:.0f}s")
        shutil.rmtree(ind, ignore_errors=True)
        if up is not None:
            up.join()
        def do_up(k=k, b=b, outd=outd):
            ok = upload(outd, dest, workdir)
            if ok:
                with open(donef, "a") as f:
                    f.write("\n".join(r[0] for r in b) + "\n")
                shutil.rmtree(os.path.dirname(outd), ignore_errors=True)
            else:
                log("BATCH UPLOAD FAILED", k)
        up = threading.Thread(target=do_up); up.start()
    if up is not None:
        up.join()
    log("ALL DONE")


if __name__ == "__main__":
    main()
