"""Specialty detection and per-specialty writing material.

`detect(contact)` works out a doctor's specialty from whatever the spreadsheet
gave us — the specialty column, degrees ("MD, DM (Cardiology)"), the clinic
name ("Heart Care Centre"), notes, even the email handle ("drcardio@") — and
returns a canonical key. Each key has a playbook: the between-visit moments
that are real for that specialty, catchy subject lines, and what its patients
typically keep an eye on. Drafts are built from the playbook, so a cardiologist
and a dermatologist get genuinely different emails.

All scenes are hypothetical and generic — never a real patient.
"""
import hashlib
import re
from dataclasses import dataclass, field


@dataclass
class Playbook:
    label: str
    patterns: list[str]            # regexes matched against specialty text (strongest signal)
    org_patterns: list[str]        # regexes matched against clinic name / notes / email (weaker)
    watch: str                     # what patients of this specialty keep an eye on between visits
    moments: list[str]             # short, present-tense between-visit scenes
    gaps: list[str]                # the line that names the problem, in the doctor's own words
    subjects: list[str]            # catchy, honest subjects; subjects[i] goes with moments[i]
    audience: str = "patients"     # "parents" for paediatrics, etc.
    extra: dict = field(default_factory=dict)


PLAYBOOKS: dict[str, Playbook] = {
    "cardiology": Playbook(
        "Cardiology",
        [r"cardi", r"\bheart\b", r"\bcvts?\b", r"electrophysiolog", r"interventional"],
        [r"heart", r"cardi", r"\bhridaya", r"\bdil\b"],
        "blood pressure, heart rate, breathlessness, swelling in the feet, chest discomfort",
        [
            "Two days after her review, a patient notices she's a little more breathless on the stairs. She tells herself it's the weather. By the time she calls, it's been a week.",
            "A man on a new beta-blocker feels light-headed every morning. He doesn't want to trouble you, so he skips a dose instead — and doesn't mention it until his next visit.",
            "His ankles have been a little puffier each evening this week. He's sure it's nothing. He'll bring it up in March.",
        ],
        [
            "You know the stretch between visits is where most of the story happens — you just rarely get to hear it while it's happening.",
            "The patients who worry you aren't the ones who call. They're the ones who don't.",
        ],
        ["She called three days too late", "The dose he quietly skipped", "His ankles were telling you something", "What happens between your cardiac reviews?"],
    ),
    "orthopaedics": Playbook(
        "Orthopaedics",
        [r"orth", r"\bspine\b", r"joint replacement", r"arthroplast", r"sports medicine", r"trauma surg"],
        [r"bone", r"joint", r"ortho", r"spine", r"\bknee\b", r"\bhip\b"],
        "pain scores, swelling, wound healing, mobility, physio exercises done or skipped",
        [
            "Ten days after a knee replacement, a patient notices the joint is warmer than it should be. Recovery is supposed to hurt, she reasons. Four days later she's back in your OPD with something that would have been simple on day one.",
            "Your post-op patient stopped doing his physio on day six because it hurt. He'll tell you at the three-week review — when the stiffness has already set in.",
            "A small patch of redness appears near the incision. She takes a photo, thinks about sending it, and decides she's overreacting.",
        ],
        [
            "Post-op recovery is where orthopaedic care goes quiet — and where the only way you find out something's wrong is if they call.",
            "The surgery went perfectly. The weeks after it are the part you can't see.",
        ],
        ["Day ten, and her knee felt warm", "He stopped his physio on day six", "The photo she almost sent you", "What your post-op patients don't tell you"],
    ),
    "obgyn": Playbook(
        "Obstetrics & Gynaecology",
        [r"gyn(a)?ec", r"obst", r"\bob[/-]?gyn", r"\bivf\b", r"fertil", r"materni", r"\bmfm\b", r"perinat"],
        [r"women", r"mother", r"matern", r"\bivf\b", r"fertil", r"stree", r"janani", r"\bnari\b"],
        "blood pressure, headaches, swelling, foetal movements, bleeding, sugar readings",
        [
            "In her third trimester, a patient has a headache that won't quite go away and her rings feel tight. She doesn't want to be the anxious one, so she waits for Thursday's appointment.",
            "She's felt the baby move a little less since yesterday. Probably just a quiet day, she thinks, and doesn't call.",
            "Her home sugar readings have crept up all week. She writes them in a notebook she plans to show you next month.",
        ],
        [
            "Between antenatal visits, the small signs that matter most are exactly the ones patients talk themselves out of.",
            "You've told her to call if anything feels off. She's decided nothing feels off enough.",
        ],
        ["Her rings felt tight on Tuesday", "A quieter day than usual", "She wrote it in a notebook for next month", "The call she almost made"],
    ),
    "endocrinology": Playbook(
        "Endocrinology & Diabetes",
        [r"endocrin", r"diabet", r"thyroid", r"metabol", r"\bhba1c\b"],
        [r"diabet", r"sugar", r"thyroid", r"endocr", r"madhumeh"],
        "sugar readings, insulin doses, hypo episodes, weight, thyroid symptoms",
        [
            "His sugars have been creeping up for ten days. He notices, shrugs, and decides to mention it at his next visit — three weeks away.",
            "She had two mild hypos this week after you adjusted her insulin. She quietly cut the dose herself instead of calling.",
            "He's been checking his sugar every morning, just like you asked. The readings live in a phone gallery nobody will ever scroll through.",
        ],
        [
            "You make the adjustment in clinic, and then you wait three months to learn how it went.",
            "Your patients are already measuring. The numbers just never reach you in time to matter.",
        ],
        ["His sugars crept up for ten days", "The hypo she handled on her own", "The readings nobody sees", "Three months is a long time to wait for a number"],
    ),
    "pulmonology": Playbook(
        "Pulmonology",
        [r"pulmon", r"chest", r"respir", r"\basthma\b", r"\bcopd\b", r"\btb\b", r"sleep medicine"],
        [r"lung", r"chest", r"breath", r"respir", r"\bswas"],
        "breathlessness, inhaler use, oxygen saturation, cough, peak flow",
        [
            "A patient with COPD is using his reliever a little more every day. He blames the pollution and waits it out.",
            "Her saturation reads 92 on the home oximeter, then 91. She isn't sure what number is supposed to worry her, so she doesn't call.",
            "His cough has changed this week — deeper, more tired. He'll mention it if it's still there next month.",
        ],
        [
            "Flare-ups rarely arrive without warning. The warnings just arrive at home, where you can't see them.",
            "By the time breathlessness becomes a phone call, it's usually become an admission.",
        ],
        ["He blamed it on the pollution", "92, then 91 — and no one to tell", "The cough that changed this week", "The inhaler he kept reaching for"],
    ),
    "neurology": Playbook(
        "Neurology",
        [r"neuro(?!.*surg)", r"epilep", r"stroke", r"movement disorder", r"headache clinic"],
        [r"neuro", r"brain", r"stroke", r"epilep"],
        "seizure frequency, headaches, weakness or tingling, medication side effects, sleep",
        [
            "She's had two small seizures since her dose was changed. Both short, both at night. She'll tell you at her next review — if she remembers the dates.",
            "The tingling in his left hand comes and goes for a few days. He isn't sure it's worth a call, so he doesn't make one.",
            "Her headaches have shifted from evenings to mornings. It seems like a small detail. It isn't.",
        ],
        [
            "Neurology runs on patterns, and the patterns happen at home, between visits, in memories that blur by the time they reach your OPD.",
            "Your patients are keeping diaries in their heads. Most of the entries never make it to you.",
        ],
        ["The two seizures she'll tell you about later", "His hand tingled for three days", "Her headaches moved to the mornings", "The pattern you only see in hindsight"],
    ),
    "nephrology": Playbook(
        "Nephrology",
        [r"nephro", r"kidney", r"renal", r"dialysis", r"transplant"],
        [r"kidney", r"renal", r"nephro", r"dialysis"],
        "weight, blood pressure, fluid intake, swelling, urine output",
        [
            "Between dialysis sessions, he's put on two kilos. He thought the extra chai wouldn't matter.",
            "Her blood pressure has been higher every evening this week. She's written it down for her next visit, in three weeks.",
            "His feet are more swollen than usual, but he's not sure how much is too much.",
        ],
        [
            "The numbers that tell you a kidney patient is drifting are measured at home, every day — and seen by you once a month.",
            "Most of what goes wrong for your patients shows up days before anyone picks up a phone.",
        ],
        ["Two kilos between sessions", "Her evening BP, written down for later", "The swelling he wasn't sure about", "What the numbers said last Tuesday"],
    ),
    "gastroenterology": Playbook(
        "Gastroenterology & Hepatology",
        [r"gastro", r"hepat", r"liver", r"\bgi\b", r"endoscop", r"\bibd\b", r"colorect"],
        [r"gastro", r"liver", r"digest", r"\bgut\b", r"\bpet\b"],
        "abdominal pain, stools, weight, appetite, medication tolerance",
        [
            "His flare started quietly on Monday. By Friday he's barely eating, but he's still hoping it settles on its own.",
            "She's been more tired and her appetite's dropped since the new tablets. She assumes it's normal and says nothing.",
        ],
        [
            "Flares build for days before they become an emergency — and patients spend most of those days hoping.",
            "You hear about the bad week after it's over.",
        ],
        ["His flare started on Monday", "What changed after the new tablets", "The week she hoped it would settle"],
    ),
    "oncology": Playbook(
        "Oncology",
        [r"oncol", r"cancer", r"haemato-?onc", r"radiation", r"chemo"],
        [r"cancer", r"onco", r"tumou?r"],
        "fever, fatigue, nausea, appetite, side effects between cycles",
        [
            "Between chemo cycles, he's more tired than last time and has a low-grade fever. He assumes it's expected, and waits for his next appointment.",
            "She's been too nauseous to eat properly for three days. She doesn't want to seem like she's complaining.",
        ],
        [
            "The days between cycles are when patients feel most alone — and when small symptoms matter most.",
            "Your patients are brave about exactly the things you'd want them to report.",
        ],
        ["The fever he thought was expected", "Three days of not eating", "Between cycles, he decided to wait"],
    ),
    "paediatrics": Playbook(
        "Paediatrics",
        [r"p(a)?ediat", r"neonat", r"\bchild\b", r"\bnicu\b"],
        [r"child", r"kids?\b", r"baby", r"bal\b", r"shishu", r"rainbow"],
        "fever, feeding, hydration, rashes, breathing, growth",
        [
            "A mother notices the fever has come back on the third evening. It's late, she doesn't want to overreact, so she decides to wait and see.",
            "The baby's feeding a little less today. His father isn't sure if that's a real worry or a first-time-parent worry.",
        ],
        [
            "Parents are the best observers you have — and the most reluctant to call.",
            "Between visits, every parent is making judgement calls on your behalf.",
        ],
        ["The fever came back on the third night", "First-time parent, second-guessing", "She didn't want to overreact"],
        audience="parents",
    ),
    "psychiatry": Playbook(
        "Psychiatry",
        [r"psychiat", r"mental health", r"de-?addiction", r"\bpsych\b"],
        [r"mind", r"mental", r"psych", r"manas"],
        "mood, sleep, medication side effects, adherence",
        [
            "Three weeks into a new medication, she's sleeping badly and feels flat. She thinks it means the tablets aren't working, and quietly stops them.",
            "His sleep has slipped from seven hours to four over two weeks. He doesn't connect it to anything.",
        ],
        [
            "The weeks after a medication change are the ones you most want to hear about, and the ones patients are least likely to share.",
            "Small changes in sleep and mood show up long before a crisis does.",
        ],
        ["She stopped the tablets on her own", "Seven hours of sleep became four", "The weeks after a dose change"],
    ),
    "dermatology": Playbook(
        "Dermatology",
        [r"dermat", r"\bskin\b", r"cosmetolog", r"venereolog", r"trichol"],
        [r"skin", r"derma", r"hair", r"glow"],
        "how a rash or lesion is changing, treatment side effects, adherence to a regimen",
        [
            "Ten days into the new cream, the redness is worse, not better. She isn't sure if that's the treatment working or the treatment failing, so she stops it.",
            "He took a photo of the patch on day one, meaning to compare. He never did.",
        ],
        [
            "In dermatology, the whole story is visual — and it happens at home, photographed on phones you never see.",
            "Most treatment plans don't fail in clinic. They quietly stop at home in week two.",
        ],
        ["She stopped the cream on day ten", "The photo he meant to send", "Is it working? She wasn't sure"],
    ),
    "general_medicine": Playbook(
        "General / Internal Medicine",
        [r"general medicine", r"internal medicine", r"\bphysician\b", r"family (medicine|physician)", r"\bgp\b", r"\bmbbs\b", r"general practi", r"\bmd\s*\(?med", r"geriatric"],
        [r"polyclinic", r"family clinic", r"general hospital", r"health ?care"],
        "blood pressure, sugar, fever, new symptoms, medication side effects",
        [
            "A patient leaves your clinic feeling fine. Three days later, something feels slightly off — a little dizzy, a little tired. She waits. It's probably nothing.",
            "He's been getting headaches since the new BP tablets. He's not sure it's related, so he just takes a painkiller.",
        ],
        [
            "You see your patients for ten minutes. The rest of their health happens in the months in between.",
            "The patients who worry you most aren't the ones who call.",
        ],
        ["It's probably nothing, she thought", "The headache since the new tablets", "What happens after they leave your clinic?"],
    ),
    "general_surgery": Playbook(
        "Surgery",
        [r"surg(eon|ery)", r"laparoscop", r"\bbariatric\b", r"plastic"],
        [r"surg", r"laparo"],
        "wound healing, pain, fever, drain output, mobility after surgery",
        [
            "Four days after a laparoscopic repair, a patient's wound is a little red and she has a mild fever. She's been told some discomfort is normal, so she waits.",
            "He was discharged on Friday. By Sunday the pain is worse, not better, but the clinic is closed and he doesn't want to go to the ER for 'nothing'.",
        ],
        [
            "The operation is the part you control. The ten days after it are the part you hear about too late.",
            "Discharge is where surgical care goes quiet.",
        ],
        ["Some discomfort is normal, she thought", "Discharged Friday, worse by Sunday", "The ten days after surgery"],
    ),
    "ophthalmology": Playbook(
        "Ophthalmology",
        [r"ophthal", r"\beye\b", r"retina", r"cornea", r"glaucoma", r"cataract"],
        [r"\beye", r"vision", r"netra", r"drishti", r"sight"],
        "vision changes, pain, redness, drop adherence after surgery",
        [
            "Two days after cataract surgery, the eye is a little more painful and the vision a little hazier. She assumes healing just feels like this.",
            "He's been skipping his evening glaucoma drops because they sting. You'll find out when the pressure's up.",
        ],
        [
            "Most eye problems after surgery give a warning. The warning just happens at home.",
            "Drop adherence is the invisible half of every treatment plan.",
        ],
        ["Day two, and it was a little hazier", "The drops he skipped because they sting", "She assumed healing felt like this"],
    ),
    "ent": Playbook(
        "ENT",
        [r"\bent\b", r"otolaryng", r"otorhino", r"head and neck"],
        [r"\bent\b", r"ear", r"nose", r"throat"],
        "pain, bleeding, fever and breathing after procedures",
        [
            "A week after a tonsillectomy, there's a small streak of blood when he coughs. His mother isn't sure whether that's normal.",
        ],
        ["Post-procedure worries rarely make it to your phone until they're emergencies."],
        ["A streak of blood on day seven", "His mother wasn't sure it was normal"],
    ),
    "urology": Playbook(
        "Urology",
        [r"urolog", r"androlog", r"prostat"],
        [r"uro", r"kidney stone", r"prostate"],
        "pain, urine changes, catheter or stent issues, fever",
        ["His stent's been uncomfortable for a few days and there's a little fever tonight. He figures it'll settle by morning."],
        ["Stents, catheters and stones all give warnings — usually at 11 pm, at home."],
        ["He figured it would settle by morning", "The fever with the stent"],
    ),
    "rheumatology": Playbook(
        "Rheumatology",
        [r"rheumat", r"arthrit", r"\blupus\b", r"autoimmun"],
        [r"arthrit", r"rheum", r"joint"],
        "joint pain and stiffness, flares, side effects, adherence",
        ["Her morning stiffness has gone from twenty minutes to two hours over a fortnight. She's decided to just push through until her next appointment."],
        ["Flares build slowly, and patients are very good at pushing through them."],
        ["Twenty minutes of stiffness became two hours", "She decided to push through"],
    ),
}

GENERIC = PLAYBOOKS["general_medicine"]


def _text(*parts) -> str:
    return " ".join(p for p in parts if p).lower()


def detect(contact: dict) -> tuple[str | None, str]:
    """(specialty_key or None, where it came from). Only for doctor contacts."""
    specialty = contact.get("specialty") or ""
    name = contact.get("name") or ""
    sources = [
        ("specialty", _text(specialty), "patterns"),
        ("name/degrees", _text(name), "patterns"),
        ("notes", _text(contact.get("notes")), "patterns"),
        ("clinic name", _text(contact.get("clinic")), "org_patterns"),
        ("email", _text((contact.get("email") or "").split("@")[0].replace(".", " ")), "org_patterns"),
        ("notes", _text(contact.get("notes")), "org_patterns"),
    ]
    for source, text, attr in sources:
        if not text:
            continue
        # Surgeons with an organ-specific specialty (e.g. cardiothoracic) belong to that specialty,
        # so check every specific playbook before the generic surgery/medicine ones.
        ordered = [k for k in PLAYBOOKS if k not in ("general_surgery", "general_medicine")] + ["general_surgery", "general_medicine"]
        for key in ordered:
            if any(re.search(p, text) for p in getattr(PLAYBOOKS[key], attr)):
                return key, source
    return None, ""


def playbook(key: str | None) -> Playbook:
    return PLAYBOOKS.get(key or "", GENERIC)


def label(key: str | None) -> str:
    return PLAYBOOKS[key].label if key in PLAYBOOKS else ""


def pick(items: list[str], seed: str, salt: str = "") -> str:
    """Stable choice per contact, so regenerating a template draft doesn't shuffle it but contacts vary."""
    if not items:
        return ""
    h = int(hashlib.sha1((seed + salt).encode()).hexdigest(), 16)
    return items[h % len(items)]


def pick_index(n: int, seed: str) -> int:
    return int(hashlib.sha1(seed.encode()).hexdigest(), 16) % n if n else 0


def choices() -> list[dict]:
    return [{"key": k, "label": p.label} for k, p in PLAYBOOKS.items()]
