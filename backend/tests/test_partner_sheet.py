"""The founder's workbook: multi-tab import, skip rules, sheet statuses and re-import."""
import io
from pathlib import Path

import openpyxl
import pytest

from backend.app import db, settings_store, worker
from backend.app.services import outreach, partner_sheet, qa

FIXTURE = Path(__file__).parent / "fixtures" / "partner_list_sample.xlsx"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def edited(changes: dict) -> bytes:
    """The fixture with some cells changed: {(tab, row, column letter): value}."""
    wb = openpyxl.load_workbook(FIXTURE)
    for (tab, row, col), value in changes.items():
        wb[tab][f"{col}{row}"] = value
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def upload(c, data: bytes | None = None):
    r = c.post("/api/import/preview", files={"file": ("partners.xlsx", data or FIXTURE.read_bytes(), XLSX)})
    assert r.status_code == 200, r.text
    return r.json()


def commit(c, preview):
    r = c.post("/api/import/commit", json={"token": preview["token"]})
    assert r.status_code == 200, r.text
    return r.json()


def contacts(c) -> dict:
    return {x["email"]: x for x in c.get("/api/contacts").json()}


def test_preview_reads_every_city_tab_and_reports_skips(app_client):
    p = upload(app_client)
    assert p["format"] == "partner_workbook"
    assert p["tabs"] == ["Hyderabad", "Delhi", "Mumbai", "Bengaluru"] and p["skipped_tabs"] == ["Legend"]
    assert p["row_count"] == 17
    assert p["counts"]["add"] == 10 and p["counts"]["update"] == 0
    assert p["counts"]["skipped_by_reason"] == {
        "helpline": 3, "no email": 1, "invalid email": 1, "duplicate email": 1, "unknown status": 1,
    }
    names = {s["name"] for s in p["skipped"] if s["reason"] == "helpline"}
    assert names == {"Tele-MANAS Hyderabad", "iCALL Psychosocial Support", "Sahara Support Line"}
    dup = next(s for s in p["skipped"] if s["reason"] == "duplicate email")
    assert dup["tab"] == "Mumbai" and "Hyderabad row 5" in dup["detail"]  # first occurrence wins
    assert app_client.get("/api/contacts").json() == []  # preview writes nothing


def test_import_maps_fields_types_and_sheet_statuses(app_client):
    commit(app_client, upload(app_client))
    cs = contacts(app_client)
    assert len(cs) == 10
    crunch = cs["hello@crunchwell.test"]
    assert (crunch["type"], crunch["category"], crunch["tier"], crunch["partner_id"], crunch["city"]) == \
        ("brand", "FD", "T2", "B-FD-T2-HYD-01", "Hyderabad")
    assert crunch["website"] == "crunchwell.test" and crunch["source_url"] == "https://crunchwell.test/about"
    assert crunch["description"].startswith("Millet snack bars") and crunch["phone"] == "+91 90000 00001"
    assert cs["info@sunleaf.test"]["needs_check"] == "generic inbox, find a named contact"
    assert cs["support@greenbowl.test"]["tier"] == "" and cs["kavya@greenplate.test"]["tier"] == "T3"  # TBD / from the ID
    assert cs["meera@fitroot.test"]["type"] == "professional" and cs["meera@fitroot.test"]["category"] == "NT"

    statuses = {e: x["status"] for e, x in cs.items()}
    assert statuses["info@sunleaf.test"] == "sent"                 # Contacted
    assert statuses["care@shreyaclinic.test"] == "replied"         # Replied
    assert statuses["support@greenbowl.test"] == "adopted"         # Interested
    assert statuses["info@totalhealthphysio.test"] == "unsubscribed"
    assert statuses["team@fitplate.test"] == "bounced"
    assert cs["info@totalhealthphysio.test"]["suppressed"] and cs["team@fitplate.test"]["suppressed"]
    assert {e for e, s in statuses.items() if s == "new"} == {
        "hello@crunchwell.test", "meera@fitroot.test", "arvind@bhallaclinic.test", "kavya@greenplate.test", "ritika@calmcorner.test",
    }


def test_only_not_contacted_rows_are_drafted_and_drafts_are_clean(configured):
    c = configured
    commit(c, upload(c))
    worker.tick()
    worker.tick()
    review = c.get("/api/review").json()
    assert {m["contact_email"] for m in review} == {
        "hello@crunchwell.test", "meera@fitroot.test", "arvind@bhallaclinic.test", "kavya@greenplate.test", "ritika@calmcorner.test",
    }
    for m in review:
        assert not qa.has_errors(m["qa"]), (m["contact_name"], m["qa"])
        first = m["body"].splitlines()[0]
        assert first in {"Hello Crunchwell Foods team,", "Dear Dr. Kulkarni,", "Dear Dr. Bhalla,", "Dear Kavya,", "Dear Ritika,"}, first
    assert c.post("/api/review/approve-clean").json()["approved"] == 5
    c.post("/api/campaign/start")
    for _ in range(5):
        worker.tick()
    assert len(c.get("/api/outbox").json()) == 5  # dry run


def test_reimport_updates_in_place_and_follows_the_rules(configured):
    c = configured
    commit(c, upload(c))
    with db.connect() as conn:  # Crunchwell has already been emailed
        cid = conn.execute("SELECT id FROM contacts WHERE email = 'hello@crunchwell.test'").fetchone()[0]
        conn.execute("INSERT INTO messages (contact_id, subject, body, status, created_at, updated_at, sent_at) "
                     "VALUES (?, 's', 'b', 'sent', 0, 0, 0)", (cid,))
        conn.execute("UPDATE contacts SET status = 'sent', last_sent_at = 0 WHERE id = ?", (cid,))

    data = edited({
        ("Hyderabad", 5, "G"): None,                              # phone emptied: must not erase
        ("Hyderabad", 5, "H"): "partnerships@crunchwell.test",    # email changed after sending: kept, flagged
        ("Hyderabad", 5, "I"): "Not contacted",                   # sheet status never overrides the app's
        ("Mumbai", 5, "A"): "B-SUP-T2-MUM-01",                    # TBD tier filled in: ID changes
        ("Mumbai", 5, "F"): "T2",
        ("Mumbai", 7, "A"): "P-NT-T2-MUM-01",                     # ID tier changed AND email changed, never emailed
        ("Mumbai", 7, "H"): "kavya.rao@greenplate.test",
        ("Mumbai", 6, "J"): "New notes for an opted-out contact",  # unsubscribed: untouched
        ("Bengaluru", 6, "I"): "Unsubscribed",                    # newly opted out in the sheet
        ("Bengaluru", 6, "J"): "Counselling psychologist; CHECK: confirm she still practises",
    })
    p = upload(c, data)
    assert p["counts"]["add"] == 0, p["adds"]
    assert p["counts"]["update"] == 3  # Greenbowl, Kavya, Ritika; Crunchwell only gets a flag
    opted = [s for s in p["skipped"] if s["reason"] == "opted out"]
    assert {s["name"] for s in opted} == {"Total Health Physio & Sports", "FitPlate Protein"}
    messages = " ".join(f["message"] for f in p["flags"])
    assert "kept the old address" in messages and "added to the opt-out list" in messages
    commit(c, p)

    cs = contacts(c)
    assert len(cs) == 10  # no duplicates
    crunch = cs["hello@crunchwell.test"]
    assert crunch["phone"] == "+91 90000 00001" and crunch["status"] == "sent"
    assert cs["support@greenbowl.test"]["partner_id"] == "B-SUP-T2-MUM-01" and cs["support@greenbowl.test"]["tier"] == "T2"
    assert "kavya@greenplate.test" not in cs and cs["kavya.rao@greenplate.test"]["partner_id"] == "P-NT-T2-MUM-01"
    assert cs["info@totalhealthphysio.test"]["description"] == "Sports physiotherapy and rehab, Mumbai HQ"
    ritika = cs["ritika@calmcorner.test"]
    assert ritika["status"] == "unsubscribed" and ritika["suppressed"]
    assert ritika["needs_check"] == "confirm she still practises"

    # Importing the same sheet again changes nothing.
    again = upload(c, data)
    assert again["counts"]["add"] == 0 and again["counts"]["update"] == 0


def test_reimport_fixes_legacy_contacts_and_replaces_stale_drafts(configured):
    """Contacts imported before the marketplace types existed (as 'partner') are corrected in place."""
    c = configured
    r = c.post("/api/contacts", json={"name": "Crunchwell Foods", "email": "hello@crunchwell.test", "type": "partner",
                                      "product": "Millet snack bars"}).json()
    old = c.post(f"/api/contacts/{r['id']}/draft").json()
    commit(c, upload(c))
    with db.connect() as conn:
        assert outreach.get_message(conn, old["id"])["status"] == "discarded"
        contact = outreach.get_contact(conn, r["id"])
    assert (contact["type"], contact["category"], contact["status"]) == ("brand", "FD", "new")
    worker.tick()
    fresh = next(m for m in c.get("/api/review").json() if m["contact_email"] == "hello@crunchwell.test")
    assert fresh["body"].startswith("Hello Crunchwell Foods team,") and "reading on her" not in fresh["body"]


def test_hand_edited_draft_is_kept_on_reimport(configured):
    c = configured
    commit(c, upload(c))
    cid = contacts(c)["meera@fitroot.test"]["id"]
    m = c.post(f"/api/contacts/{cid}/draft").json()
    c.put(f"/api/messages/{m['id']}", json={"body": m["body"] + "\n\nEdited by a person."})
    commit(c, upload(c, edited({("Hyderabad", 6, "J"): "Sports nutritionist"})))
    with db.connect() as conn:
        assert outreach.get_message(conn, m["id"])["status"] == "draft"


def test_blocklist_is_configurable(app_client):
    settings_store.update_section("import", {"blocked_patterns": ["helpline", "suicide", "tele-manas", "icall", "fitroot"]})
    p = upload(app_client)
    assert any(s["name"] == "Dr. Meera Kulkarni (FitRoot)" and s["reason"] == "helpline" for s in p["skipped"])


def test_pending_upload_survives_restart(app_client):
    p = upload(app_client)
    db.init()  # what a restart does
    assert commit(app_client, p)["added"] == 10
    assert app_client.post("/api/import/commit", json={"token": p["token"]}).status_code == 410  # used once


@pytest.mark.parametrize("pid, key", [
    ("B-SUP-T2-MUM-01", "B-SUP-MUM-01"), ("B-SUP-TBD-MUM-01", "B-SUP-MUM-01"), ("b-sup-t3-mum-01 ", "B-SUP-MUM-01"),
])
def test_partner_key_ignores_tier(pid, key):
    assert partner_sheet.partner_key(pid) == key


def test_simple_csv_still_uses_column_mapping(app_client):
    p = app_client.post("/api/import/preview", files={"file": ("x.csv", "name,email\nDr. A,a@x.in\n", "text/csv")}).json()
    assert p["format"] == "table" and p["mapping"]["email"] == 1
