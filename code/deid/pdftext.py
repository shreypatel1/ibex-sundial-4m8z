"""Visible-text de-identification for PDFs (PyMuPDF redaction + same-place pseudonym).

Finds identifiers in page text: known identities (harvest), e-mails, web addresses,
phone numbers, person names (first-name dictionary + capitalised surname, or
initial + surname, or values in drawing title-block roles), and organisation names
(... Inc/LLC/GmbH/Ltd/University/Lab/Institute). Each hit is removed from the page
content and the pseudonym of exactly that text is written in the same box, so the
same identity reads the same in every PDF. Vector graphics and images are untouched.
"""
import re
import fitz
from .pseudo import pseudo_str

try:
    from wordfreq import zipf_frequency
except Exception:
    def zipf_frequency(w, l):
        return 0.0
try:
    import gender_guesser.detector as _gd
    _FIRST = {k.lower() for k in _gd.Detector(case_sensitive=False).names.keys()}
except Exception:
    _FIRST = set()

EMAIL = re.compile(r"[A-Za-z0-9][A-Za-z0-9._%+-]{0,63}@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,10}")
URL = re.compile(r"(?i)\b(?:https?://)?(?:www\.)?[a-z0-9][a-z0-9-]{1,62}(?:\.[a-z0-9-]{1,62})*\.(?:com|org|net|edu|gov|io|de|uk|fr|it|es|nl|ch|at|eu|cn|jp|co|us|ca|au|info|biz)\b(?:/[^\s)]*)?")
PHONE = re.compile(r"(?<![\w.+/])(?:\+\d{1,3}[ .-]?(?:\(0\)[ ]?)?\d{1,4}(?:[ .-]\d{2,4}){2,4}|\(?\d{3}\)?[ .-]\d{3}[ .-]\d{4})(?![\w./])")
ORG = re.compile(r"\b(?:[A-Z][A-Za-z0-9&'.-]*[ ]+){0,4}(?:Inc\.?|LLC|L\.L\.C\.|Ltd\.?|Limited|GmbH|Corp\.?|Corporation|AG|S\.A\.|S\.L\.|B\.V\.|Pty\.? Ltd\.?|Co\.,?\s?Ltd\.?|S\.r\.l\.|SRL|SAS|AB|Oy|A/S|KG|e\.V\.)(?![A-Za-z])")
UNI = re.compile(r"\b(?:University|Universit[äa]t|Universidad|Universit[ée]|Universit[àa]|College|Institute|Institut|Laboratory|Laboratories)\s+(?:of\s+|for\s+|de\s+|der\s+)?(?:[A-Z][A-Za-z.-]+)(?:\s+[A-Z][A-Za-z.-]+){0,3}"
                 r"|\b(?:[A-Z][A-Za-z.-]+\s+){1,3}(?:University|Universit[äa]t|Institute|Laboratory|Lab|College|Polytechnic)\b")
UNI_CAPS = re.compile(r"\b(?:[A-Z]{2,}[ ]+){0,3}(?:UNIVERSITY|UNIVERSITAT|INSTITUTE|LABORATORY|LABORATORIES|COLLEGE)(?:[ ]+OF(?:[ ]+[A-Z]{2,}){1,3})?\b")
INIT_SUR = re.compile(r"\b([A-Z])\.[ ]?([A-Z][A-Za-z'-]{2,}|[A-Z]{3,})\b")
FIRST_LAST = re.compile(r"\b([A-Z][a-z]{1,15}|[A-Z]{2,15})[ ]+(?:[A-Z]\.[ ]+)?([A-Z][a-z'-]{1,20}|[A-Z]{2,20})(?:[ ]+([A-Z][a-z'-]{2,20}))?\b")
ROLE = re.compile(r"(?i)\b(?:drawn|drn|dwn|drafter|draftsman|designed|designer|dsgn|checked|checker|chk'?d|approved|appv'?d|appr|engineer|eng|author|created by|prepared by|by)[ \t]*[:.]?[ \t]+([A-Z][A-Za-z.'-]{1,20}(?:[ ][A-Z][A-Za-z.'-]{1,20})?)")
COPY = re.compile(r"(?:©|\(c\)|\(C\)|Copyright)\s*(?:\d{4}(?:\s*[-–]\s*\d{4})?,?\s*)?((?:[A-Z][\w&.,'-]*\s?){1,5})")

ENG_WORDS = {"detail", "section", "view", "scale", "sheet", "material", "finish", "date", "rev", "revision",
             "title", "size", "weight", "drawn", "checked", "approved", "name", "signature", "tolerance",
             "tolerances", "unless", "otherwise", "specified", "dimensions", "assy", "assembly", "part",
             "qty", "item", "steel", "aluminum", "aluminium", "brass", "nylon", "plastic", "thread", "thru",
             "typ", "max", "min", "ref", "dia", "radius", "angle", "linear", "angular", "surface", "break",
             "sharp", "edges", "deburr", "projection", "third", "first", "angle", "notes", "note", "see",
             "dwg", "no", "number", "description", "drawing", "do", "not", "mfg", "q.a", "qa", "next",
             "used", "on", "sub", "system", "project", "model", "hole", "holes", "places", "top", "bottom",
             "left", "right", "front", "back", "side", "iso", "din", "ansi", "asme", "unc", "unf", "metric",
             "stainless", "black", "white", "red", "blue", "green", "anodize", "anodized", "general"}


def _common(w):
    lw = w.lower().strip(".")
    if lw in ENG_WORDS:
        return True
    z = zipf_frequency(lw, "en")
    return z >= 4.2 or (z >= 3.3 and lw not in _FIRST and not _surnameish(lw))


def _surnameish(lw):
    return bool(re.search(r"(ez|ov|ova|sky|ski|son|sen|man|mann|berg|stein|ini|elli|etti|ian|yan|oglu|ić|ic|uk|enko|ner|er)$", lw)) and len(lw) >= 5


def candidates(text, R):
    """Return list of (start, end) spans of identifiers in page text."""
    spans = []
    b = text.encode("utf-8", "surrogatepass")
    # known identities
    for st, ln, enc in R.find(b):
        if enc != "u8":
            continue
        s = len(b[:st].decode("utf-8", "ignore")); e = s + len(b[st:st + ln].decode("utf-8", "ignore"))
        spans.append((s, e))
    for rx in (EMAIL, URL):
        for m in rx.finditer(text):
            spans.append(m.span())
    for m in PHONE.finditer(text):
        digits = re.sub(r"\D", "", m.group(0))
        if 9 <= len(digits) <= 15 and not re.fullmatch(r"(19|20)\d{2}[\s.-]\d{1,2}[\s.-]\d{1,2}", m.group(0)):
            spans.append(m.span())
    for m in ORG.finditer(text):
        g = m.group(0).strip()
        if len(g.split()) >= 2:
            spans.append((m.start(), m.start() + len(g)))
    for m in UNI.finditer(text):
        spans.append(m.span())
    for m in UNI_CAPS.finditer(text):
        if len(m.group(0).split()) >= 2:
            spans.append(m.span())
    for m in COPY.finditer(text):
        g = m.group(1).strip(" ,.")
        if g and not _common(g.split()[0]):
            st = m.start(1); spans.append((st, st + len(g)))
    for m in INIT_SUR.finditer(text):
        if not _common(m.group(2)):
            spans.append(m.span())
    for m in FIRST_LAST.finditer(text):
        f, l = m.group(1), m.group(2)
        if f.lower() in _FIRST and not _common(l) and (not _common(f) or zipf_frequency(l.lower(), "en") < 2.5) \
                and l.lower() not in ENG_WORDS and len(l) >= 2:
            spans.append(m.span())
    for m in ROLE.finditer(text):
        v = m.group(1)
        if not _common(v.split()[0]) and not re.search(r"\d", v):
            spans.append(m.span(1))
    # merge overlaps
    spans.sort()
    out = []
    for s, e in spans:
        if out and s <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], e))
        else:
            out.append((s, e))
    return out


def _fit(text, rect, fontname="helv"):
    fs = max(3.0, rect.height * 0.78)
    w = fitz.get_text_length(text, fontname=fontname, fontsize=fs)
    if w > rect.width * 0.98 and w > 0:
        fs = max(2.0, fs * rect.width * 0.98 / w)
    return fs


def redact_pdf(data, R, log=None, max_pages=400):
    """Return new PDF bytes or None if nothing changed."""
    try:
        doc = fitz.open(stream=data, filetype="pdf")
    except Exception:
        return None
    if doc.needs_pass or doc.is_encrypted:
        return None
    changed = False
    for pno in range(min(doc.page_count, max_pages)):
        page = doc[pno]
        try:
            text = page.get_text("text")
        except Exception:
            continue
        if not text.strip():
            continue
        found = set()
        for s, e in candidates(text, R):
            frag = text[s:e].strip()
            if "\n" in frag:
                found.update(x.strip() for x in frag.split("\n") if len(x.strip()) >= 3)
            elif len(frag) >= 3:
                found.add(frag)
        if not found:
            continue
        n = 0
        todo = []
        for frag in sorted(found, key=len, reverse=True):
            try:
                quads = page.search_for(frag, quads=False)
            except Exception:
                continue
            rep = pseudo_str(frag)
            for r in quads:
                if r.is_empty or r.width < 1:
                    continue
                page.add_redact_annot(r, fill=False, cross_out=False)
                todo.append((r, rep))
                n += 1
        if n:
            try:
                page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE, graphics=fitz.PDF_REDACT_LINE_ART_NONE,
                                      text=fitz.PDF_REDACT_TEXT_REMOVE)
                changed = True
                for r, rep in todo:
                    vertical = r.height > r.width * 1.5 and len(rep) > 2
                    if vertical:
                        fs = max(2.0, min(r.width * 0.85, r.height / max(1, fitz.get_text_length(rep, "helv", 1))))
                        page.insert_text(fitz.Point(r.x1 - r.width * 0.2, r.y1), rep, fontsize=fs, fontname="helv", rotate=90)
                    else:
                        fs = max(2.0, min(r.height * 0.85, r.width / max(0.01, fitz.get_text_length(rep, "helv", 1))))
                        page.insert_text(fitz.Point(r.x0, r.y1 - r.height * 0.18), rep, fontsize=fs, fontname="helv")
            except Exception as ex:
                if log is not None:
                    log.append(f"redact p{pno}: {ex}")
    if not changed:
        return None
    try:
        return doc.tobytes(garbage=1, deflate=True)
    except Exception as ex:
        if log is not None:
            log.append(f"pdf save: {ex}")
        return None
