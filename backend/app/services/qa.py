"""Pre-send checks. Errors block sending (automatic or manual); warnings are
shown to the reviewer. Cheap heuristics that catch the usual problems with an
AI or template draft before a human skims it."""
import re

from . import greeting, marketplace

URGENCY_RE = re.compile(r"\b(limited time|last chance|act now|only \d+ (spots|slots|places)|expires|hurry|don'?t miss)\b", re.I)
PLACEHOLDER_RE = re.compile(r"\[[^\]]{1,40}\]|\{[^}]{1,40}\}|\(not (given|supplied)|\(unknown\)|\(sender name\)|lorem ipsum", re.I)
FIGURE_RE = re.compile(
    r"\d[\d,.]*\s*(%|percent|x\b|times\b|lakh|crore|k\b)|\b\d{3,}(,\d{3})*\+?\s+(doctors|patients|hospitals|clinics|users)",
    re.I,
)
# Empty-field artefacts a template or spreadsheet can leave behind. Case-sensitive on purpose:
# "None" and "TODO" are artefacts, "none of them" is English.
ARTEFACT_RE = re.compile(r"\b(None|NaN|nan|null|undefined|TODO|TBD|FIXME|XXX)\b|\bN/A\b|\byour (product|organi[sz]ation)\b(?![s'’])|\byour Product\b")
# The old doctor pitch (patient monitoring) has no place in a marketplace email.
MONITORING_RE = re.compile(r"\b(wounds?|incisions?|stitches|between (visits|appointments)|your patients|check[- ]ins? between|quiet flag)\b", re.I)
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

# Marketplace (brand / professional) emails.
PRICING_RE = re.compile(
    r"(₹\s?\d|\brs\.?\s?\d|\binr\b|\bprice[ds]?\b|\bpricing\b|\b(listing |joining |sign[- ]?up )?fees?\b|\bcommissions?\b|"
    r"\bper (booking|appointment|lead|sale|order|consultation|month|year)\b|\bsubscriptions?\b|\bdiscount(s|ed)?\b|"
    r"\b\d+\s?% off\b|\bfree (trial|listing|of charge|for \w+ months?)\b|\bcharges?\b|\bpaid (listing|plan|placement)s?\b|"
    r"\bsponsored\b|\bpromo(tional)? (code|offer)\b|\btrials?\b|\bfree (first )?(month|months|week|weeks)\b|\bearly[- ]partner (offer|pricing|rate)s?\b)",
    re.I,
)
# Paying for, or being paid for, patient referrals: never in an email to a doctor or clinic (NMC 2023).
REFERRAL_RE = re.compile(
    r"\b(commissions?|referral( fee| bonus| payment)?s?|revenue[- ]shar(e|ing)|per[- ](booking|appointment|patient|lead|referral)|"
    r"(share|cut) of (the |your )?(fees?|consultations?|bookings?)|kickbacks?|incentives?|pay(ment)? for (patients|bookings|leads))\b",
    re.I,
)
EFFICACY_RE = re.compile(
    r"\b(clinically (proven|tested)|scientifically proven|doctor[- ]recommended|boosts? (your |the )?(immunity|metabolism|energy)|"
    r"burns? fat|fat[- ]burning|melts?|detox(es|ify|ifies)?|reverses?|heals?|instant results|guaranteed results|"
    r"lose \d+ ?kgs?|no side[- ]effects|safe for everyone|100% natural|immunity booster)\b",
    re.I,
)


def claim_in(text: str) -> str:
    """The first claim in a piece of text that a draft mustn't repeat as fact, or ''."""
    for rx in (SUPERLATIVE_RE, CURE_RE, EFFICACY_RE, FIGURE_RE):
        if m := rx.search(text or ""):
            return m.group(0)
    return ""


def _without(text: str, phrase: str) -> str:
    """`text` with every occurrence of `phrase` taken out, ignoring case and spacing."""
    words = (phrase or "").split()
    if not words:
        return text
    return re.sub(r"\s+".join(re.escape(w) for w in words), " ", text, flags=re.I)


def marketplace_checks(text: str, contact: dict, market: dict, add) -> None:
    if m := MONITORING_RE.search(text):
        add("warn", f"Uses the old patient-monitoring pitch (\"{m.group(0)}\"). Marketplace emails are about being found, not about monitoring patients.")
    allowed = (market.get("trial_text") or "").strip() if market.get("mention_trial") else ""
    if marketplace.is_doctor(contact) and (m := REFERRAL_RE.search(text)):
        add("error", f"Mentions \"{m.group(0)}\" to a doctor or clinic. Paying for, or being paid for, patient "
                     "referrals is not allowed (NMC 2023). Take it out.")
    if m := PRICING_RE.search(_without(text, allowed)):
        if allowed:
            add("error", f"Mentions pricing (\"{m.group(0)}\"). With the trial setting on, the only offer allowed is the trial text from Setup.")
        else:
            add("error", f"Mentions pricing (\"{m.group(0)}\"). First emails don't mention prices, fees, commission or offers "
                         "(turn on \"Mention the trial\" under Setup to allow the trial text).")
    if m := EFFICACY_RE.search(text):
        add("error", f"Makes a health or efficacy claim (\"{m.group(0)}\") about a product or service. Not allowed (ASCI, Drugs & Magic Remedies Act).")


def check(subject: str, body: str, contact: dict, sender: dict, pitches: dict, kind: str = "initial",
          market: dict | None = None) -> list[dict]:
    out: list[dict] = []
    market = market or {}
    ctype = contact.get("type") or "doctor"
    is_market = ctype in marketplace.TYPES
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
    elif m := ARTEFACT_RE.search(subject + "\n" + body):
        add("error", f"A placeholder or empty-field artefact was left in (\"{m.group(0)}\"). Rewrite the sentence without it.")
    for problem in greeting.check(body, contact):
        add("error", problem)
    if not sender.get("name"):
        add("error", "Sender name is missing — fill it in under Setup.")
    if not sender.get("address"):
        add("error", "Business address is missing — the compliance footer needs it (Setup).")
    if is_market and market:
        from ..settings_store import DEFAULTS  # local import: settings_store imports db, not services
        defaults = DEFAULTS["marketplace"]
        field = "value_prop_brands" if ctype == "brand" else "value_prop_professionals"
        if (market.get(field) or "").strip() == defaults[field].strip():
            add("warn", "The marketplace pitch is still the built-in placeholder wording — replace it with the "
                        "founder's own under Setup → Marketplace pitch before sending for real.")

    who = greeting.for_contact(contact)
    expected = [who.last, who.first] if who.is_person else (who.org.split()[:1] if who.org else [])
    if expected and len(expected[0]) > 1 and not any(n and n.lower() in body.lower() for n in expected):
        add("warn", f"Doesn't mention {expected[0]} by name — check the greeting.")

    words = len(body.split())
    limit = 150 if kind == "followup" else (200 if is_market else 240)
    if words > limit:
        add("warn", f"{words} words — long for a {'follow-up' if kind == 'followup' else 'cold email'}.")
    if kind == "initial" and len(subject.split()) > 12:
        add("warn", "Subject is long — under ~9 words usually does better.")

    known = " ".join(
        [
            " ".join([(p or {}).get("pitch", "") + " " + " ".join((p or {}).get("props", [])) for p in pitches.values()]),
            contact.get("notes") or "",
            market.get("value_prop_brands", "") + " " + market.get("value_prop_professionals", "") + " " + market.get("trial_text", ""),
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
    if is_market:
        marketplace_checks(text, contact, market, add)
    if (not is_market or marketplace.is_doctor(contact)) and (m := INCENTIVE_RE.search(_without(text, (market.get("trial_text") or "") if market.get("mention_trial") else ""))):
        add("error", f"Offers an incentive (\"{m.group(0)}\") — gifts, fees or perks to doctors breach NMC 2023 / UCPMP 2024.")
    if m := CURE_RE.search(text):
        add("error", f"Makes a cure/guarantee claim (\"{m.group(0)}\") — not allowed (Drugs & Magic Remedies Act, ASCI).")
    if m := ENDORSE_RE.search(text):
        add("error" if ctype in ("doctor", "partner", "professional") else "warn",
            f"Asks for an endorsement or prescribing (\"{m.group(0)}\") — doctors may not endorse products (NMC 2023).")
    if m := CLINICAL_CLAIM_RE.search(text):
        add("warn", f"Sounds like a clinical claim (\"{m.group(0)}\") — Dr.Tracker helps doctors notice sooner; it doesn't diagnose or treat.")
    if m := SUPERLATIVE_RE.search(text):
        add("error" if is_market else "warn", f"Unsubstantiated superlative (\"{m.group(0)}\") — ASCI requires claims you can back up.")
    if m := FEAR_RE.search(body):
        add("warn", f"Fear-based wording (\"{m.group(0)}\") — ASCI discourages exploiting fear; keep the scene calm.")
    if contact.get("needs_check"):
        add("warn", f"Flagged in the sheet for a check before sending: {contact['needs_check']}")
    if kind == "initial" and ctype == "doctor":
        # Greeting, then the story paragraphs; the product belongs only in the paragraph before the sign-off.
        paras = [p for p in re.split(r"\n\s*\n", body) if p.strip()]
        story = paras[1:3] if len(paras) >= 5 else paras[1:2]
        if any("dr.tracker" in p.lower() for p in story):
            add("warn", "Dr.Tracker is named in the opening — lead with the story and save the offer for the last paragraph.")
    return out


def has_errors(issues: list[dict]) -> bool:
    return any(i["level"] == "error" for i in issues)
