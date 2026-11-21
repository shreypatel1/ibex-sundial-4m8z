"""Pass 1: find identity strings (people, companies, accounts, emails, domains).

Identity sources:
  * paths: GitHub/GitLab owners, web domains, user-home folders
  * native metadata: STEP FILE_NAME author/organization, STEP PERSON and
    ORGANIZATION entities, IGES global author/organization, OLE (SolidWorks,
    Inventor, Solid Edge) SummaryInformation, PDF Info and XMP, FreeCAD
    Document.xml, 3MF metadata, image EXIF
  * free text anywhere: e-mail addresses and C:\\Users\\<name> style paths
Output: JSON lines {"v": value, "k": kind, "f": example path}.
"""
import io, os, re, zipfile, gzip, tarfile, json, sys

EMAIL = re.compile(rb"[A-Za-z0-9][A-Za-z0-9._%+-]{0,63}@[A-Za-z0-9-]{1,63}(?:\.[A-Za-z0-9-]{1,63})*\.[A-Za-z]{2,10}")
USERPATH = re.compile(rb"(?i)(?:[a-z]:[\\/]+(?:users|documents and settings)[\\/]+|/(?:home|users)/)([A-Za-z0-9._ -]{2,40}?)(?=[\\/])")
USERPATH16 = re.compile(rb"(?i)(?:[a-z]\x00:\x00[\\/]\x00(?:u\x00s\x00e\x00r\x00s\x00)[\\/]\x00)((?:[A-Za-z0-9._ -]\x00){2,40}?)(?=[\\/]\x00)")
STEP_FN = re.compile(rb"FILE_NAME\s*\((.*?)\)\s*;", re.S)
STEP_PERSON = re.compile(rb"=\s*PERSON\s*\((.*?)\)\s*;", re.S)
STEP_ORG = re.compile(rb"=\s*ORGANIZATION\s*\((.*?)\)\s*;", re.S)
QSTR = re.compile(rb"'((?:[^']|'')*)'")


def _dec(b):
    for enc in ("utf-8", "cp1252", "latin-1"):
        try:
            return b.decode(enc)
        except UnicodeDecodeError:
            pass
    return b.decode("latin-1")


def step_unescape(s):
    s = s.replace("''", "'")
    def x2(m):
        h = m.group(1)
        try:
            return bytes.fromhex(h).decode("utf-16-be")
        except Exception:
            return m.group(0)
    s = re.sub(r"\\X2\\([0-9A-Fa-f]+)\\X0\\", x2, s)
    return s


def harvest_step(data, emit):
    m = STEP_FN.search(data[:200000])
    if m:
        body = m.group(1)
        # FILE_NAME(name, time_stamp, (author...), (organization...), preproc, orig_sys, authorization)
        parts = re.findall(rb"\((.*?)\)", body, re.S)
        if len(parts) >= 2:
            for v in QSTR.findall(parts[0]):
                emit(step_unescape(_dec(v)), "step_author")
            for v in QSTR.findall(parts[1]):
                emit(step_unescape(_dec(v)), "step_org")
        q = QSTR.findall(body)
        if len(q) >= 7:
            emit(step_unescape(_dec(q[-1])), "step_authorization")
    for m in STEP_PERSON.finditer(data):
        q = [step_unescape(_dec(v)) for v in QSTR.findall(m.group(1))]
        if len(q) >= 3:
            emit(q[0], "step_person_id")
            full = " ".join(x for x in (q[2], q[1]) if x and x != "$")
            emit(full, "step_person")
            emit(q[1], "step_person_last")
            emit(q[2], "step_person_first")
    for m in STEP_ORG.finditer(data):
        q = [step_unescape(_dec(v)) for v in QSTR.findall(m.group(1))]
        for v in q[:2]:
            emit(v, "step_org")


def harvest_iges(data, emit):
    lines = data[:400000].split(b"\n")
    g = b"".join(l[:72] for l in lines if len(l) >= 73 and l[72:73] == b"G")
    if not g:
        return
    try:
        pd = chr(g[2]) if g[:2] == b"1H" else ","
        rd = ";"
        fields, i = [], 0
        s = g.decode("latin-1")
        while i < len(s) and len(fields) < 26:
            m = re.match(r"\s*(\d+)H", s[i:])
            if m:
                n = int(m.group(1)); st = i + m.end()
                fields.append(s[st:st + n]); i = st + n
                j = i
                while j < len(s) and s[j] not in (pd, rd):
                    j += 1
                i = j + 1
            else:
                j = i
                while j < len(s) and s[j] not in (pd, rd):
                    j += 1
                fields.append(s[i:j].strip()); i = j + 1
        if len(fields) >= 22:
            emit(fields[20], "iges_author")
            emit(fields[21], "iges_org")
    except Exception:
        pass


def harvest_ole(data, emit):
    import olefile
    try:
        o = olefile.OleFileIO(io.BytesIO(data))
        m = o.get_metadata()
        for a, k in (("author", "ole_author"), ("last_saved_by", "ole_lastsaved"),
                     ("company", "ole_company"), ("manager", "ole_manager")):
            v = getattr(m, a)
            if v:
                emit(_dec(v) if isinstance(v, bytes) else str(v), k)
    except Exception:
        pass


def harvest_pdf(data, emit):
    try:
        import pikepdf
        with pikepdf.open(io.BytesIO(data)) as pdf:
            for k in ("/Author", "/Company", "/Manager", "/LastModifiedBy", "/Owner"):
                if k in pdf.docinfo:
                    emit(str(pdf.docinfo[k]), "pdf_" + k[1:].lower())
            try:
                meta = pdf.Root.get("/Metadata")
                if meta is not None:
                    x = meta.read_bytes()
                    for tag in (rb"dc:creator", rb"pdf:Author", rb"xmp:Author", rb"photoshop:AuthorsPosition"):
                        for mm in re.finditer(rb"<" + tag + rb"[^>]*>(.*?)</" + tag + rb">", x, re.S):
                            for li in re.findall(rb"<rdf:li[^>]*>(.*?)</rdf:li>", mm.group(1), re.S) or [mm.group(1)]:
                                emit(_dec(li).strip(), "xmp_creator")
            except Exception:
                pass
    except Exception:
        pass


def harvest_xml_meta(data, emit, kind):
    t = data.decode("utf-8", "ignore")
    for m in re.finditer(r'<Property name="(CreatedBy|LastModifiedBy|Company)"[^>]*>\s*<String value="([^"]*)"', t):
        emit(m.group(2), "fcstd_" + m.group(1).lower())
    for m in re.finditer(r'<metadata name="(Designer|Copyright|LicenseTerms|Author|Rating|Application)"[^>]*>([^<]*)<', t):
        if m.group(1) in ("Designer", "Copyright", "Author"):
            emit(m.group(2), "3mf_" + m.group(1).lower())


def harvest_free(data, emit):
    for m in EMAIL.finditer(data):
        emit(_dec(m.group(0)), "email")
    for m in USERPATH.finditer(data):
        emit(_dec(m.group(1)), "userpath")
    for m in USERPATH16.finditer(data):
        emit(m.group(1).decode("utf-16-le", "ignore"), "userpath")


ZIPLIKE = {".zip", ".fcstd", ".3mf", ".f3d", ".f3z", ".docx", ".xlsx", ".pptx", ".odt", ".ods"}
OLE_SIG = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"


def harvest_bytes(name, data, emit, depth=0):
    ext = os.path.splitext(name)[1].lower()
    if data[:4] == b"PK\x03\x04" and depth < 4:
        try:
            z = zipfile.ZipFile(io.BytesIO(data))
            for zi in z.infolist():
                emit_path(zi.filename, emit)
                if zi.file_size < 300_000_000 and not zi.is_dir():
                    try:
                        harvest_bytes(zi.filename, z.read(zi), emit, depth + 1)
                    except Exception:
                        pass
            return
        except Exception:
            pass
    if data[:2] == b"\x1f\x8b" and depth < 4:
        try:
            raw = gzip.decompress(data)
            inner = name[:-3] if name.lower().endswith(".gz") else name + ".raw"
            if raw[257:262] == b"ustar":
                with tarfile.open(fileobj=io.BytesIO(raw)) as tf:
                    for ti in tf.getmembers():
                        emit_path(ti.name, emit)
                        if ti.uname: emit(ti.uname, "tar_uname")
                        if ti.isfile() and ti.size < 300_000_000:
                            harvest_bytes(ti.name, tf.extractfile(ti).read(), emit, depth + 1)
            else:
                harvest_bytes(inner, raw, emit, depth + 1)
            return
        except Exception:
            pass
    if data[257:262] == b"ustar" and depth < 4:
        try:
            with tarfile.open(fileobj=io.BytesIO(data)) as tf:
                for ti in tf.getmembers():
                    emit_path(ti.name, emit)
                    if ti.uname: emit(ti.uname, "tar_uname")
                    if ti.isfile() and ti.size < 300_000_000:
                        harvest_bytes(ti.name, tf.extractfile(ti).read(), emit, depth + 1)
            return
        except Exception:
            pass
    if b"ISO-10303-21" in data[:200]:
        harvest_step(data, emit)
    elif ext in (".igs", ".iges") or data[72:73] == b"S":
        harvest_iges(data, emit)
    elif data[:8] == OLE_SIG:
        harvest_ole(data, emit)
    elif data[:5] == b"%PDF-" or ext == ".pdf":
        harvest_pdf(data, emit)
    if name.endswith("Document.xml") or name.endswith(".model") or name.endswith("GuiDocument.xml"):
        harvest_xml_meta(data, emit, ext)
    if ext == ".stl" and data[:5].lower() != b"solid":
        harvest_free(data[:80], emit)
    elif len(data) > 64_000_000:
        harvest_free(data[:8_000_000], emit); harvest_free(data[-8_000_000:], emit)
    else:
        harvest_free(data, emit)


def emit_path(path, emit):
    for m in USERPATH.finditer(path.encode("utf-8", "ignore") + b"/"):
        emit(_dec(m.group(1)), "userpath")


def path_identities(path, emit):
    p = path.split("/")
    if p[0] == "sources" and len(p) > 2:
        if p[1] in ("github",):
            owner = p[2].split("__")[0]
            emit(owner, "gh_owner")
        elif p[1] == "gitlab":
            # gitlab.com__owner____repo
            seg = p[2].split("__")
            if len(seg) > 1:
                emit(seg[1], "gl_owner")
        elif p[1] == "web":
            emit(p[2], "web_domain")
    elif p[0] == "archives" and len(p) > 1:
        emit(p[1].split("__")[0], "gh_owner")
    emit_path(path, emit)


if __name__ == "__main__":
    # usage: harvest.py ROOT [ROOT...] > out.jsonl   (paths relative to ROOT)
    import multiprocessing as mp
    files = []
    for root in sys.argv[1:]:
        for d, _, fs in os.walk(root):
            for f in fs:
                full = os.path.join(d, f)
                files.append((root, full))

    def work(item):
        root, full = item
        rel = os.path.relpath(full, root)
        seen = []
        def emit(v, k):
            if v and v.strip():
                seen.append((v.strip(), k))
        try:
            if os.path.getsize(full) < 1_500_000_000:
                with open(full, "rb") as fh:
                    data = fh.read()
                harvest_bytes(rel, data, emit)
        except Exception as e:
            print("ERR", rel, e, file=sys.stderr)
        return rel, sorted(set(seen))

    with mp.Pool(4) as pool:
        for rel, seen in pool.imap_unordered(work, files, chunksize=8):
            for v, k in seen:
                print(json.dumps({"v": v, "k": k, "f": rel}, ensure_ascii=False))
