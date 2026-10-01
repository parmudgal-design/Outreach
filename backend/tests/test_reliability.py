"""Regression tests for failure handling: stuck sends, drafting retries, the daily cap on "Send now"."""
import time

import pytest

from backend.app import db, worker
from backend.app.services import drafting, outreach


class ExplodingTransport:
    name = "smtp"

    def send(self, msg):
        raise RuntimeError("socket closed unexpectedly")


class FailingDrafter:
    def __init__(self, retryable: bool):
        self.retryable, self.calls = retryable, 0

    def initial(self, *args):
        self.calls += 1
        raise drafting.DraftError("rate limited" if self.retryable else "bad key", retryable=self.retryable)


def _approved_message(c) -> int:
    cid = c.post("/api/contacts", json={"name": "Dr. Aditi Sharma", "email": "aditi@x.in", "specialty": "Cardiology"}).json()["id"]
    m = c.post(f"/api/contacts/{cid}/draft").json()
    assert c.post(f"/api/messages/{m['id']}/approve").status_code == 200
    return m["id"]


def test_unexpected_send_exception_marks_failed(configured):
    c = configured
    c.put("/api/settings/email", json={"from_email": "ananya@drtracker.test"})
    mid = _approved_message(c)
    with pytest.raises(outreach.OutreachError, match="socket closed"):
        outreach.send(mid, transport=ExplodingTransport())
    with db.connect() as conn:
        m = outreach.get_message(conn, mid)
    assert m["status"] == "failed" and "socket closed" in m["error"]


def test_startup_marks_interrupted_sends_failed(configured):
    mid = _approved_message(configured)
    with db.connect() as conn:
        conn.execute("UPDATE messages SET status = 'sending' WHERE id = ?", (mid,))
    db.init()
    with db.connect() as conn:
        m = outreach.get_message(conn, mid)
    assert m["status"] == "failed"  # not re-queued: it may already have gone out
    assert m["error"] == "Interrupted while sending (app restarted). Check your Sent folder before retrying."


@pytest.mark.parametrize("retryable, expected_attempts", [(True, 0), (False, 1)])
def test_only_permanent_draft_errors_count(configured, retryable, expected_attempts):
    c = configured
    cid = c.post("/api/contacts", json={"name": "Dr. Aditi Sharma", "email": "aditi@x.in"}).json()["id"]
    with pytest.raises(drafting.DraftError):
        outreach.draft(cid, drafter=FailingDrafter(retryable))
    with db.connect() as conn:
        contact = outreach.get_contact(conn, cid)
    assert contact["draft_attempts"] == expected_attempts and contact["last_error"]


def test_unexpected_drafter_exception_counts_as_permanent(configured):
    class Broken:
        def initial(self, *args):
            raise KeyError("subject")

    cid = configured.post("/api/contacts", json={"name": "Dr. A", "email": "a@x.in"}).json()["id"]
    with pytest.raises(drafting.DraftError, match="Unexpected drafting error"):
        outreach.draft(cid, drafter=Broken())
    with db.connect() as conn:
        assert outreach.get_contact(conn, cid)["draft_attempts"] == 1


@pytest.mark.parametrize("retryable", [True, False])
def test_worker_backs_off_on_any_draft_error(configured, retryable):
    for i in range(3):
        configured.post("/api/contacts", json={"name": f"Dr. Test {i}", "email": f"t{i}@x.in"})
    drafter = FailingDrafter(retryable)
    assert worker.draft_stage(drafter) == 0
    assert drafter.calls == 1  # stopped after the first failure, until the next tick


def test_send_now_refuses_at_daily_cap(configured):
    c = configured
    c.put("/api/settings/campaign", json={"daily_cap": 1})
    first = _approved_message(c)
    assert c.post(f"/api/messages/{first}/send").json()["status"] == "sent"
    cid = c.post("/api/contacts", json={"name": "Dr. Rahul Nair", "email": "rahul@x.in"}).json()["id"]
    second = c.post(f"/api/contacts/{cid}/draft").json()["id"]
    r = c.post(f"/api/messages/{second}/send")
    assert r.status_code == 429 and "daily cap" in r.json()["detail"]
    with db.connect() as conn:
        assert outreach.get_message(conn, second)["status"] == "draft"  # untouched, not approved or sent
        assert outreach.sent_since(conn, time.time() - 3600) == 1
