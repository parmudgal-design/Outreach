"""Who an email is addressed to, worked out from the Name cell.

The founder's sheet mixes people and organisations in one column:
"Dr. Kavya Rao (FitPlate Nutrition)", "Dt. Meenal Joshi (Green Bowl)",
"Ritu Sethi (Nourish Kitchen)", "VitalSense", "Total Health Physio & Sports".
`parse` tells them apart, so a draft greets a person by name and an
organisation as a team, and never writes "Dear 24," or "Dr. Sports".
"""
import re
from dataclasses import dataclass

TITLE_RE = re.compile(r"^(dr|dt|prof|mr|mrs|ms)\.?\s+", re.I)
TITLES = {"dr": "Dr.", "dt": "Dt.", "prof": "Prof.", "mr": "Mr.", "mrs": "Mrs.", "ms": "Ms."}
COMPANY_SUFFIX_RE = re.compile(r"[\s,]+(pvt\.?\s*ltd\.?|private limited|ltd\.?|limited|llp|inc\.?)\s*$", re.I)
DEGREE_WORDS = {"ortho", "cardio", "gynae", "paeds", "peds", "hons", "gold medalist"}

# Words that mark a name as an organisation, matched whole ("Total Health Physio & Sports")
# or as the end of a run-together brand ("LiveNutrifit", "PathLabs").
ORG_WORDS = {
    "health", "healthcare", "clinic", "clinics", "care", "centre", "center", "hospital", "hospitals", "wellness",
    "nutrition", "foods", "food", "kitchen", "labs", "lab", "diagnostics", "pharma", "pvt", "ltd", "limited", "llp",
    "inc", "india", "sports", "sport", "fitness", "physio", "physiotherapy", "rehab", "studio", "studios", "gym",
    "foundation", "trust", "group", "solutions", "technologies", "tech", "devices", "supplements", "protein",
    "naturals", "organics", "organic", "life", "living", "therapy", "counselling", "counseling", "minds", "wellbeing",
    "institute", "academy", "hub", "co", "company", "store", "services", "network", "nutraceuticals", "ayurveda",
    "dental", "diet", "diets", "yoga", "bowl", "team", "the", "and", "&", "+", "bio", "plus", "point", "space",
}
ORG_ENDINGS = ("labs", "lab", "care", "health", "fit", "foods", "nutrition", "clinic", "wellness", "pharma", "tech", "life", "living")
NAME_TOKEN_RE = re.compile(r"^[A-Z][a-zA-Z'’-]*\.?$")


@dataclass
class Addressee:
    kind: str           # person | org | unknown
    title: str = ""     # "Dr." etc. for a person
    first: str = ""
    last: str = ""
    org: str = ""       # the organisation, if one is named

    @property
    def is_person(self) -> bool:
        return self.kind == "person"

    def short(self) -> str:
        """How the email refers to them by name: 'Dr. Rao', 'Meenal', 'FitPlate'."""
        if self.is_person:
            return f"{self.title} {self.last}" if self.title in ("Dr.", "Prof.") else (self.first or self.last)
        return self.org


def _strip_degrees(name: str) -> str:
    return re.split(r",", name)[0].strip()  # "Rahul Nair, MS (Ortho)" -> "Rahul Nair"


def is_org_word(token: str) -> bool:
    t = token.lower().strip(".,'’()")
    return t in ORG_WORDS or any(t.endswith(e) and len(t) > len(e) + 2 for e in ORG_ENDINGS) or t.endswith(("'s", "’s"))


def looks_like_org(text: str) -> bool:
    tokens = (text or "").split()
    return any(re.search(r"\d", t) for t in tokens) or any(is_org_word(t) for t in tokens)


def _person(text: str, require_title: bool, lenient: bool = False) -> Addressee | None:
    text = _strip_degrees(text)
    m = TITLE_RE.match(text)
    title = TITLES[m.group(1).lower()] if m else ""
    rest = text[m.end():].strip() if m else text.strip()
    tokens = rest.split()
    if not tokens or (require_title and not title) or len(tokens) > 4 or looks_like_org(rest):
        return None
    if len(tokens) == 1 and not title:
        return None
    if not (title or lenient) and not all(NAME_TOKEN_RE.match(t) for t in tokens):
        return None
    return Addressee("person", title, tokens[0] if len(tokens) > 1 else "", tokens[-1])


def _is_degree(text: str) -> bool:
    words = re.findall(r"[A-Za-z]+", text)
    return bool(words) and all((w.isupper() and len(w) <= 6) or w.lower() in DEGREE_WORDS for w in words)


def _org_name(text: str) -> str:
    """'NOTO Healthy Ice Creams & Desserts' -> 'NOTO' (an all-caps brand followed by what it sells);
    'Kenia Eye Hospital Pvt Ltd' -> 'Kenia Eye Hospital'."""
    name = COMPANY_SUFFIX_RE.sub("", (text or "").strip()).strip(" ,")
    tokens = name.split()
    caps = 0
    while caps < len(tokens) and re.fullmatch(r"[A-Z][A-Z0-9&'’.-]+", tokens[caps]):
        caps += 1
    if 0 < caps < len(tokens):
        return " ".join(tokens[:caps])
    return name


def parse(name: str, email: str = "", assume_person: bool = False, bare_person: bool = False) -> Addressee:
    """`assume_person` is for the original contact types (doctor, hospital, …), whose Name column
    holds people ("Neha Kapoor"). Marketplace rows are organisations unless a title or a
    "Person (Organisation)" pattern says otherwise; `bare_person` also accepts a plain,
    properly capitalised personal name ("Ritu Sethi"), which suits professionals but not brands."""
    name = re.sub(r"\s+", " ", (name or "").strip())
    local = (email or "").split("@")[0].lower()
    if not name or name.lower() == local:
        return Addressee("unknown")
    m = re.match(r"^(.*?)\s*\(([^()]*)\)\s*$", name)
    if m and not _is_degree(m.group(2)):
        outside, inside = m.group(1).strip(), m.group(2).strip()
        # "Dr. Kavya Rao (FitPlate)" or "FitPlate (Dr. Kavya Rao)"
        for person_part, org_part in ((outside, inside), (inside, outside)):
            # A brand's brackets hold a branch or a legal name ("Dr Trust (BLR Regional)"), not a person, unless titled.
            p = _person(person_part, require_title=not (assume_person or bare_person))
            if p and not looks_like_org(person_part):
                p.org = _org_name(org_part)
                return p
        return Addressee("org", org=_org_name(outside or inside))
    if m:  # "Dr. Rahul Nair (MD)": a degree in brackets, not an organisation
        name = m.group(1).strip()
    p = _person(name, require_title=not (assume_person or bare_person), lenient=assume_person)
    if p:
        return p
    return Addressee("org", org=_org_name(name))


LEGACY_TYPES = ("doctor", "hospital", "company", "partner")


def for_contact(contact: dict) -> Addressee:
    ctype = contact.get("type") or "doctor"
    return parse(contact.get("name") or "", contact.get("email") or "",
                 assume_person=ctype in LEGACY_TYPES, bare_person=ctype == "professional")


def salutation(a: Addressee, tone: str = "warm") -> str:
    """The greeting line for a marketplace email."""
    if a.is_person:
        if a.title in ("Dr.", "Prof."):
            return f"Dear {a.title} {a.last},"
        return f"Dear {a.first or a.last},"
    if a.kind == "org":
        org = a.org[:-5].strip() if a.org.lower().endswith(" team") else a.org
        return f"Hello {org} team,"
    return "Hello,"


GREETING_RE = re.compile(r"^\s*(dear|hello|hi|hey|namaste)\b[ \t]*([^\n]*?)[ \t]*[,:]?[ \t]*$", re.I)
JUNK_NAME_RE = re.compile(r"^(none|nan|null|undefined|n/?a|-+|\.+|name)$", re.I)


def check(body: str, contact: dict) -> list[str]:
    """Problems with the greeting line, as error messages. Empty when it's fine."""
    first = next((line for line in (body or "").splitlines() if line.strip()), "")
    m = GREETING_RE.match(first)
    if not m:
        return []
    word, who = m.group(1).lower(), m.group(2).strip().rstrip(",:").strip()
    problems = []
    if not who:
        if word in ("dear", "namaste"):
            problems.append(f"The greeting (\"{first.strip()}\") has no name in it.")
        return problems
    bare = re.sub(r"^(dr|dt|prof|mr|mrs|ms)\.?\s+", "", who, flags=re.I)
    if re.fullmatch(r"[\d\W_]+", bare) or len(bare) == 1 or (len(bare) == 2 and not bare.isupper()):
        problems.append(f"The greeting (\"{first.strip()}\") has a lone number or fragment where the name should be.")
    if JUNK_NAME_RE.match(who) or re.search(r"\b(none|nan|null|undefined)\b", who, re.I):
        problems.append(f"The greeting (\"{first.strip()}\") contains an empty-field value.")
    dr = re.match(r"^dr\.?\s+(.+)$", who, re.I)
    if dr and not who.lower().endswith(" team"):  # "Hello Dr. Batra's Clinic team," is fine
        if looks_like_org(dr.group(1)) or for_contact(contact).kind == "org":
            problems.append(f"The greeting (\"{first.strip()}\") calls an organisation \"Dr.\". Greet the team instead.")
    return problems
