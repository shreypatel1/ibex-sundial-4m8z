"""Deterministic, length-preserving pseudonyms.

Every identity string is mapped token by token: a run of ASCII letters becomes a
pronounceable word of the same length and case pattern, a run of digits becomes
other digits, a non-ASCII character becomes ASCII letters, and punctuation and
spaces stay where they are. The mapping is keyed by a secret (MACHINA_KEY), so
the same original token always gets the same pseudonym anywhere in the corpus,
and different tokens get different ones.
"""
import hashlib, hmac, os, re

KEY = os.environ.get("MACHINA_KEY", "change-me").encode()
CONS = "bcdfghklmnprstvz"
VOWS = "aeiou"
KEEP_LABELS = {"www", "com", "org", "net", "edu", "gov", "de", "uk", "co", "fr", "it",
               "es", "nl", "jp", "cn", "ru", "io", "ch", "at", "be", "se", "no", "dk",
               "fi", "pl", "cz", "ca", "au", "us", "in", "br", "kr", "tw", "hu", "tr",
               "info", "eu", "ac", "mx", "ar", "pt", "gr", "ie", "nz", "sg", "hk", "il"}
_cache = {}


def _stream(key: str, salt: str = ""):
    i = 0
    while True:
        d = hmac.new(KEY, f"{salt}|{key}|{i}".encode(), hashlib.sha256).digest()
        yield from d
        i += 1


def _word(key: str, n: int) -> str:
    k = ("w", key, n)
    if k in _cache:
        return _cache[k]
    for salt in range(50):
        s = _stream(key, str(salt))
        out = []
        for j in range(n):
            pool = CONS if j % 2 == 0 else VOWS
            out.append(pool[next(s) % len(pool)])
        w = "".join(out)
        if w != key:
            break
    _cache[k] = w
    return w


def _digits(key: str, n: int) -> str:
    k = ("d", key, n)
    if k in _cache:
        return _cache[k]
    for salt in range(50):
        s = _stream(key, "d" + str(salt))
        w = "".join(str(next(s) % 10) for _ in range(n))
        if w != key or n == 0:
            break
    _cache[k] = w
    return w


def _case(src: str, w: str) -> str:
    return "".join(c.upper() if s.isupper() else c for s, c in zip(src, w))


TOK = re.compile(r"[A-Za-z]+|[0-9]+|[^\x00-\x7f]+|.", re.S)


def pseudo_str(s: str, utf8_len: bool = True, domain_aware: bool = True) -> str:
    """Pseudonym for an identity string.

    utf8_len=True keeps the UTF-8 byte length (non-ASCII char -> as many ASCII
    letters as it had bytes); False keeps the character count (for UTF-16).
    """
    labels_keep = set()
    if domain_aware and re.fullmatch(r"[\w.-]+\.[A-Za-z]{2,6}", s.split("@")[-1] or ""):
        # domain or email: keep www and the TLD-ish labels so it still looks like one
        dom = s.split("@")[-1]
        labs = dom.split(".")
        start = len(s) - len(dom)
        pos = start
        for i, lab in enumerate(labs):
            if (i == len(labs) - 1 or (i == 0 and lab.lower() == "www") or
                    (i >= len(labs) - 2 and lab.lower() in KEEP_LABELS and len(labs) > 2)):
                labels_keep.add((pos, pos + len(lab)))
            pos += len(lab) + 1
    out = []
    for m in TOK.finditer(s):
        t = m.group(0)
        if any(a <= m.start() and m.end() <= b for a, b in labels_keep):
            out.append(t)
        elif t.isascii() and t.isalpha():
            out.append(_case(t, _word(t.lower(), len(t))))
        elif t.isascii() and t.isdigit():
            out.append(_digits(t, len(t)))
        elif not t.isascii():
            n = len(t.encode("utf-8")) if utf8_len else len(t)
            w = _word("u:" + t, n)
            out.append(w[:1].upper() + w[1:])
        else:
            out.append(t)
    return "".join(out)


def pseudo_utf8(b: bytes) -> bytes:
    s = b.decode("utf-8", "surrogateescape")
    r = pseudo_str(s, utf8_len=True).encode("utf-8", "surrogateescape")
    if len(r) != len(b):  # stray invalid bytes: fall back to bytewise letters
        r = bytes((ord(_word("b:" + b.hex(), len(b))[i]) if not (48 <= c <= 57 or c in b" .-_@/\\") else c)
                  for i, c in enumerate(b))
    return r


def pseudo_utf16(b: bytes) -> bytes:
    s = b.decode("utf-16-le", "surrogatepass")
    return pseudo_str(s, utf8_len=False).encode("utf-16-le", "surrogatepass")


def pseudo_latin1(b: bytes) -> bytes:
    s = b.decode("latin-1")
    r = pseudo_str(s, utf8_len=False)
    return r.encode("latin-1", "replace")
