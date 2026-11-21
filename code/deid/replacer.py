"""Length-preserving, case-insensitive multi-pattern replacement on raw bytes.

Patterns are identity strings in UTF-8, UTF-16LE and Latin-1. Matching runs on an
ASCII-lowercased copy of the data with Aho-Corasick, needs a non-alphanumeric
neighbour on both sides, and replaces each hit with the pseudonym of the exact
text found (case pattern kept), so file offsets and lengths never change.
"""
import ahocorasick
from .pseudo import pseudo_utf8, pseudo_utf16, pseudo_latin1, pseudo_str

ALNUM = set(b"abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789")


class Replacer:
    def __init__(self, identities):
        self.A = ahocorasick.Automaton()
        n = 0
        for s in set(identities):
            if not s:
                continue
            low = s.lower()
            forms = {("u8", low.encode("utf-8"))}
            forms.add(("u16", low.encode("utf-16-le")))
            try:
                l1 = low.encode("latin-1")
                if l1 != low.encode("utf-8"):
                    forms.add(("l1", l1))
            except UnicodeEncodeError:
                pass
            for enc, b in forms:
                if len(b) < 3:
                    continue
                key = b.decode("latin-1")
                if key not in self.A:
                    self.A.add_word(key, (enc, len(b)))
                    n += 1
        self.n = n
        if n:
            self.A.make_automaton()
        self.cache = {}
        self.hits = {}

    def _ok16(self, data, i):
        if i < 0 or i + 1 >= len(data):
            return True
        return not (data[i] in ALNUM and data[i + 1] == 0)

    def find(self, data: bytes):
        if not self.n:
            return []
        low = data.lower().decode("latin-1")
        cands = []
        for end, (enc, ln) in self.A.iter(low):
            st = end - ln + 1
            if enc == "u16":
                if st % 2 and False:
                    pass
                if not (self._ok16(data, st - 2) and self._ok16(data, end + 1)):
                    continue
            else:
                if st > 0 and data[st - 1] in ALNUM:
                    continue
                if end + 1 < len(data) and data[end + 1] in ALNUM:
                    continue
            cands.append((st, -ln, enc))
        cands.sort()
        out, last = [], -1
        for st, nl, enc in cands:
            if st < last:
                continue
            out.append((st, -nl, enc))
            last = st - nl
        return out

    def sub(self, data: bytes) -> bytes:
        hits = self.find(data)
        if not hits:
            return data
        buf = bytearray(data)
        for st, ln, enc in hits:
            orig = bytes(data[st:st + ln])
            k = (enc, orig)
            r = self.cache.get(k)
            if r is None:
                try:
                    if enc == "u8":
                        r = pseudo_utf8(orig)
                    elif enc == "u16":
                        r = pseudo_utf16(orig)
                    else:
                        r = pseudo_latin1(orig)
                except Exception:
                    r = orig
                if len(r) != ln:
                    r = orig
                self.cache[k] = r
            buf[st:st + ln] = r
            self.hits[orig] = self.hits.get(orig, 0) + 1
        return bytes(buf)

    def sub_str(self, s: str) -> str:
        """Replace inside a Python string (paths, metadata); length may change."""
        if not self.n or not s:
            return s
        b = s.encode("utf-8", "surrogateescape")
        hits = [h for h in self.find(b) if h[2] == "u8"]
        if not hits:
            return s
        out, pos = [], 0
        for st, ln, enc in hits:
            out.append(b[pos:st].decode("utf-8", "surrogateescape"))
            frag = b[st:st + ln].decode("utf-8", "surrogateescape")
            out.append(pseudo_str(frag))
            pos = st + ln
        out.append(b[pos:].decode("utf-8", "surrogateescape"))
        return "".join(out)
