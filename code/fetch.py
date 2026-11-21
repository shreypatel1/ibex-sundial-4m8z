import sys,os,urllib.request,urllib.parse,concurrent.futures as cf
BASE="https://machina-input.joinbuilders.org/"
tsv,dest=sys.argv[1],sys.argv[2]
rows=[l.rstrip('\n').split('\t') for l in open(tsv) if not l.startswith('#')]
def get(r):
    p,b,_,u=r; out=os.path.join(dest,p)
    if os.path.exists(out) and os.path.getsize(out)==int(b): return 0
    os.makedirs(os.path.dirname(out),exist_ok=True)
    url=BASE+u
    for i in range(5):
        try:
            with urllib.request.urlopen(url,timeout=120) as resp, open(out,'wb') as f:
                while True:
                    c=resp.read(1<<20)
                    if not c: break
                    f.write(c)
            return 0
        except Exception as e: err=e
    print("FAIL",p,err,flush=True); return 1
with cf.ThreadPoolExecutor(int(sys.argv[3]) if len(sys.argv)>3 else 32) as ex:
    print(sum(ex.map(get,rows)),"failures")
