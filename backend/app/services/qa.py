"""Pre-send checks. Errors block sending (automatic or manual); warnings are
shown to the reviewer. Cheap heuristics that catch the usual problems with an
AI or template draft before a human skims it."""
import re

URGENCY_RE = re.compile(r"\b(limited time|last chance|act now|only \d+ (spots|slots|places)|expires|hurry|don'?t miss)\b", re.I)
PLACEHOLDER_RE = re.compile(r"\[[^\]]{1,40}\]|\{[^}]{1,40}\}|\(not (given|supplied)|\(unknown\)|\(sender name\)|lorem ipsum", re.I)
FIGURE_RE = re.compile(
    r"\d[\d,.]*\s*(%|percent|x\b|times\b|lakh|crore|k\b)|\b\d{3,}(,\d{3})*\+?\s+(doctors|patients|hospitals|clinics|users)",
    re.I,
)
TITLE_RE = re.compile(r"^(dr\.?|mr\.?|ms\.?|mrs\.?|prof\.?)\s+", re.I)

# Indian advertising / medical-conduct guardrails — the same rules the drafting prompt states.
FAKE_THREAD_RE = re.compile(r"^\s*(re|fwd?|fw)\s*:", re.I)
INCENTIVE_RE = re.compile(
    r"\b(gift|gifts|commission|referral (fee|bonus)|revenue[- ]share|cash ?back|honorarium|sponsor(ed|ship)?|"
    r"free (trip|travel|holiday|dinner|stay)|incentives?|kickback|per[- ]patient (fee|payment)|we('| wi)ll pay you|paid per)\b",
    re.I,
)
CURE_RE = re.compile(
    r"\b(cures?|cured|guarantee[ds]?|100 ?%|miracle|eliminates?|zero (complications|readmissions|risk)|"
    r"prevents? (death|deaths|readmissions?|complications?)|saves? lives)\b",
    re.I,
)
CLINICAL_CLAIM_RE = re.compile(r"\b(diagnos(e|es|is)|treats?|detects? (disease|cancer|heart attack)|predicts?)\b", re.I)
SUPERLATIVE_RE = re.compile(r"\b(the best|best-in-class|no\.? ?1|number one|india'?s (first|only|largest)|world[- ]class|revolutionary|unmatched|market[- ]leading)\b", re.I)
ENDORSE_RE = re.compile(r"\b(endorse|endorsement|promote (our|it|dr\.?tracker)|brand ambassador|testimonial|prescribe (our|it|their))\b", re.I)
FEAR_RE = re.compile(r"\b(die[ds]?|death|dying|fatal|icu|ventilator|funeral)\b", re.I)


def check(subject: str, body: str, contact: dict, sender: dict, pitches: dict, kind: str = "initial") -> list[dict]:
    out: list[dict] = []
    subject = (subject or "").strip()
    body = (body or "").strip()

    def add(level, msg):
        out.append({"level": level, "message": msg})

    if not subject:
        add("error", "No subject line.")
    if not body:
        add("error", "No body yet.")
        return out
    if PLACEHOLDER_RE.search(subject + " " + body):
        add("error", "A placeholder was left in (e.g. [Name] or \"(not given)\").")
    if not sender.get("name"):
        add("error", "Sender name is missing — fill it in under Setup.")
    if not sender.get("address"):
        add("error", "Business address is missing — the compliance footer needs it (Setup).")

    names = TITLE_RE.sub("", (contact.get("name") or "").split(",")[0]).strip().split()
    if names and len(names[-1]) > 1 and not any(n.lower() in body.lower() for n in {names[0], names[-1]}):
        add("warn", f"Doesn't mention {names[-1]} by name — check the greeting.")

    words = len(body.split())
    limit = 150 if kind == "followup" else 240
    if words > limit:
        add("warn", f"{words} words — long for a {'follow-up' if kind == 'followup' else 'cold email'}.")
    if kind == "initial" and len(subject.split()) > 12:
        add("warn", "Subject is long — under ~9 words usually does better.")

    known = " ".join(
        [
            " ".join([(p or {}).get("pitch", "") + " " + " ".join((p or {}).get("props", [])) for p in pitches.values()]),
            contact.get("notes") or "",
        ]
    ).lower()
    figures = [m.group(0) for m in FIGURE_RE.finditer(body) if m.group(0).lower().strip() not in known]
    if figures:
        add("warn", f"Mentions figures not in Setup or notes ({', '.join(figures[:3])}) — make sure they're real.")
    if URGENCY_RE.search(subject + " " + body):
        add("warn", "Uses pressure language (deadlines, scarcity) — CCPA misleading-ads / dark-pattern guidelines. Take it out.")

    text = subject + "\n" + body
    if kind == "initial" and FAKE_THREAD_RE.search(subject):
        add("error", "Subject starts with Re:/Fwd: on a first email — deceptive under the IT Act and CCPA guidelines.")
    if m := INCENTIVE_RE.search(text):
        add("error", f"Offers an incentive (\"{m.group(0)}\") — gifts, fees or perks to doctors breach NMC 2023 / UCPMP 2024.")
    if m := CURE_RE.search(text):
        add("error", f"Makes a cure/guarantee claim (\"{m.group(0)}\") — not allowed (Drugs & Magic Remedies Act, ASCI).")
    if m := ENDORSE_RE.search(text):
        add("error" if contact.get("type", "doctor") in ("doctor", "partner") else "warn",
            f"Asks for an endorsement or prescribing (\"{m.group(0)}\") — doctors may not endorse products (NMC 2023).")
    if m := CLINICAL_CLAIM_RE.search(text):
        add("warn", f"Sounds like a clinical claim (\"{m.group(0)}\") — Dr.Tracker helps doctors notice sooner; it doesn't diagnose or treat.")
    if m := SUPERLATIVE_RE.search(text):
        add("warn", f"Unsubstantiated superlative (\"{m.group(0)}\") — ASCI requires claims you can back up.")
    if m := FEAR_RE.search(body):
        add("warn", f"Fear-based wording (\"{m.group(0)}\") — ASCI discourages exploiting fear; keep the scene calm.")
    if kind == "initial" and contact.get("type", "doctor") == "doctor":
        # Greeting, then the story paragraphs; the product belongs only in the paragraph before the sign-off.
        paras = [p for p in re.split(r"\n\s*\n", body) if p.strip()]
        story = paras[1:3] if len(paras) >= 5 else paras[1:2]
        if any("dr.tracker" in p.lower() for p in story):
            add("warn", "Dr.Tracker is named in the opening — lead with the story and save the offer for the last paragraph.")
    return out


def has_errors(issues: list[dict]) -> bool:
    return any(i["level"] == "error" for i in issues)
