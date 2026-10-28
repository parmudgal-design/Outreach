"""End to end: import a spreadsheet, draft, approve, send over real SMTP to a
local test server, then feed replies/bounces back through the inbox path."""
import email
import socket
import time
from email import policy

import pytest
from aiosmtpd.controller import Controller

from backend.app import settings_store, worker
from backend.app.services import inbox, outreach

SHEET = (
    "name,email,type,specialty,clinic,city,notes\n"
    "Dr. Aditi Sharma,aditi@x.in,doctor,Cardiology,Sharma Clinic,Pune,busy OPD\n"
    "Dr. Rahul Nair,rahul@x.in,doctor,Orthopaedics,Sunrise,Kochi,\n"
    "Dr. Gone Away,gone@x.in,doctor,Neurology,City,Delhi,\n"
    "Dr. Aditi Dup,ADITI@x.in,doctor,Cardiology,Other,Pune,\n"
)


class Sink:
    def __init__(self):
        self.messages = []

    async def handle_DATA(self, server, session, envelope):
        self.messages.append(envelope)
        return "250 OK"


@pytest.fixture()
def smtp_server():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    sink = Sink()
    controller = Controller(sink, hostname="127.0.0.1", port=port)
    controller.start()
    yield sink, port
    controller.stop()


def _import(c):
    r = c.post("/api/import/preview", files={"file": ("list.csv", SHEET, "text/csv")})
    assert r.status_code == 200, r.text
    p = r.json()
    assert p["row_count"] == 4
    r = c.post("/api/import/commit", json={"token": p["token"], "mapping": p["mapping"]})
    assert r.json() == {"format": "table", "audience": "mixed", "added": 3, "duplicates": 1, "opted_out": 0, "invalid": [], "invalid_count": 0}


def test_full_pipeline_over_smtp(configured, smtp_server):
    c = configured
    sink, port = smtp_server
    c.put("/api/settings/email", json={
        "mode": "smtp", "smtp_host": "127.0.0.1", "smtp_port": port, "smtp_security": "none",
        "smtp_username": "", "from_email": "ananya@drtracker.test", "from_name": "Ananya Gupta",
    })
    _import(c)

    # Worker drafts everyone (template drafter — no API key), nothing sends: approval required.
    worker.tick()
    review = c.get("/api/review").json()
    assert len(review) == 3 and all(m["status"] == "draft" and m["drafted_by"] == "template" for m in review)
    assert sink.messages == []

    # Start refuses nothing (sender filled in); approve all clean drafts, then tick sends one at a time.
    assert c.post("/api/campaign/start").status_code == 200
    assert c.post("/api/review/approve-clean").json() == {"approved": 3, "skipped": 0}
    for _ in range(3):
        worker.tick()
    assert len(sink.messages) == 3
    first_msg = email.message_from_bytes(sink.messages[0].content, policy=policy.default)
    assert first_msg["List-Unsubscribe"] == "<mailto:ananya@drtracker.test?subject=unsubscribe>"
    text = first_msg.get_content()
    assert "Reply \"unsubscribe\" and we'll stop." in text and "HSR Layout" in text and "Namaste Dr. Sharma" in text

    contacts = {x["email"]: x for x in c.get("/api/contacts").json()}
    assert all(x["status"] == "sent" for x in contacts.values())

    # Replies and bounces come back through the inbox path.
    sent = {m["contact_email"]: m for m in c.get("/api/outbox").json()}
    reply = f"From: aditi@x.in\r\nSubject: Re: hi\r\nIn-Reply-To: {sent['aditi@x.in']['message_id']}\r\n\r\nYes, Thursday works.".encode()
    unsub = b"From: rahul@x.in\r\nSubject: Re: hi\r\n\r\nPlease unsubscribe me."
    bounce = (b"From: mailer-daemon@x.in\r\nSubject: Undeliverable\r\n\r\nFinal-Recipient: rfc822; gone@x.in\r\n")
    for i, raw in enumerate([reply, unsub, bounce, reply]):  # the repeated reply must be ignored
        outreach.record_inbound(f"1:{i if i < 3 else 0}", inbox.classify(raw))
    contacts = {x["email"]: x for x in c.get("/api/contacts").json()}
    assert contacts["aditi@x.in"]["status"] == "replied"
    assert contacts["rahul@x.in"]["status"] == "unsubscribed" and contacts["rahul@x.in"]["suppressed"]
    assert contacts["gone@x.in"]["status"] == "bounced"

    # Opted-out people can't be re-added.
    r = c.post("/api/contacts", json={"name": "Rahul again", "email": "RAHUL@x.in"})
    assert r.status_code == 409


def test_followup_threads_and_stops_on_reply(configured, smtp_server):
    c = configured
    sink, port = smtp_server
    c.put("/api/settings/email", json={"mode": "smtp", "smtp_host": "127.0.0.1", "smtp_port": port,
                                       "smtp_security": "none", "from_email": "ananya@drtracker.test"})
    c.put("/api/settings/campaign", json={"require_approval": False, "followup_after_days": 0})
    cid = c.post("/api/contacts", json={"name": "Dr. Aditi Sharma", "email": "aditi@x.in", "specialty": "Cardiology"}).json()["id"]
    c.post("/api/campaign/start")

    worker.tick()  # drafts (auto-approved) and sends the first email
    assert len(sink.messages) == 1
    time.sleep(0.01)
    worker.tick()  # follow-up drafted and sent in-thread
    assert len(sink.messages) == 2
    followup = email.message_from_bytes(sink.messages[1].content, policy=policy.default)
    first = c.get("/api/outbox").json()[-1]
    assert followup["In-Reply-To"] == first["message_id"] and followup["Subject"].startswith("Re: ")

    detail = c.get(f"/api/contacts/{cid}").json()
    assert detail["followups_sent"] == 1
    worker.tick()  # max_followups = 1: nothing more
    assert len(sink.messages) == 2


def test_window_cap_and_dry_run(configured):
    c = configured
    c.put("/api/settings/campaign", json={"require_approval": False, "daily_cap": 1})
    for i in range(3):
        c.post("/api/contacts", json={"name": f"Dr. Test {i}", "email": f"t{i}@x.in"})
    c.post("/api/campaign/start")
    for _ in range(3):
        worker.tick()
    out = c.get("/api/outbox").json()
    assert len(out) == 1 and out[0]["transport"] == "dry_run"           # daily cap of 1
    assert worker.send_stage()["skipped"] == "daily cap reached"

    settings_store.update_section("campaign", {"daily_cap": 10, "ist_window": True, "send_days": []})
    assert worker.send_stage()["skipped"] == "outside sending window"
    c.post("/api/campaign/pause")
    assert worker.send_stage()["skipped"] == "paused"


def test_qa_errors_block_sending(app_client):
    c = app_client  # no sender name / address configured
    cid = c.post("/api/contacts", json={"name": "Dr. Aditi Sharma", "email": "aditi@x.in"}).json()["id"]
    m = c.post(f"/api/contacts/{cid}/draft").json()
    assert any(i["level"] == "error" for i in m["qa"])
    r = c.post(f"/api/messages/{m['id']}/approve")
    assert r.status_code == 400 and "Sender name" in r.json()["detail"]
    assert c.post("/api/campaign/start").status_code == 400


def test_secrets_are_masked(app_client):
    c = app_client
    c.put("/api/settings/ai", json={"anthropic_api_key": "sk-ant-api-secretvalue1234"})
    view = c.get("/api/settings").json()["ai"]
    assert view["anthropic_api_key"] == "" and view["anthropic_api_key_set"] and view["anthropic_api_key_hint"].endswith("1234")
    c.put("/api/settings/ai", json={"anthropic_api_key": "", "model": "claude-sonnet-5-5"})  # blank keeps the key
    assert settings_store.get_section("ai")["anthropic_api_key"] == "sk-ant-api-secretvalue1234"
