"""Compare input vs output: structure checks per format + leak scan of global identities."""
import io, json, os, re, sys, zipfile, collections, warnings
warnings.filterwarnings("ignore")
import logging; logging.disable(logging.WARNING)
from .replacer import Replacer


def chk(src, dst, data_in, data_out):
    e = os.path.splitext(src)[1].lower()
    if data_in[:4] == b"PK\x03\x04":
        try:
            zi, zo = zipfile.ZipFile(io.BytesIO(data_in)), zipfile.ZipFile(io.BytesIO(data_out))
            if len(zi.infolist()) != len(zo.infolist()):
                return "zip member count"
            if zo.testzip() is not None and zi.testzip() is None:
                return "zip crc"
        except zipfile.BadZipFile:
            pass
        except NotImplementedError:
            pass
        return None
    if data_in[:5] == b"%PDF-":
        import pikepdf
        try:
            a = len(pikepdf.open(io.BytesIO(data_in)).pages)
        except Exception:
            return None
        try:
            b = len(pikepdf.open(io.BytesIO(data_out)).pages)
        except Exception as ex:
            return f"pdf open {ex}"
        return None if a == b else "pdf pages"
    if b"ISO-10303-21" in data_in[:200]:
        a = len(re.findall(rb"\n\s*#\d+\s*=", data_in)); b = len(re.findall(rb"\n\s*#\d+\s*=", data_out))
        return None if a == b else f"step entities {a}->{b}"
    if data_in[:2] == b"\x1f\x8b" or data_in[257:262] == b"ustar":
        return None
    if len(data_in) != len(data_out):
        return f"size {len(data_in)}->{len(data_out)}"
    if data_in[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1":
        import olefile
        try:
            if sorted(olefile.OleFileIO(io.BytesIO(data_in)).listdir()) != sorted(olefile.OleFileIO(io.BytesIO(data_out)).listdir()):
                return "ole streams"
        except Exception as ex:
            return f"ole {ex}"
    return None


if __name__ == "__main__":
    ids, src_root, out_root, rep = sys.argv[1:5]
    g = json.load(open(ids))["global"]
    R = Replacer(g)
    probs = collections.Counter(); ex = {}
    leaks = collections.Counter(); leakf = {}
    pathleaks = 0
    for l in open(rep):
        r = json.loads(l)
        a = open(os.path.join(src_root, r["src"]), "rb").read()
        b = open(os.path.join(out_root, r["dst"]), "rb").read()
        p = chk(r["src"], r["dst"], a, b)
        if p:
            probs[p.split(" ")[0] + " " + p.split(" ")[1] if " " in p else p] += 1; ex[p] = r["src"]
        if not (b[:4] == b"PK\x03\x04" or b[:2] == b"\x1f\x8b"):
            for st, ln, enc in R.find(b):
                if enc == "u8":
                    leaks[b[st:st+ln]] += 1; leakf[b[st:st+ln]] = r["dst"]
        if R.find(r["dst"].encode()):
            pathleaks += 1
    print("problems", probs.most_common(20))
    for k, v in list(ex.items())[:15]: print("  ", k, v)
    print("content leaks", sum(leaks.values()), leaks.most_common(30))
    for k, v in leaks.most_common(10): print("  ", k, leakf[k])
    print("path leaks", pathleaks)
