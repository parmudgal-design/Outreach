"""Dr.Tracker's partner marketplace: brands and health professionals.

The founder's sheet codes each partner as Type (B = brand, P = professional)
and Category. This module holds what each category means for an email — who
the partner serves, a hypothetical moment on Dr.Tracker that leads to them,
honest subjects, and the tone the profession needs — plus the small parsers
that turn sheet cells (IDs, "Notes / Source") into contact fields.
"""
import re
from dataclasses import dataclass

from . import specialties

TYPES = ("brand", "professional")
# Doctors and clinics: NMC rules forbid paying for or being paid for patient referrals.
DOCTOR_CATEGORIES = {"GP", "SPC"}


@dataclass
class Category:
    label: str
    seekers: str        # the people on Dr.Tracker who'd look for this partner ("People on Dr.Tracker who …")
    moment: str         # opening scene used when the sheet has no description
    subjects: list[str]
    tone: str           # drafting guidance for the prompt
    ask: str            # the 15-minute-call question, in the profession's register


BRAND_CATEGORIES: dict[str, Category] = {
    "FD": Category(
        "Wellness food",
        "log their meals and want food that fits the plan",
        "Picture someone who started logging meals on Dr.Tracker last month. They're in a supermarket aisle, reading labels, trying to find a snack that fits what they've been working towards.",
        ["Food that fits the plan", "Where people read every label", "The snack aisle, with a plan in hand"],
        "Friendly and concrete. Talk about everyday eating, not nutrition science.",
        "Would you be open to a 15-minute call? I'd show you how listing on Dr.Tracker would work for {who}.",
    ),
    "SUP": Category(
        "Supplements",
        "are keeping track of their sleep, energy or a nutrient their doctor mentioned",
        "Picture someone who has been logging their sleep and energy on Dr.Tracker for a few weeks. Their doctor mentioned their vitamin D was low, and now they're trying to work out what to buy.",
        ["Where people decide what to try next", "After the doctor mentions vitamin D", "Reaching people mid health-goal"],
        "Measured and factual. Supplements attract scepticism, so make no claim about what any product does.",
        "Would you be open to a 15-minute call? I'd show you how listing on Dr.Tracker would work for {who}.",
    ),
    "PRO": Category(
        "Protein and sports nutrition",
        "are building a fitness routine and trying to hit a daily protein target",
        "Picture someone a few weeks into building a fitness routine. They log every workout on Dr.Tracker, have just set a daily protein target, and are comparing tubs and bars to find one that fits.",
        ["The protein target, logged every day", "After the workout log, the shopping list", "People counting grams of protein"],
        "Energetic but grounded. Talk about routines and goals, never about results.",
        "Would you be open to a 15-minute call? I'd show you how listing on Dr.Tracker would work for {who}.",
    ),
    "DV": Category(
        "Health devices",
        "check their blood pressure, sugar or weight at home and track the readings",
        "Picture someone who has been writing their blood pressure readings into Dr.Tracker by hand every morning. They're starting to wonder whether a better monitor would make it easier.",
        ["People who already take readings at home", "Every morning's reading, typed in by hand", "Where home readings get tracked"],
        "Practical. Talk about daily use at home, not about accuracy or clinical outcomes.",
        "Would you be open to a 15-minute call? I'd show you how listing on Dr.Tracker would work for {who}.",
    ),
}

PROFESSIONAL_CATEGORIES: dict[str, Category] = {
    "NT": Category(
        "Nutritionist / dietitian",
        "want help turning their food logs into a real plan",
        "Picture someone who has logged every meal on Dr.Tracker for a month. They've decided it's time to see a nutritionist who can turn those notes into a plan they can actually follow.",
        ["From food log to a real plan", "People looking for a nutritionist like you", "A month of meal logs, and a next step"],
        "Friendly and practical. Respect their expertise; don't tell them what good nutrition is.",
        "Would you be open to a 15-minute call? I can show you how a listing works and answer any questions.",
    ),
    "GP": Category(
        "General physician",
        "want a doctor close to home they can trust",
        "Picture someone who has been noting a cough and a mild fever in Dr.Tracker for three days. They've decided it's time to see a doctor, and want a general physician near home.",
        ["Patients looking for a doctor nearby", "Three days of notes, and a doctor to see", "When people decide it's time to see a GP"],
        "Respectful and professional. Every clinical decision stays with the doctor. Never discuss payments of any kind.",
        "Would you be open to a 15-minute call, at a time that suits your clinic hours, to see how a listing works?",
    ),
    "SPC": Category(
        "Specialist / clinic",
        "have been advised to see a specialist and are deciding where to go",
        "Picture someone whose readings in Dr.Tracker have been slowly creeping up. Their GP has suggested seeing a specialist, and they're trying to work out which clinic to trust.",
        ["When patients are told to see a specialist", "Choosing a clinic, readings in hand", "When the GP says: see a specialist"],
        "Respectful and professional. Every clinical decision stays with the doctor. Never discuss payments of any kind.",
        "Would you be open to a 15-minute call, at a time that suits your clinic hours, to see how a listing works?",
    ),
    "MH": Category(
        "Mental health",
        "are ready to talk to someone and want a professional they feel comfortable with",
        "Picture someone who has been noting low moods and poor sleep in Dr.Tracker for a few weeks. Quietly, they've decided they're ready to talk to someone, and they're looking for a counsellor they'd feel comfortable reaching out to.",
        ["When someone is ready to talk", "Finding someone they'd feel comfortable with", "The quiet decision to reach out"],
        "Warm, gentle and careful, with no pressure. No clinical labels for the person in the scene and nothing dramatic.",
        "If it feels right, would you be open to a 15-minute call to see how a listing works? There's no pressure either way.",
    ),
    "PT": Category(
        "Physio / trainer",
        "want to recover from an injury or train safely",
        "Picture someone who logs a stiff knee in Dr.Tracker after every run. They want a physio or trainer nearby who can get them moving properly again.",
        ["The stiff knee after every run", "Back to training, properly this time", "People looking for a physio nearby"],
        "Practical and energetic. Short sentences, everyday language.",
        "Would you be up for a quick 15-minute call? I'll show you how a listing works.",
    ),
}

CATEGORY_WORDS = {
    "FD": ["food", "foods", "wellness food", "snacks", "fd"],
    "SUP": ["supplement", "supplements", "nutraceutical", "nutraceuticals", "vitamins", "sup"],
    # The brand sheet writes PR and DEV.
    "PRO": ["protein", "sports nutrition", "pro", "pr"],
    "DV": ["device", "devices", "health device", "health devices", "wearable", "dv", "dev"],
    "NT": ["nutritionist", "dietitian", "dietician", "nutrition", "nt"],
    "GP": ["gp", "general physician", "family physician", "general practitioner"],
    "SPC": ["specialist", "clinic", "specialist clinic", "spc"],
    "MH": ["mental health", "psychologist", "counsellor", "counselor", "therapist", "psychiatrist", "mh"],
    "PT": ["physio", "physiotherapist", "trainer", "fitness trainer", "pt"],
}


def categories(ctype: str) -> dict[str, Category]:
    return BRAND_CATEGORIES if ctype == "brand" else PROFESSIONAL_CATEGORIES


def category(contact: dict) -> Category:
    """The contact's category, or a sensible default for its type when the sheet left it blank."""
    ctype = contact.get("type")
    cats = categories(ctype)
    return cats.get((contact.get("category") or "").upper()) or cats["FD" if ctype == "brand" else "NT"]


def is_doctor(contact: dict) -> bool:
    """GPs and specialists/clinics. With no category, a "Dr." professional counts as a doctor, to stay on the safe side."""
    if contact.get("type") != "professional":
        return False
    code = (contact.get("category") or "").upper()
    if code:
        return code in DOCTOR_CATEGORIES
    return bool(re.match(r"^\s*dr\.?\s", contact.get("name") or "", re.I))


def guess_category(ctype: str, *texts: str) -> str:
    """Category code from the category/specialty text; for a professional, a medical specialty means a specialist."""
    for t in texts:
        if code := normalise_category(t, ctype):
            return code
    if ctype == "professional":
        key, _ = specialties.detect({"specialty": texts[1] if len(texts) > 1 else "", "name": ""})
        if key:
            return "GP" if key == "general_medicine" else "SPC"
    return ""


def normalise_category(value: str, ctype: str = "") -> str:
    v = re.sub(r"\s+", " ", (value or "").strip().lower())
    if not v:
        return ""
    allowed = categories(ctype) if ctype in TYPES else {**BRAND_CATEGORIES, **PROFESSIONAL_CATEGORIES}
    for code, words in CATEGORY_WORDS.items():
        label = allowed[code].label.lower() if code in allowed else ""
        parts = {p.strip() for p in re.split(r"[/&,]| and ", label)}
        if code in allowed and (v == code.lower() or v in words or v == label or v in parts):
            return code
    return ""


REMARKS_ID_RE = re.compile(r"[\s;,.]*\bpartner id\s*:?\s*([A-Z]-[A-Z0-9-]+)\s*\.?\s*$", re.I)


def split_partner_id(remarks: str) -> tuple[str, str]:
    """'Busy store; Partner ID B-SUP-T2-MUM-01' -> ('Busy store', 'B-SUP-T2-MUM-01')."""
    m = REMARKS_ID_RE.search(remarks or "")
    if not m:
        return (remarks or "").strip(), ""
    return remarks[:m.start()].strip(), m.group(1).upper()


ID_RE = re.compile(r"^\s*([BP])-([A-Z]{2,3})-(T\d|TBD)?-?([A-Z]{3})-(\d+)\s*$", re.I)


def parse_partner_id(pid: str) -> dict:
    """'B-SUP-T2-MUM-01' -> {'type': 'brand', 'category': 'SUP', 'tier': 'T2'}; {} if it isn't one."""
    m = ID_RE.match(pid or "")
    if not m:
        return {}
    kind, cat, tier = m.group(1).upper(), m.group(2).upper(), (m.group(3) or "").upper()
    return {"type": "brand" if kind == "B" else "professional", "category": cat, "tier": "" if tier == "TBD" else tier}


URL_RE = re.compile(r"(https?://\S+|\b(?:www\.)?[a-z0-9-]+(?:\.[a-z0-9-]+)*\.[a-z]{2,24}\b(?:/\S*)?)", re.I)


def parse_notes_source(text: str) -> dict:
    """Split the sheet's free-text 'Notes / Source' cell.

    'Official site example.com; Plant protein bars, Mumbai HQ; Source: https://…; CHECK: email is generic'
    -> website, description, source_url and needs_check. Any part may be missing.
    """
    out = {"website": "", "description": "", "source_url": "", "needs_check": ""}
    rest = []
    for part in re.split(r"\s*;\s*", (text or "").strip()):
        if not part:
            continue
        low = part.lower()
        if m := re.match(r"check\s*:\s*(.*)", part, re.I):
            out["needs_check"] = m.group(1).strip() or "flagged for checking"
        elif m := re.match(r"source\s*:\s*(.*)", part, re.I):
            out["source_url"] = m.group(1).strip()
        elif low.startswith(("official site", "website", "site:", "web:")) and (u := URL_RE.search(part)):
            out["website"] = u.group(0).rstrip(".,")
        elif not out["website"] and URL_RE.fullmatch(part):
            out["website"] = part.rstrip(".,")
        else:
            rest.append(part)
    out["description"] = "; ".join(rest).strip()
    return out


HQ_RE = re.compile(r",?\s*\b[A-Z][a-zA-Z]+(?:\s[A-Z][a-zA-Z]+)?\s+HQ\b\.?", re.I)


def clean_description(text: str, max_len: int = 160) -> str:
    """The description as a template can quote it: no 'Mumbai HQ' tail, no trailing stop, not too long.
    Returns '' when it contains claims a draft mustn't repeat as fact (superlatives, cures, figures)."""
    from . import qa  # local import: qa imports this module

    d = HQ_RE.sub("", (text or "").strip())
    d = re.sub(r"\s+", " ", d).strip(" ;,.")
    if not d or qa.claim_in(d):
        return ""
    if len(d) > max_len:
        cut = max(d.rfind(", ", 0, max_len), d.rfind("; ", 0, max_len))
        d = d[:cut] if cut > 40 else ""
    return d


def choose(items: list[str], seed: str, salt: str = "") -> str:
    return specialties.pick(items, seed, salt)


SPEND_RE = re.compile(r"^\s*(?:₹|rs\.?|inr)?\s*(\d[\d,]*(?:\.\d+)?)\s*(?:(?:-|–|—|to)\s*(?:₹|rs\.?|inr)?\s*(\d[\d,]*(?:\.\d+)?))?\s*(?:lakhs?|lacs?|l)?\s*$", re.I)


def spend_range(spend: str) -> tuple[float, float] | None:
    """The sheet's "Spend last quarter (₹ lakhs)" cell as (low, high): "18-25" -> (18, 25), "20" -> (20, 20).
    None when it's blank or not a figure, since the brand guide never lets a spend figure be invented."""
    m = SPEND_RE.match(spend or "")
    if not m:
        return None
    low, high = float(m.group(1).replace(",", "")), float((m.group(2) or m.group(1)).replace(",", ""))
    return (min(low, high), max(low, high))


def lakhs(n: float) -> str:
    """18.0 -> '18', 1.5 -> '1.5', 2000 -> '2,000'."""
    return f"{int(n):,}" if n == int(n) else f"{n:g}"


CITY_SUFFIX_RE = re.compile(r"[\s,-]+(mumbai|delhi|delhi ncr|ncr|new delhi|bengaluru|bangalore|blr|hyderabad|hyd|pune|chennai|kolkata)\s*$", re.I)
WEB_SUFFIX_RE = re.compile(r"(\.|\s)(in|com|co\.in)\s*$", re.I)


def shop_name(name: str) -> str:
    """'Natures Basket - Sultan Palya' -> 'Natures Basket', 'Fix The Glytch / GLYTCH' -> 'Fix The Glytch',
    'Fast&Up Mumbai' -> 'Fast&Up', 'Primal Nutrition.In' -> 'Primal Nutrition'. Brackets are dropped too."""
    n = re.sub(r"\s+", " ", re.sub(r"\([^)]*\)", " ", name or "")).strip()
    for sep in (" / ", " | ", " - ", " – ", " — "):
        head = n.split(sep)[0].strip()
        if len(head) >= 2:
            n = head
    for rx in (CITY_SUFFIX_RE, WEB_SUFFIX_RE):
        cut = rx.sub("", n).strip(" ,.-")
        if len(cut) >= 2:
            n = cut
    return n
