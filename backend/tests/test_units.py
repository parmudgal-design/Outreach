from datetime import datetime

from backend.app.services import importer, inbox, qa, schedule
from backend.app.services.drafting import TemplateDrafter
from backend.app.settings_store import DEFAULTS

SENDER = {"name": "Ananya Gupta", "role": "Founder", "phone": "+91 1", "tone": "warm", "address": "Bengaluru"}


def test_importer_guesses_columns_and_types():
    csv_bytes = (
        "Full Name,E-mail,Speciality,Hospital,City,Category,Remarks\n"
        "Dr. Aditi Sharma,ADITI@x.in,Cardiology,Apollo,Pune,Doctor,busy OPD\n"
        "Navjeevan,ms@nav.in,Medical Superintendent,Navjeevan,Delhi,hospital,\n"
        "Bad Row,not-an-email,,,,,\n"
    ).encode()
    headers, rows = importer.read_table("list.csv", csv_bytes)
    mapping = importer.guess_mapping(headers, rows)
    assert mapping["name"] == 0 and mapping["email"] == 1 and mapping["specialty"] == 2 and mapping["clinic"] == 3
    assert mapping["type"] == 5 and mapping["notes"] == 6
    contacts, problems = importer.to_contacts(headers, rows, mapping)
    assert [c["email"] for c in contacts] == ["aditi@x.in", "ms@nav.in"]
    assert [c["type"] for c in contacts] == ["doctor", "hospital"]
    assert problems == [{"row": 4, "name": "Bad Row", "reason": "invalid email 'not-an-email'"}]


def test_importer_xlsx_and_first_last_names(tmp_path):
    import openpyxl

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["First Name", "Last Name", "Contact", "Department"])
    ws.append(["Rahul", "Nair", "rahul@x.in", "Orthopaedics"])
    path = tmp_path / "l.xlsx"
    wb.save(path)
    headers, rows = importer.read_table("l.xlsx", path.read_bytes())
    mapping = importer.guess_mapping(headers, rows)
    contacts, _ = importer.to_contacts(headers, rows, mapping)
    assert contacts[0]["name"] == "Rahul Nair" and contacts[0]["email"] == "rahul@x.in"
    assert contacts[0]["specialty"] == "Orthopaedics"


def test_qa_flags_problems():
    contact = {"name": "Dr. Aditi Sharma", "notes": ""}
    issues = qa.check("Hi", "Hello [Name], we help 5000 doctors cut readmissions 40%. Limited time!", contact, SENDER, DEFAULTS["pitches"])
    levels = {i["message"].split()[0]: i["level"] for i in issues}
    assert qa.has_errors(issues)
    assert any("placeholder" in i["message"] for i in issues)
    assert any("Sharma" in i["message"] for i in issues)
    assert any("figures" in i["message"] for i in issues)
    assert any("pressure" in i["message"] for i in issues)
    assert levels


def test_template_draft_passes_qa_for_every_type():
    for ctype in ("doctor", "hospital", "company", "partner"):
        contact = {"name": "Dr. Rahul Nair", "type": ctype, "specialty": "Orthopaedics", "clinic": "Sunrise", "notes": ""}
        d = TemplateDrafter().initial(contact, SENDER, DEFAULTS["pitches"])
        issues = qa.check(d.subject, d.body, contact, SENDER, DEFAULTS["pitches"])
        assert not qa.has_errors(issues), (ctype, issues)
        assert "Nair" in d.body and "P.S." in d.body


def test_ist_window():
    camp = DEFAULTS["campaign"]
    ist = schedule.IST
    assert schedule.in_window(camp, datetime(2026, 9, 30, 11, 0, tzinfo=ist).timestamp())      # Wed 11:00
    assert not schedule.in_window(camp, datetime(2026, 9, 30, 18, 30, tzinfo=ist).timestamp())  # Wed 18:30
    assert not schedule.in_window(camp, datetime(2026, 10, 4, 12, 0, tzinfo=ist).timestamp())   # Sunday
    nxt = schedule.next_window_open(camp, datetime(2026, 10, 3, 19, 0, tzinfo=ist).timestamp())  # Sat evening
    assert nxt.weekday() == 0 and nxt.hour == 10                                                # -> Monday 10:00


def _raw(headers: dict, body: str) -> bytes:
    return ("".join(f"{k}: {v}\r\n" for k, v in headers.items()) + "\r\n" + body).encode()


def test_inbox_classification():
    reply = _raw({"From": "Aditi <aditi@x.in>", "Subject": "Re: She called", "In-Reply-To": "<abc@drtracker.test>"},
                 "Sounds interesting, Thursday works.\n\nOn Mon, Ananya wrote:\n> unsubscribe")
    v = inbox.classify(reply)
    assert v["kind"] == "reply" and v["refs"] == ["<abc@drtracker.test>"] and "Thursday" in v["snippet"]

    unsub = _raw({"From": "aditi@x.in", "Subject": "Re: hi"}, "Please remove me from your list.")
    assert inbox.classify(unsub)["kind"] == "unsubscribe"

    ooo = _raw({"From": "aditi@x.in", "Subject": "Automatic reply: She called", "Auto-Submitted": "auto-replied"}, "I'm away")
    assert inbox.classify(ooo)["kind"] == "auto_reply"

    bounce = _raw({"From": "Mail Delivery Subsystem <mailer-daemon@googlemail.com>", "Subject": "Delivery Status Notification (Failure)"},
                  "Final-Recipient: rfc822; gone@x.in\nMessage-ID: <abc@drtracker.test>")
    v = inbox.classify(bounce)
    assert v["kind"] == "bounce" and v["recipients"] == ["gone@x.in"] and "<abc@drtracker.test>" in v["refs"]


def test_specialty_detection_from_many_signals():
    from backend.app.services.specialties import detect

    cases = [
        ({"specialty": "MD, DM (Cardiology)"}, "cardiology"),
        ({"specialty": "Consultant Gynaecologist & Obstetrician"}, "obgyn"),
        ({"specialty": "", "clinic": "Kochi Bone & Joint Centre"}, "orthopaedics"),
        ({"specialty": "", "clinic": "Sugar Care Diabetes Clinic"}, "endocrinology"),
        ({"name": "Dr. Meera Iyer, MBBS, DCH (Paediatrics)"}, "paediatrics"),
        ({"specialty": "Cardiothoracic surgeon"}, "cardiology"),
        ({"specialty": "General Surgeon"}, "general_surgery"),
        ({"specialty": "", "email": "drskinclinic@x.in"}, "dermatology"),
        ({"specialty": "", "notes": "Runs a busy retina practice"}, "ophthalmology"),
        ({"specialty": "Physician"}, "general_medicine"),
        ({"specialty": "", "clinic": "City Carpets"}, None),
    ]
    for contact, expected in cases:
        assert detect(contact)[0] == expected, (contact, detect(contact))


def test_each_specialty_gets_its_own_email():
    from backend.app.services import specialties

    bodies = set()
    for key in specialties.PLAYBOOKS:
        contact = {"name": "Dr. Rahul Nair", "type": "doctor", "specialty_key": key, "email": f"{key}@x.in", "notes": ""}
        d = TemplateDrafter().initial(contact, SENDER, DEFAULTS["pitches"])
        issues = qa.check(d.subject, d.body, contact, SENDER, DEFAULTS["pitches"])
        assert not qa.has_errors(issues) and not issues, (key, issues)
        paras = [p for p in d.body.split("\n\n") if p.strip()]
        # Dr.Tracker appears only in the offer paragraph (the one before the sign-off).
        assert [i for i, p in enumerate(paras) if "Dr.Tracker" in p and "Founder" not in p] == [3], (key, paras)
        assert "Dr.Tracker" not in d.subject
        bodies.add(paras[1])
    assert len(bodies) == len(specialties.PLAYBOOKS)


def test_partner_pitch_is_product_specific_and_compliant():
    contact = {"name": "Neha Kapoor", "type": "partner", "clinic": "VitalSense", "product": "VitalSense BP cuff", "email": "n@v.in"}
    d = TemplateDrafter().initial(contact, SENDER, DEFAULTS["pitches"])
    assert "VitalSense BP cuff" in d.body and "Dear Neha," in d.body
    assert not qa.check(d.subject, d.body, contact, SENDER, DEFAULTS["pitches"])


def test_indian_guardrails():
    contact = {"name": "Dr. Aditi Sharma", "type": "doctor"}
    def msgs(subject, body):
        return [(i["level"], i["message"]) for i in qa.check(subject, "Namaste Dr. Sharma,\n\n" + body, contact, SENDER, DEFAULTS["pitches"])]
    assert any(l == "error" and "Re:/Fwd:" in m for l, m in msgs("Re: our chat", "hello"))
    assert any(l == "error" and "NMC" in m for l, m in msgs("Hi", "Join and get a referral fee for every patient."))
    assert any(l == "error" and "cure" in m for l, m in msgs("Hi", "Dr.Tracker is guaranteed to cut complications."))
    assert any(l == "error" and "endorse" in m for l, m in msgs("Hi", "We'd love you to endorse Dr.Tracker."))
    assert any(l == "warn" and "ASCI" in m for l, m in msgs("Hi", "India's first remote monitoring app."))
    assert any(l == "warn" and "clinical claim" in m for l, m in msgs("Hi", "It will diagnose problems early."))
