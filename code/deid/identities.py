"""Pass 1b: turn harvested raw values into the identity lists used for replacement.

global  -> replaced everywhere (content bytes in UTF-8/UTF-16/Latin-1, paths, archive names)
paths   -> replaced only in file and folder names (short acronyms)
fields  -> replaced only inside identity fields (common first names, generic-looking values)
"""
import json, re, sys, collections

try:
    from wordfreq import zipf_frequency
except Exception:  # pragma: no cover
    def zipf_frequency(w, lang):
        return 0.0

GENERIC = {
    "author", "an author", "a company", "user", "users", "administrator", "admin", "unknown", "none",
    "system", "me", "owner", "public", "default", "all users", "temp", "tmp", "guest", "operator",
    "anonymous", "unspecified", "pcbnew", "kicad", "freecad", "open cascade", "opencascade",
    "microsoft", "solidworks", "autodesk", "user32", "nx", "default user", "localservice",
    "networkservice", "unknown authorisation", "unknown authorization", "confidential", "first",
    "last", "first last", "mr. president", "mr.", "president", "box", "na", "n/a", "test", "name",
    "company", "organization", "organisation", "student", "personal", "home", "standard", "build",
    "builder", "runner", "root", "ubuntu", "vagrant", "docker", "jenkins", "appveyor", "travis",
    "zemax", "spatial corp", "ptc", "siemens", "dassault systemes", "3ds", "catia", "onshape",
    "fusion", "inventor", "solid edge", "rhino", "sketchup", "blender", "openscad", "cadquery",
    "build123d", "step.parts", "traceparts", "traceparts s.a.", "zwcad software co.", "zwsoft",
    "abcd", "xxx", "x", "desktop", "documents", "downloads", "appdata", "local", "roaming",
    "program files", "windows", "pc", "computer", "laptop", "workstation", "ws", "hp", "dell",
    "lenovo", "asus", "acer", "msi", "intel", "amd", "pdf", "cad", "cam", "draft", "drawing",
    "part", "assembly", "model", "design", "engineering", "mechanical engineering", "engineer",
    "designer", "drafter", "checker", "approver", "manager", "lab", "team", "group", "project",
    "university", "school", "college", "institute", "department", "inc", "inc.", "ltd", "llc",
    "gmbh", "co.", "corp", "corporation", "company name", "your name", "your company", "todo",
    "unbekannt", "benutzer", "utilisateur", "usuario", "utente", "gebruiker", "未指定", "未", "微软用户",
    "微软中国", "dell user", "hp user", "hp inc.", "windows user", "valued customer", "authorized user",
    "unregistered", "unlicensed", "trial", "evaluation", "license", "licensed user", "(unspecified)",
    "unspecified unspecified", "noname", "no name", "anon", "nobody", "somebody", "someone", "my company",
    "nauo", "solidworks pdf publisher", "zemax", "mm", "in", "inch", "edu", "educational", "academic",
}
GENERIC_RE = re.compile(r"^(?:nauo-(?:org|per)\d+|person_\w+|\w+_(?:first|last)name|[\d\W_]+|.{0,1}|"
                        r"desktop-[a-z0-9]{5,8}|laptop-[a-z0-9]{5,8}|win-[a-z0-9]{6,12}|user\d*|admin\d*|"
                        r"pc\d*|.*@(?:\d|ugs)\..*|[a-z]:.*|[a-z]{1,2}\d+(?:x\d*)?|\d+[a-z]{1,3}\d*)$", re.I)

# Organisations, people and projects named in catalog/web folder titles (curated by hand
# from the manifest; these are the identifying parts of those folder names).
CURATED_GLOBAL = [
    "Aarhus", "AdReNa", "Buildlog", "buildlog.net", "CNR-STIIMA", "STIIMA", "Dominique Lachiver", "Lachiver",
    "FENRIR", "FabCity Hamburg", "FabCity", "Curious Community Labs", "LOCILAMP", "LBNL", "Trillium II",
    "LulzBot", "Aleph Objects", "Maximilian Behm", "Behm", "Maxwell Lohss", "Lohss", "Muzi Works", "BASE DUO",
    "NIST", "NTNU", "Open Source Ecology", "OpenSourceEcology", "opensourceecology", "PowerCube6", "PowerCube",
    "CEB Press", "ThrowBot", "Open Lab Automata", "Pipettin", "pipettin-bot", "EPFL", "IIT Kanpur", "IITK",
    "RWTH Aachen", "RWTH", "TU Delft", "Tiny Oat Collider", "UKCEH", "University Ljubljana",
    "University Michigan", "OpenSourceLeg", "Open Source Leg", "University Southampton", "University Stuttgart",
    "HyFaB", "Vhéliotech", "vhélio", "Vhelio", "Remorque PM", "Festo", "Misumi", "MISUMI", "Marco Inc",
    "marco-inc", "omnimech", "seedsovereignty", "Darachbeg Croft", "LIGO", "Caltech", "ohwr", "Open Hardware Repository",
    "learning-tower", "Learning Tower", "M4x", "ProfeMaxi", "Profe Maxi", "open-la", "Thorlabs", "THORLABS",
    "Machinist Blog", "modelesavapeur", "ulrich-rapp", "Ulrich Rapp", "neme-s", "journeymans-workshop",
    "Journeymans Workshop", "soumard", "upskill-dev", "episerver", "itwwelding", "teenage engineering",
    "teenage.engineering", "TU Chemnitz", "tu-chemnitz", "Siemens AG", "Rockwell Collins", "Curtin University",
    "Yale University", "WorldSkills International", "WorldSkills", "Hewlett-Packard Company",
    "Carnegie Mellon University", "Carnegie Mellon", "ETH Zurich", "CA Institute of Technology",
    "Motedis GmbH", "Motedis", "DyvikDesign", "Dyvik Design", "Fabricatable Machines", "PathOn Robotics",
    "BERSERK.DESIGN", "episource.de", "leonvandenbeukel.nl", "Sciurus17", "OpenLoong", "Seeed Studio",
    "Source Robotics", "Foundation Devices", "BrainCOGS", "Global Health Engineering", "PAROL6",
    "University of Michigan", "University of Southampton", "University of Stuttgart", "University of Ljubljana",
]
# short acronyms only renamed in paths (too collision-prone for raw bytes)
CURATED_PATHS = ["BAM", "AWI", "OSE", "OST", "UNH", "ZSW", "MAS 2.0", "RM2", "ARC-RWS", "MOUSE", "ORD Bot",
                 "CTC_FTC", "HDX-MS", "IIT", "iitm", "iitk", "TRC", "PMP", "dDNP", "LOD-ESR"]

# big public platforms in web hostnames: keep, they identify nobody
PLATFORM_LABELS = {"www", "cdn", "media", "files", "static", "assets", "blob", "core", "windows", "dropbox",
                   "shopify", "wordpress", "github", "gitlab", "amazonaws", "cloudfront", "googleusercontent",
                   "arxiv", "biorxiv", "nvlpubs", "data", "download", "ftp", "public", "wiki", "ed", "home",
                   "contenthub", "api", "app", "uploads", "net", "com", "org", "edu", "gov", "prod", "dev",
                   "dc", "dcc", "llo", "wa", "peer", "staff", "np", "phy", "seas", "us", "ec", "cnc", "network",
                   "lg", "metro", "tokyo", "co", "ac", "or", "go", "thin01mstroc282prod", "dxcloud"}
TLDS = {"com", "org", "net", "edu", "gov", "info", "io", "de", "uk", "fr", "it", "es", "nl", "jp", "cn", "ru", "ch",
        "at", "be", "se", "no", "dk", "fi", "pl", "cz", "ca", "au", "us", "in", "br", "kr", "tw", "hu", "tr", "eu",
        "mx", "ar", "pt", "gr", "ie", "nz", "sg", "hk", "il", "dev", "me", "tech", "xyz", "co", "ac", "cc", "tv",
        "biz", "us", "ro", "sk", "si", "lt", "lv", "ee", "ua", "by", "za", "vn", "th", "id", "my", "ph", "cl", "pe"}


def clean(v):
    v = v.split("\x00")[0]
    v = re.sub(r"\s+", " ", v).strip().strip("'\"")
    if "Ã" in v or "Â" in v:
        try:
            v = v.encode("latin-1").decode("utf-8")
        except Exception:
            pass
    return v


def is_generic(v):
    lv = v.lower().strip()
    return (lv in GENERIC or GENERIC_RE.match(lv) is not None or len(lv) < 2
            or not re.search(r"[^\W\d_]", lv))


def common_word(v):
    toks = re.findall(r"[^\W\d_]+", v.lower())
    if len(toks) != 1:
        return False
    return zipf_frequency(toks[0], "en") >= 4.3 or len(toks[0]) <= 3


def split_value(v):
    """'DOMAIN\\user (MACHINE)' / 'Name, phone, email' -> parts"""
    parts = [p.strip() for p in re.split(r"[\\/(),;|<>\[\]]| - ", v) if p.strip()]
    return parts


def good_email(e):
    m = re.fullmatch(r"([A-Za-z0-9._%+-]+)@([A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*)\.([a-z]{2,6})", e)
    if not m or m.group(3) not in TLDS:
        return False
    if re.search(r"\.(sldprt|sldasm|slddrw|tlb|dll|ipt|iam|prt)$", e, re.I):
        return False
    dom = m.group(2)
    if dom.lower() != dom and not dom.isupper():
        return False
    return len(m.group(1)) >= 2 and "--" not in e


def build(harvest_files, manifest=None):
    glob, fields, paths = set(), set(), set()
    kinds = collections.Counter()

    def add_person_like(v, src):
        v = clean(v)
        if not v or is_generic(v) or len(v) > 80:
            return
        parts = split_value(v)
        cand = [v] + (parts if len(parts) > 1 else [])
        for c in cand:
            c = c.strip(" .-_")
            if not c or is_generic(c):
                continue
            if "@" in c:
                if good_email(c):
                    glob.add(c); glob.add(c.split("@")[0]) if len(c.split("@")[0]) >= 4 and not common_word(c.split("@")[0]) else None
                continue
            if len(c) >= 4 and not common_word(c):
                glob.add(c)
            else:
                fields.add(c)
            # tokens of multi-word person names (surnames usually)
            toks = re.findall(r"[^\W\d_][^\W_]*", c)
            if 1 < len(toks) <= 4:
                for t in toks:
                    if len(t) >= 5 and zipf_frequency(t.lower(), "en") < 3.0 and not is_generic(t):
                        glob.add(t)
            kinds[src] += 1

    for hf in harvest_files:
        for line in open(hf, encoding="utf-8"):
            d = json.loads(line)
            v, k = d["v"], d["k"]
            if k == "email":
                v = clean(v)
                if good_email(v):
                    glob.add(v)
                    loc = v.split("@")[0]
                    if len(loc) >= 4 and not common_word(loc) and not is_generic(loc) and loc.lower() not in ("info", "sales", "support", "contact", "office", "admin", "mail", "noreply", "webmaster", "service"):
                        glob.add(loc)
                    dom = v.split("@")[1]
                    if dom.lower() not in ("gmail.com", "yahoo.com", "hotmail.com", "outlook.com", "qq.com", "163.com", "126.com", "web.de", "gmx.de", "gmx.net", "aol.com", "icloud.com", "live.com", "msn.com", "email.com", "example.com", "autodesk.com", "3ds.com"):
                        glob.add(dom)
            elif k in ("gh_owner", "gl_owner"):
                v = v.strip()
                if not v:
                    continue
                if (len(v) >= 4 and not common_word(v) and not is_generic(v)) or re.search(r"\d|-", v):
                    glob.add(v)
            elif k == "web_domain":
                labs = v.lower().split(".")
                glob.add(v)
                core = [l for l in labs if l not in PLATFORM_LABELS and l not in TLDS]
                for l in core:
                    if len(l) >= 4 and not common_word(l):
                        glob.add(l)
                    else:
                        paths.add(l)
                if len(labs) >= 2 and labs[-2] not in PLATFORM_LABELS:
                    glob.add(".".join(labs[-2:]))
            elif k in ("step_person_id", "step_person_first", "step_person_last", "iges_org",
                       "step_authorization"):
                v = clean(v)
                if k == "step_authorization" and v.lower() not in ("unknown",) and not is_generic(v):
                    add_person_like(v, k)
                elif k in ("step_person_first", "step_person_last") and not is_generic(v):
                    fields.add(v)
                continue
            else:
                add_person_like(v, k)

    for c in CURATED_GLOBAL:
        glob.add(c)
    for c in CURATED_PATHS:
        paths.add(c)
    glob = {g for g in glob if len(g) >= 3 and not is_generic(g)}
    fields -= glob
    paths -= glob
    return sorted(glob), sorted(fields), sorted(paths), kinds


if __name__ == "__main__":
    out = sys.argv[1]
    g, f, p, kinds = build(sys.argv[2:])
    json.dump({"global": g, "fields": f, "paths": p}, open(out, "w"), ensure_ascii=False, indent=0)
    print(len(g), len(f), len(p), dict(kinds))
