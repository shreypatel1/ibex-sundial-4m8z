"""Process a local input tree into OUT/corpus with the same relative layout (paths mapped)."""
import json, os, sys, time, multiprocessing as mp
from .process import Ctx

CTX = None


def init(idsfile):
    global CTX
    CTX = Ctx(json.load(open(idsfile, encoding="utf-8")))


def work(item):
    src_root, rel, out_root = item
    t = time.time()
    src = os.path.join(src_root, rel)
    newrel = CTX.map_path(rel)
    dst = os.path.join(out_root, newrel)
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    with open(src, "rb") as fh:
        data = fh.read()
    CTX.log.clear()
    new = CTX.process(rel, data)
    with open(dst, "wb") as fh:
        fh.write(new)
    return rel, newrel, len(data), new != data, list(CTX.log), time.time() - t


def main(idsfile, src_root, out_root, rels, procs=4, report=None):
    items = [(src_root, r, out_root) for r in rels]
    items.sort(key=lambda x: -os.path.getsize(os.path.join(x[0], x[1])))
    rep = open(report, "a", encoding="utf-8") if report else None
    n = 0
    with mp.Pool(procs, initializer=init, initargs=(idsfile,), maxtasksperchild=200) as pool:
        for rel, newrel, size, changed, log, dt in pool.imap_unordered(work, items, chunksize=1):
            n += 1
            if rep:
                rep.write(json.dumps({"src": rel, "dst": newrel, "size": size, "changed": changed, "log": log, "t": round(dt, 2)}, ensure_ascii=False) + "\n")
    if rep:
        rep.close()
    return n


if __name__ == "__main__":
    idsfile, src_root, out_root = sys.argv[1:4]
    rels = []
    for d, _, fs in os.walk(src_root):
        for f in fs:
            rels.append(os.path.relpath(os.path.join(d, f), src_root))
    t = time.time()
    n = main(idsfile, src_root, out_root, rels, report=sys.argv[4] if len(sys.argv) > 4 else None)
    print(n, "files", round(time.time() - t, 1), "s")
