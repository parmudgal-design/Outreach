"""Marketplace pitch: brand and professional contacts, greetings, templates, prompts and checks.
All contacts are fake (backend/tests/fixtures/)."""
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.app.services import drafting, greeting, importer, marketplace, prompts, qa, spend, templates
from backend.app.services.drafting import TemplateDrafter
from backend.app.settings_store import DEFAULTS

FIXTURES = Path(__file__).parent / "fixtures"
SENDER = {"name": "Ananya Gupta", "role": "Founder, Dr.Tracker", "phone": "+91 98765 43210", "tone": "warm",
          "address": "HSR Layout, Bengaluru 560102"}
# The brand guide (brand_outreach_agent.md) won't write without a call link.
MARKET = {**DEFAULTS["marketplace"], "meeting_link": "https://cal.example/drtracker"}
# ...or without spend figures, which the sample sheet doesn't have.
SPEND = {"spend": "18-25", "spend_source": "ad library estimate"}


def load(name: str) -> list[dict]:
    headers, rows = importer.read_table(name, (FIXTURES / name).read_bytes())
    contacts, _ = importer.to_contacts(headers, rows, importer.guess_mapping(headers, rows))
    return contacts


CONTACTS = {c["category"]: ({**c, **SPEND} if c["type"] == "brand" else c) for c in load("marketplace_contacts.csv")}
# The sample's care@ inbox is one the brand guide says not to write to (tested on its own below).
CONTACTS["SUP"] = {**CONTACTS["SUP"], "email": "partners@sunleaf.test"}

# category -> expected greeting line (brands: the brand guide's "Hi {Brand} team,")
GREETINGS = {
    "FD": "Hi Crunchwell Foods team,",
    "SUP": "Hi SUNLEAF team,",
    "PRO": "Hi LiftFuel Nutrition team,",
    "DV": "Hi VitalSense Devices team,",
    "NT": "Dear Meenal,",
    "GP": "Dear Dr. Menon,",
    "SPC": "Hello Kenia Eye Hospital team,",
    "MH": "Dear Pooja,",
    "PT": "Hello Total Health Physio & Sports team,",
}


def draft(contact, market=MARKET):
    d = TemplateDrafter().initial(contact, SENDER, DEFAULTS["pitches"], market)
    return d, qa.check(d.subject, d.body, contact, SENDER, DEFAULTS["pitches"], "initial", market)


# --- Import -------------------------------------------------------------------

def test_import_simple_csv_layout():
    assert set(CONTACTS) == set(GREETINGS)  # one contact per category
    sup, gp = CONTACTS["SUP"], CONTACTS["GP"]
    assert sup["type"] == "brand" and gp["type"] == "professional"  # "B" and "P" accepted
    assert sup["specialty"] == "Supplements" and sup["description"] == "Vitamin D3 and B12 gummies for adults"
    assert sup["partner_id"] == "B-SUP-T2-MUM-02" and sup["tier"] == "T2" and sup["notes"] == "Generic inbox"
    assert CONTACTS["FD"]["notes"] == ""  # the Partner ID tail isn't left in the notes


def test_import_founder_sheet_layout():
    contacts = {c["email"]: c for c in load("partners_sample.csv")}  # header on row 4, "Notes / Source" cell
    assert "No Email Foods" not in {c["name"] for c in contacts.values()}
    c = contacts["care@sunleaf.test"]
    assert c["type"] == "brand" and c["category"] == "SUP" and c["city"] == "Mumbai" and c["tier"] == ""
    assert c["website"] == "sunleaf.test" and c["source_url"] == "https://sunleaf.test"
    assert c["needs_check"] == "email is a generic care@ inbox"
    assert contacts["kavya@fitplate.test"]["description"].startswith("Diet plans for PCOS")


def test_category_from_label_or_specialty():
    assert marketplace.normalise_category("Nutritionist / dietitian", "professional") == "NT"
    assert marketplace.normalise_category("dietitian", "professional") == "NT"
    assert marketplace.normalise_category("Physio / trainer", "professional") == "PT"
    assert marketplace.guess_category("professional", "", "Cardiology") == "SPC"
    assert marketplace.normalise_category("Supplements", "professional") == ""  # wrong type


# --- Greetings ----------------------------------------------------------------

@pytest.mark.parametrize("name,ctype,expected", [
    ("Dr. Anjali Hooda (LiveNutrifit)", "professional", "Dear Dr. Hooda,"),
    ("Dt. Sridevi Jasti (Vibrant Living)", "professional", "Dear Sridevi,"),
    ("Pooja Makhija (Nourish)", "professional", "Dear Pooja,"),
    ("NOTO Healthy Ice Creams & Desserts", "brand", "Hello NOTO team,"),
    ("Kenia Eye Hospital", "professional", "Hello Kenia Eye Hospital team,"),
    ("Total Health Physio & Sports", "professional", "Hello Total Health Physio & Sports team,"),
    ("24 Seven Wellness", "professional", "Hello 24 Seven Wellness team,"),
    ("Sunleaf Nutraceuticals Pvt Ltd", "brand", "Hello Sunleaf Nutraceuticals team,"),
    ("Green Bowl (Dr. Kavya Rao)", "professional", "Dear Dr. Rao,"),
])
def test_greeting(name, ctype, expected):
    line = greeting.salutation(greeting.for_contact({"name": name, "type": ctype}))
    assert line == expected
    assert "Dear 24" not in line and "Dr. Sports" not in line


def test_dr_sports_never_appears_for_any_type():
    for ctype in ("doctor", "hospital", "company", "partner", "brand", "professional"):
        contact = {"name": "Total Health Physio & Sports", "type": ctype, "email": "info@x.test", "category": "PT", **SPEND,
                   "_write_anyway": True}  # a physio practice listed as a brand is skipped otherwise
        initial = TemplateDrafter().initial(contact, SENDER, DEFAULTS["pitches"], MARKET)
        followup = TemplateDrafter().followup(contact, SENDER, DEFAULTS["pitches"], {"subject": "s", "message_id": None}, 1, MARKET)
        for body in (initial.body, followup.body):
            assert "Dr. Sports" not in body and "Sports," not in body.splitlines()[0], (ctype, body)


@pytest.mark.parametrize("first_line", ["Dear 24,", "Dear ,", "Dear Dr. Sports,", "Hello undefined team,", "Dear None,", "Hi A,"])
def test_broken_greetings_block(first_line):
    contact = {"name": "Total Health Physio & Sports", "type": "professional", "category": "PT"}
    issues = qa.check("A subject", first_line + "\n\nBody text here.", contact, SENDER, DEFAULTS["pitches"], "initial", MARKET)
    assert qa.has_errors(issues), issues


def test_dr_for_an_organisation_blocks():
    contact = {"name": "Kenia Eye Hospital", "type": "professional", "category": "SPC"}
    issues = qa.check("A subject", "Dear Dr. Kenia,\n\nBody.", contact, SENDER, DEFAULTS["pitches"], "initial", MARKET)
    assert any('"Dr."' in i["message"] and i["level"] == "error" for i in issues)


# --- One template draft per category --------------------------------------------

@pytest.mark.parametrize("cat", list(GREETINGS))
def test_template_draft_per_category(cat):
    contact = CONTACTS[cat]
    d, issues = draft(contact)
    text = d.subject + "\n" + d.body
    assert d.body.splitlines()[0] == GREETINGS[cat]
    assert not qa.PLACEHOLDER_RE.search(templates.MARKER_RE.sub("", text))
    assert not qa.ARTEFACT_RE.search(qa._without_guide_wording(text, contact))   # the guide itself says "your product"
    assert not qa.MONITORING_RE.search(text)                                      # not the old doctor pitch
    assert not qa.PRICING_RE.search(qa.SHOPPER_PRICE_RE.sub("", qa._without_spend(text, contact.get("spend"))))  # trial is off
    if cat in ("GP", "SPC"):
        assert not qa.REFERRAL_RE.search(text)
    if contact["type"] == "brand":
        # The brand guide's touch 1: the bill subject, the estimated spend, the 3 steps, then the screenshots.
        assert d.subject == f"{GREETINGS[cat][3:-6]}, your marketing bill for ₹25 lakhs is ready"
        assert "your estimated range of spend on marketing was ₹18–25 lakhs" in d.body
        paras = d.body.split("\n\n")
        # The screenshots go inside the body: 1 after the hook, 2 and 3 after "But we can take your product…".
        assert "dance with it online" in paras[2] and paras[3] == "[SCREENSHOT_1]"
        assert paras[4].startswith("But we can take your product") and paras[5:7] == ["[SCREENSHOT_2]", "[SCREENSHOT_3]"]
        assert "1. Ask" not in d.body and "attached" not in d.body
        assert d.body.endswith("https://cal.example/drtracker\n\nAnanya Gupta\nFounder, Dr. Tracker Bharat")
        assert "Dr.Tracker" not in text
    else:
        assert contact["description"].lower()[:30] in d.body.lower()             # description used
        assert 120 <= len(d.body.split()) <= 170, len(d.body.split())
        assert MARKET["value_prop_professionals"] in d.body
    assert not issues, issues                                                     # QA passes cleanly


@pytest.mark.parametrize("cat", list(GREETINGS))
def test_template_without_description_still_reads_naturally(cat):
    contact = {**CONTACTS[cat], "description": ""}
    d, issues = draft(contact)
    assert "I was reading about" not in d.body
    if contact["type"] != "brand":  # the brand guide's own template says "your product"
        assert "your product" not in d.body
    assert not qa.has_errors(issues), issues


def test_description_with_claims_is_not_repeated():
    contact = {**CONTACTS["SUP"], "description": "India's No. 1 vitamin gummies, clinically proven"}
    d, issues = draft(contact)
    assert "No. 1" not in d.body and "clinically" not in d.body
    assert not qa.has_errors(issues)


def test_trial_text_only_when_switched_on():
    on = {**MARKET, "mention_trial": True}
    for cat in GREETINGS:
        off_draft, _ = draft(CONTACTS[cat])
        assert MARKET["trial_text"] not in off_draft.body
        d, issues = draft(CONTACTS[cat], on)
        # Brands get only the brand guide's wording, which has no trial.
        assert (MARKET["trial_text"] in d.body) == (CONTACTS[cat]["type"] != "brand")
        assert not qa.has_errors(issues), (cat, issues)


def test_value_prop_and_signature_come_from_settings():
    custom = {**MARKET, "value_prop_professionals": "Custom wording from the founder.", "signature_name": "Rohan Mehta",
              "signature_role": "Partnerships, Dr.Tracker", "meeting_link": "https://cal.example/drtracker"}
    d, _ = draft(CONTACTS["NT"], custom)
    assert "Custom wording from the founder." in d.body
    assert "Rohan Mehta\nPartnerships, Dr.Tracker" in d.body and "Ananya Gupta" not in d.body
    d, issues = draft(CONTACTS["FD"], custom)  # brands: the guide's sign-off, after the call link
    assert d.body.endswith("https://cal.example/drtracker\n\nRohan Mehta\nPartnerships, Dr. Tracker Bharat")
    assert not qa.has_errors(issues), issues


# --- Checks -----------------------------------------------------------------------

def body_with(text: str) -> str:
    return "Hello Kenia Eye Hospital team,\n\n" + text + "\n\nWarmly,\nAnanya Gupta"


def levels(issues, needle):
    return [i["level"] for i in issues if needle in i["message"]]


@pytest.mark.parametrize("artefact", ["your product", "[Name]", "{company}", "undefined", "None"])
def test_placeholder_artefacts_block(artefact):
    issues = qa.check("Subject", body_with(f"We'd love to feature {artefact} on Dr.Tracker."), CONTACTS["SPC"],
                      SENDER, DEFAULTS["pitches"], "initial", MARKET)
    assert qa.has_errors(issues), issues


@pytest.mark.parametrize("phrase", ["a 10% commission", "a referral fee", "payment per booking", "a revenue share"])
def test_commission_to_gp_and_specialist_blocks_even_with_trial_on(phrase):
    on = {**MARKET, "mention_trial": True}
    for cat in ("GP", "SPC"):
        issues = qa.check("Subject", body_with(f"We only take {phrase}."), CONTACTS[cat], SENDER, DEFAULTS["pitches"], "initial", on)
        assert "error" in levels(issues, "NMC"), (cat, phrase, issues)


def test_pricing_blocks_when_trial_is_off_and_only_trial_text_allowed_when_on():
    contact = CONTACTS["FD"]
    trial = MARKET["trial_text"]
    off = qa.check("Subject", body_with(trial), contact, SENDER, DEFAULTS["pitches"], "initial", MARKET)
    assert "error" in levels(off, "pricing")
    on = {**MARKET, "mention_trial": True}
    assert not levels(qa.check("Subject", body_with(trial), contact, SENDER, DEFAULTS["pitches"], "initial", on), "pricing")
    extra = qa.check("Subject", body_with(trial + " After that it's ₹999 per month."), contact, SENDER, DEFAULTS["pitches"], "initial", on)
    assert "error" in levels(extra, "pricing")


def test_old_monitoring_pitch_and_length_warn():
    issues = qa.check("Subject", body_with("Your patients check in between visits. " + "word " * 200), CONTACTS["NT"],
                      SENDER, DEFAULTS["pitches"], "initial", MARKET)
    assert "warn" in levels(issues, "patient-monitoring")
    assert "warn" in levels(issues, "words")


def test_needs_check_shows_as_warning():
    contact = {**CONTACTS["SPC"], "needs_check": "confirm the clinic owner's name"}
    d, issues = draft(contact)
    assert levels(issues, "confirm the clinic owner's name") == ["warn"]


# --- Claude prompts ------------------------------------------------------------------

def test_marketplace_prompt_reads_settings_and_rules():
    gp = CONTACTS["GP"]
    text = prompts.marketplace_initial(gp, SENDER, MARKET)
    assert "Greeting line to use, exactly: Dear Dr. Menon," in text
    assert gp["description"] in text and MARKET["value_prop_professionals"] in text
    assert "don't mention any price" in text and "NMC 2023" in text
    on = prompts.marketplace_initial(CONTACTS["FD"], SENDER, {**MARKET, "mention_trial": True})
    assert MARKET["trial_text"] in on and "NMC" not in on.split("PRICING:")[1].split("\n")[0]
    assert prompts.system_for(gp) is prompts.MARKETPLACE_SYSTEM
    assert prompts.system_for({"type": "doctor"}) is prompts.SYSTEM
    assert "between visits" not in prompts.MARKETPLACE_SYSTEM.split("Never use the doctor")[0]


def test_claude_drafter_sends_marketplace_system_prompt():
    seen = {}

    def create(**kwargs):
        seen.update(kwargs)
        block = SimpleNamespace(type="text", text='{"subject": "Food that fits the plan", "body": "Hello Crunchwell Foods team,"}')
        return SimpleNamespace(stop_reason="end_turn", content=[block])

    drafter = drafting.ClaudeDrafter("sk-test")
    drafter.client = SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(create=create)))
    d = drafter.initial(CONTACTS["NT"], SENDER, DEFAULTS["pitches"], MARKET)
    assert d.subject == "Food that fits the plan"
    assert seen["system"] == prompts.MARKETPLACE_SYSTEM  # professionals: no guide yet
    assert "Dear Meenal," in seen["messages"][0]["content"]


# --- Original types: no "your product" / "your organisation" fallbacks --------------------

def test_legacy_templates_rephrase_missing_fields():
    for contact in ({"name": "Neha Kapoor", "type": "partner", "email": "n@x.test"},
                    {"name": "Navjeevan", "type": "hospital", "email": "ms@x.test"},
                    {"name": "Ritu Varma", "type": "company", "email": "r@x.test"}):
        d = TemplateDrafter().initial(contact, SENDER, DEFAULTS["pitches"])
        issues = qa.check(d.subject, d.body, contact, SENDER, DEFAULTS["pitches"])
        assert "your product" not in d.body and "your organisation" not in d.body
        assert not qa.has_errors(issues), (contact, issues)


def test_legacy_template_uses_description():
    contact = {"name": "Ritu Varma", "type": "company", "clinic": "Sanjeevani Diagnostics", "email": "r@x.test",
               "description": "Diagnostics chain across Maharashtra"}
    d = TemplateDrafter().initial(contact, SENDER, DEFAULTS["pitches"])
    assert "Diagnostics chain across Maharashtra" in d.body


# --- End to end over the API ------------------------------------------------------------

def test_import_draft_and_review_over_api(configured):
    c = configured
    p = c.post("/api/import/preview", files={"file": ("m.csv", (FIXTURES / "marketplace_contacts.csv").read_bytes(), "text/csv")}).json()
    r = c.post("/api/import/commit", json={"token": p["token"], "mapping": p["mapping"]}).json()
    assert r["added"] == 9
    c.put("/api/settings/marketplace", json={"mention_trial": True})
    assert c.get("/api/settings").json()["marketplace"]["mention_trial"] is True
    from backend.app import worker
    for _ in range(3):
        worker.draft_stage()
    review = c.get("/api/review").json()
    # The sample sheet has no spend figures: they're estimated. SUNLEAF's care@ inbox is skipped (the guide's rule).
    assert len(review) == 8
    brands = {x["email"]: x for x in c.get("/api/contacts").json() if x["type"] == "brand"}
    assert "support or complaints inbox" in brands["care@sunleaf.test"]["last_error"]
    assert all(x["spend_source"].startswith(spend.SOURCE_PREFIX) for e, x in brands.items() if e != "care@sunleaf.test")
    for m in review:
        assert m["drafted_by"] == "template"
        assert (MARKET["trial_text"] in m["body"]) == (m["contact_type"] != "brand")
        assert not qa.has_errors(m["qa"]), (m["contact_name"], m["qa"])
    meta = c.get("/api/meta").json()
    assert {x["key"] for x in meta["categories"]["brand"]} == {"FD", "SUP", "PRO", "DV"}


def test_shipped_marketplace_wording_makes_no_unbacked_claims():
    for text in (DEFAULTS["marketplace"]["value_prop_brands"], DEFAULTS["marketplace"]["value_prop_professionals"]):
        assert not qa.claim_in(text) and not qa.PRICING_RE.search(text) and not qa.REFERRAL_RE.search(text)
        assert not re.search(r"\d", text)  # no user numbers or statistics
