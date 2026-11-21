"""Pass 2: rewrite one file (bytes in, bytes out) and map its path.

Rules
* Every format: length-preserving replacement of known identities in raw bytes
  (UTF-8, UTF-16LE, Latin-1). Offsets never move, so binary CAD formats (OLE
  compound files, Parasolid, JT, STL, images) stay valid.
* STEP / IGES: identity header fields (FILE_NAME author+organization, PERSON,
  ORGANIZATION, IGES author/org) are pseudonymized even when the value is a
  common first name that is too risky to replace globally.
* OLE (SolidWorks <2015, Inventor, Solid Edge): the same for author / last saved
  by / company / manager inside the property-set streams, in place.
* PDF: Info dictionary, XMP, every string object, content and form streams,
  and embedded files (recursively), saved with pikepdf.
* ZIP-based (zip, FCStd, 3MF, F3D...), gzip, tar: unpacked, every member renamed
  and rewritten recursively, repacked with the same compression.
* Paths: identity tokens in every folder and file name are replaced with the
  same pseudonyms as in file contents, so references between files still match.
"""
import io, os, re, zipfile, gzip, tarfile, struct, zlib
from .pseudo import pseudo_str, pseudo_utf8
from .replacer import Replacer
from .identities import is_generic

OLE_SIG = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"


from .harvest import EMAIL, USERPATH, USERPATH16
from .identities import good_email

_GEN_USERS = {"public", "default", "all users", "administrator", "admin", "user", "temp", "default user", "guest"}


def _sub_userpaths(data):
    def u8(m):
        name = m.group(1)
        if name.decode("latin-1").lower() in _GEN_USERS:
            return m.group(0)
        return m.group(0)[:m.start(1) - m.start(0)] + pseudo_utf8(name)
    def u16(m):
        name = m.group(1)
        s = name.decode("utf-16-le", "ignore")
        if s.lower() in _GEN_USERS:
            return m.group(0)
        from .pseudo import pseudo_utf16
        return m.group(0)[:m.start(1) - m.start(0)] + pseudo_utf16(name)
    def em(m):
        e = m.group(0)
        try:
            es = e.decode("ascii")
        except Exception:
            return e
        return pseudo_utf8(e) if good_email(es) else e
    if any(k in data for k in (b"Users", b"users", b"USERS", b"/home/", b"Settings", b"settings")):
        data = USERPATH.sub(u8, data)
    if any(k in data for k in (b"s\x00e\x00r\x00s\x00", b"S\x00E\x00R\x00S\x00")):
        data = USERPATH16.sub(u16, data)
    if b"@" in data:
        data = EMAIL.sub(em, data)
    return data


class Ctx:
    def __init__(self, ids):
        self.R = Replacer(ids["global"])
        self.RP = Replacer(ids["global"] + ids["paths"])
        self.fields = set(x.lower() for x in ids["fields"])
        self.owners = set(ids.get("owners", []))
        self.log = []
        _R = self.R
        class _Both:
            def sub(_, data):
                return _sub_userpaths(_R.sub(data))
            def sub_str(_, s):
                return _R.sub_str(s)
            def find(_, d):
                return _R.find(d)
        self.R = _Both()

    # ---------- paths ----------
    def map_segment(self, seg):
        root, ext = os.path.splitext(seg)
        if 0 < len(ext) <= 10 and re.fullmatch(r"\.[A-Za-z0-9_]+", ext):
            return self.RP.sub_str(root) + ext
        return self.RP.sub_str(seg)

    def map_path(self, rel):
        parts = rel.split("/")
        out = []
        for i, seg in enumerate(parts):
            if i == 2 and len(parts) > 3 and parts[0] == "sources" and parts[1] == "github" and "__" in seg:
                owner, rest = seg.split("__", 1)
                seg = pseudo_str(owner, domain_aware=False) + "__" + self.map_segment(rest)
                out.append(seg); continue
            if i == 1 and parts[0] == "archives" and "__" in seg:
                owner, rest = seg.split("__", 1)
                out.append(pseudo_str(owner, domain_aware=False) + "__" + self.map_segment(rest)); continue
            if i == 2 and parts[:2] == ["sources", "gitlab"] and "__" in seg:
                bits = seg.split("__")
                if len(bits) > 1 and bits[1]:
                    bits[1] = pseudo_str(bits[1], domain_aware=False)
                out.append("__".join(self.map_segment(b) if j > 1 else b for j, b in enumerate(bits))); continue
            out.append(self.map_segment(seg))
        return "/".join(out)

    def map_inner(self, name):
        return "/".join(self.map_segment(s) for s in name.split("/"))

    # ---------- values ----------
    def field_value(self, v):
        """Pseudonym for a whole identity field value (keeps generic placeholders)."""
        if not v or is_generic(v.split("\x00")[0].strip()):
            return v
        return pseudo_str(v)

    # ---------- dispatch ----------
    def process(self, name, data, depth=0):
        try:
            return self._process(name, data, depth)
        except Exception as e:  # never lose a file: fall back to raw replacement
            self.log.append(f"fallback {name}: {type(e).__name__}: {e}")
            return self.R.sub(data)

    def _process(self, name, data, depth):
        ext = os.path.splitext(name)[1].lower()
        if len(data) == 0:
            return data
        if data[:4] == b"PK\x03\x04" and depth < 5:
            out = self.zip(data, depth)
            if out is not None:
                return out
        if data[:2] == b"\x1f\x8b" and depth < 5:
            return self.gz(name, data, depth)
        if len(data) > 262 and data[257:262] == b"ustar" and depth < 5:
            return self.tar(data, depth)
        if data[:5] == b"%PDF-" or (ext == ".pdf" and b"%PDF-" in data[:1024]):
            return self.pdf(data, depth)
        if ext == ".stl" and len(data) >= 84:
            n = struct.unpack("<I", data[80:84])[0]
            if 84 + 50 * n == len(data):
                return self.R.sub(data[:80]) + data[80:]
        if b"ISO-10303-21" in data[:200]:
            return self.step(data)
        if data[:8] == OLE_SIG:
            return self.ole(data)
        if ext in (".igs", ".iges") or (len(data) > 80 and data[72:73] == b"S" and data[73:80].strip().isdigit()):
            return self.iges(data)
        out = self.R.sub(data)
        if name.endswith("Document.xml") or name.endswith(".model"):
            out = self.xml_meta(out)
        return out

    # ---------- STEP ----------
    def step(self, data):
        head_end = data.find(b"ENDSEC;")
        if 0 < head_end < 1_000_000:
            head = data[:head_end]
            def fn(m):
                body = m.group(1)
                parts = list(re.finditer(rb"\((.*?)\)", body, re.S))
                if len(parts) < 2:
                    return m.group(0)
                nb = bytearray(body)
                for p in reversed(parts[:2]):
                    seg = p.group(1)
                    newseg = re.sub(rb"'((?:[^']|'')*)'", lambda q: b"'" + self._fv_bytes(q.group(1)) + b"'", seg)
                    nb[p.start(1):p.end(1)] = newseg
                return b"FILE_NAME(" + bytes(nb) + b");"
            head = re.sub(rb"FILE_NAME\s*\((.*?)\)\s*;", fn, head, count=1, flags=re.S)
            data = head + data[head_end:]
        # PERSON('id','last','first',...) and ORGANIZATION('id','name','desc')
        def per(m):
            body = m.group(2)
            idx = [0]
            def q(mm):
                i = idx[0]; idx[0] += 1
                if (m.group(1) == b"PERSON" and i in (0, 1, 2)) or (m.group(1) == b"ORGANIZATION" and i in (0, 1)):
                    return b"'" + self._fv_bytes(mm.group(1)) + b"'"
                return mm.group(0)
            return m.group(0)[:m.start(2) - m.start(0)] + re.sub(rb"'((?:[^']|'')*)'", q, body) + m.group(0)[m.end(2) - m.start(0):]
        if b"PERSON" in data or b"ORGANIZATION" in data:
            data = re.sub(rb"=\s*(PERSON|ORGANIZATION)\s*\((.*?)\)\s*;", per, data, flags=re.S)
        return self.R.sub(data)

    def _fv_bytes(self, raw):
        try:
            s = raw.decode("utf-8")
            enc = "utf-8"
        except UnicodeDecodeError:
            s = raw.decode("latin-1"); enc = "latin-1"
        if "\\X2\\" in s or "\\X\\" in s or "\\S\\" in s:
            # encoded non-ASCII: keep control sequences, pseudonymize ASCII letters around them
            return re.sub(rb"[A-Za-z]{2,}", lambda m: pseudo_utf8(m.group(0)), raw) if not is_generic(s) else raw
        v = s.replace("''", "'")
        nv = self.field_value(v)
        if nv == v:
            return raw
        return nv.replace("'", "''").encode(enc, "replace")

    # ---------- IGES ----------
    def iges(self, data):
        # Global section author/org: Hollerith strings; replace same-length so columns stay valid
        lines = data.split(b"\n")
        gidx = [i for i, l in enumerate(lines[:2000]) if len(l) >= 73 and l[72:73] == b"G"]
        if gidx:
            g = b"".join(lines[i][:72] for i in gidx)
            for m in re.finditer(rb"(\d+)H", g):
                n = int(m.group(1)); st = m.end()
                val = g[st:st + n]
                if len(val) == n and n >= 2 and re.search(rb"[A-Za-z]{2}", val):
                    s = val.decode("latin-1")
                    if not is_generic(s) and s.lower() in self.fields:
                        new = pseudo_utf8(val)
                        g = g[:st] + new + g[st + n:]
            for k, i in enumerate(gidx):
                lines[i] = g[k * 72:(k + 1) * 72] + lines[i][72:]
            data = b"\n".join(lines)
        return self.R.sub(data)

    # ---------- OLE ----------
    def ole(self, data):
        out = self.R.sub(data)
        try:
            import olefile
            o = olefile.OleFileIO(io.BytesIO(out), write_mode=False)
            m = o.get_metadata()
            vals = set()
            for a in ("author", "last_saved_by", "company", "manager"):
                v = getattr(m, a)
                if v:
                    v = (v.decode("cp1252", "replace") if isinstance(v, bytes) else str(v)).split("\x00")[0].strip()
                    if v and not is_generic(v):
                        vals.add(v)
            if vals:
                buf = bytearray(out)
                local = Replacer(list(vals))
                for st in ("\x05SummaryInformation", "\x05DocumentSummaryInformation"):
                    if not o.exists(st):
                        continue
                    sid = o._find(st)
                    entry = o.direntries[sid]
                    raw = o.openstream(st).read()
                    new = local.sub(raw)
                    if new != raw:
                        # write back in place through olefile
                        fh = io.BytesIO(bytes(buf))
                        ow = olefile.OleFileIO(fh, write_mode=True)
                        ow.write_stream(st, new)
                        ow.close()
                        buf = bytearray(fh.getvalue())
                out = bytes(buf)
        except Exception as e:
            self.log.append(f"ole-props: {e}")
        return out

    # ---------- XML metadata (FreeCAD Document.xml, 3MF model) ----------
    def xml_meta(self, data):
        def rep(m):
            v = m.group(2).decode("utf-8", "replace")
            return m.group(1) + self.field_value(v).encode("utf-8") + m.group(3)
        data = re.sub(rb'(<Property name="(?:CreatedBy|LastModifiedBy|Company)"[^>]*>\s*<String value=")([^"]*)(")', rep, data)
        data = re.sub(rb'(<metadata name="(?:Designer|Copyright|Author)"[^>]*>)([^<]*)(<)', rep, data)
        return data

    # ---------- containers ----------
    def zip(self, data, depth):
        try:
            zin = zipfile.ZipFile(io.BytesIO(data))
            infos = zin.infolist()
        except Exception:
            return None
        bio = io.BytesIO()
        changed = False
        with zipfile.ZipFile(bio, "w", allowZip64=True) as zout:
            if zin.comment:
                zout.comment = self.R.sub(zin.comment)
            for zi in infos:
                newname = self.map_inner(zi.filename)
                try:
                    content = zin.read(zi)
                except Exception as e:
                    self.log.append(f"zip kept as-is (member unreadable {zi.filename}: {e})")
                    return data
                newc = content if zi.is_dir() else self.process(zi.filename, content, depth + 1)
                ni = zipfile.ZipInfo(newname, date_time=zi.date_time)
                ni.compress_type = zi.compress_type if zi.compress_type in (0, 8, 12, 14) else 8
                ni.external_attr = zi.external_attr
                ni.create_system = zi.create_system
                ni.comment = self.R.sub(zi.comment) if zi.comment else b""
                if newname != zi.filename or newc != content:
                    changed = True
                zout.writestr(ni, newc)
        return bio.getvalue() if changed else data

    def gz(self, name, data, depth):
        raw = gzip.decompress(data)
        inner = name[:-3] if name.lower().endswith(".gz") else name
        if inner.lower().endswith(".tgz"):
            inner = inner[:-4] + ".tar"
        new = self.process(inner, raw, depth + 1)
        if new == raw:
            return data
        mtime = struct.unpack("<I", data[4:8])[0]
        return gzip.compress(new, compresslevel=6, mtime=mtime)

    def tar(self, data, depth):
        tin = tarfile.open(fileobj=io.BytesIO(data))
        bio = io.BytesIO()
        with tarfile.open(fileobj=bio, mode="w", format=tin.format or tarfile.GNU_FORMAT) as tout:
            for ti in tin.getmembers():
                nt = tarfile.TarInfo(self.map_inner(ti.name))
                for a in ("mode", "mtime", "type", "uid", "gid", "devmajor", "devminor"):
                    setattr(nt, a, getattr(ti, a))
                nt.linkname = self.map_inner(ti.linkname) if ti.linkname else ""
                nt.uname = self.field_value(ti.uname) if ti.uname else ""
                nt.gname = self.field_value(ti.gname) if ti.gname else ""
                if ti.isfile():
                    c = tin.extractfile(ti).read()
                    c = self.process(ti.name, c, depth + 1)
                    nt.size = len(c)
                    tout.addfile(nt, io.BytesIO(c))
                else:
                    tout.addfile(nt)
        return bio.getvalue()

    # ---------- PDF ----------
    def pdf(self, data, depth):
        import pikepdf
        try:
            pdf = pikepdf.open(io.BytesIO(data))
        except Exception as e:
            self.log.append(f"pdf unreadable, raw replace: {e}")
            return self.R.sub(data)
        changed = False
        with pdf:
            # Info dictionary
            try:
                info = pdf.trailer.get("/Info")
                if info is not None:
                    for k in list(info.keys()):
                        v = info[k]
                        if isinstance(v, pikepdf.String):
                            s = str(v)
                            if k in ("/Author", "/Company", "/Manager", "/LastModifiedBy", "/Owner"):
                                ns = self.field_value(self.R.sub_str(s)) if self.R.sub_str(s) == s else self.R.sub_str(s)
                            else:
                                ns = self.R.sub_str(s)
                            if ns != s:
                                info[k] = pikepdf.String(ns); changed = True
            except Exception as e:
                self.log.append(f"pdf info: {e}")
            for obj in pdf.objects:
                try:
                    if isinstance(obj, pikepdf.Dictionary) or isinstance(obj, pikepdf.Stream):
                        if self._pdf_dict_strings(obj, pikepdf):
                            changed = True
                    if isinstance(obj, pikepdf.Stream):
                        t = obj.get("/Type"); st = obj.get("/Subtype")
                        if st == "/Image" or "/Length1" in obj or "/Length2" in obj or st in ("/Type1C", "/CIDFontType0C", "/OpenType"):
                            continue
                        if t == "/EmbeddedFile":
                            raw = obj.read_bytes()
                            new = self.process("embedded.bin", raw, depth + 1)
                            if new != raw:
                                obj.write(new); changed = True
                            continue
                        if t == "/Metadata":
                            raw = obj.read_bytes()
                            new = self.R.sub(raw)
                            new = re.sub(rb"(<dc:creator>\s*<rdf:Seq>\s*<rdf:li[^>]*>)([^<]*)(<)",
                                         lambda m: m.group(1) + self.field_value(m.group(2).decode("utf-8", "replace")).encode("utf-8") + m.group(3), new)
                            new = re.sub(rb"(<pdf:Author>|pdf:Author=\")([^<\"]*)",
                                         lambda m: m.group(1) + self.field_value(m.group(2).decode("utf-8", "replace")).encode("utf-8"), new)
                            if new != raw:
                                obj.write(new); changed = True
                            continue
                        try:
                            raw = obj.read_bytes()
                        except Exception:
                            continue
                        if len(raw) > 200_000_000:
                            continue
                        new = self.R.sub(raw)
                        if new != raw:
                            obj.write(new); changed = True
                except Exception as e:
                    self.log.append(f"pdf obj: {e}")
            if not changed:
                return self.R.sub(data)
            bio = io.BytesIO()
            pdf.save(bio, compress_streams=True, object_stream_mode=pikepdf.ObjectStreamMode.preserve)
            return self.R.sub(bio.getvalue())

    def _pdf_nested(self, v, pikepdf, depth=0):
        changed = False
        if depth > 6:
            return False
        if isinstance(v, pikepdf.Array):
            for i in range(len(v)):
                x = v[i]
                if isinstance(x, pikepdf.String):
                    try:
                        s = str(x)
                    except Exception:
                        continue
                    ns = self.R.sub_str(s)
                    if ns != s:
                        v[i] = pikepdf.String(ns); changed = True
                elif isinstance(x, (pikepdf.Dictionary, pikepdf.Array)) and not x.is_indirect:
                    changed |= self._pdf_nested(x, pikepdf, depth + 1)
        elif isinstance(v, pikepdf.Dictionary):
            changed |= self._pdf_dict_strings(v, pikepdf, depth + 1)
        return changed

    def _pdf_dict_strings(self, d, pikepdf, depth=0):
        changed = False
        for k in list(d.keys()):
            try:
                vv = d[k]
            except Exception:
                continue
            if isinstance(vv, (pikepdf.Dictionary, pikepdf.Array)) and not vv.is_indirect and depth < 6:
                changed |= self._pdf_nested(vv, pikepdf, depth)
            try:
                v = d[k]
            except Exception:
                continue
            if isinstance(v, pikepdf.String):
                try:
                    s = str(v)
                except Exception:
                    continue
                ns = self.R.sub_str(s)
                if k in ("/T", "/Author") and ns == s and d.get("/Type") != "/Font":
                    ns = self.field_value(s) if s.lower() in self.fields or not is_generic(s) and k == "/Author" else ns
                if ns != s:
                    d[k] = pikepdf.String(ns); changed = True
        return changed
