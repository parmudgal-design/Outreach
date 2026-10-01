"""JSON API used by the frontend."""
import csv
import io
import time
import uuid

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from . import db, settings_store, worker
from .services import drafting, importer, inbox, mailer, outreach, qa, schedule, specialties

router = APIRouter(prefix="/api")

# Parsed uploads waiting for the user to confirm the column mapping.
_pending_imports: dict[str, dict] = {}


def _err(e: Exception, code: int = 400):
    raise HTTPException(status_code=code, detail=str(e))


# --- Settings ----------------------------------------------------------------

@router.get("/settings")
def get_settings():
    return settings_store.public_view()


@router.put("/settings/{section}")
def put_settings(section: str, patch: dict):
    try:
        settings_store.update_section(section, patch)
    except KeyError:
        _err(Exception(f"Unknown settings section {section!r}"), 404)
    return settings_store.public_view()


@router.post("/settings/test/ai")
def test_ai():
    ai = settings_store.get_section("ai")
    if not ai["anthropic_api_key"]:
        return {"ok": False, "message": "No API key saved yet — drafts use the built-in templates."}
    try:
        reply = drafting.ClaudeDrafter(ai["anthropic_api_key"], ai["model"]).ping()
        return {"ok": True, "message": f"Claude answered ({ai['model']}): {reply[:40]}"}
    except Exception as e:  # surface any SDK error to the user verbatim
        return {"ok": False, "message": str(e)}


@router.post("/settings/test/smtp")
def test_smtp():
    e = settings_store.get_section("email")
    try:
        return {"ok": True, "message": mailer.get_transport(e).test()}
    except mailer.SendError as err:
        return {"ok": False, "message": str(err)}


class TestSend(BaseModel):
    to: str


@router.post("/settings/test/send")
def test_send(body: TestSend):
    s = settings_store.get_all()
    from_email, from_name = mailer.from_address(s["email"], s["sender"])
    transport = mailer.get_transport(s["email"])
    msg = mailer.build_message(
        from_email=from_email or "dry-run@localhost", from_name=from_name, to_email=body.to, to_name="",
        subject="Dr.Tracker Outreach — test email",
        body="This is a test from your Dr.Tracker Outreach system. If you can read it, sending works.",
        reply_to=s["email"].get("reply_to", ""),
    )
    try:
        transport.send(msg)
    except mailer.SendError as err:
        return {"ok": False, "message": str(err)}
    if transport.name == "dry_run":
        return {"ok": True, "message": "Dry-run mode — nothing was actually sent. Switch to SMTP to send for real."}
    return {"ok": True, "message": f"Test email sent to {body.to}."}


@router.post("/settings/test/imap")
def test_imap():
    e = settings_store.get_section("email")
    try:
        return {"ok": True, "message": inbox.test(e)}
    except inbox.InboxError as err:
        return {"ok": False, "message": str(err)}


# --- Contacts ----------------------------------------------------------------

class ContactIn(BaseModel):
    name: str = Field(min_length=1)
    email: str = Field(min_length=3)
    type: str = "doctor"
    specialty: str = ""
    clinic: str = ""
    product: str = ""
    city: str = ""
    notes: str = ""


class ContactPatch(BaseModel):
    name: str | None = None
    type: str | None = None
    specialty: str | None = None
    clinic: str | None = None
    product: str | None = None
    city: str | None = None
    notes: str | None = None
    # A canonical key from /api/meta, "" to go back to automatic detection.
    specialty_key: str | None = None


def _contact_view(conn, c: dict) -> dict:
    pending = outreach.pending_message(conn, c["id"])
    c = dict(c)
    c["pending_message"] = pending
    c["suppressed"] = outreach.is_suppressed(conn, c["email"])
    c["specialty_label"] = specialties.label(c.get("specialty_key"))
    return c


@router.get("/meta")
def meta():
    return {"specialties": specialties.choices(), "statuses": outreach.CONTACT_STATUSES}


@router.get("/contacts")
def list_contacts(status: str | None = None, q: str | None = None):
    sql, args = "SELECT * FROM contacts WHERE 1=1", []
    if status:
        sql += " AND status = ?"
        args.append(status)
    if q:
        sql += " AND (name LIKE ? OR email LIKE ? OR clinic LIKE ? OR specialty LIKE ? OR city LIKE ?)"
        args += [f"%{q}%"] * 5
    sql += " ORDER BY updated_at DESC"
    with db.connect() as conn:
        return [_contact_view(conn, c) for c in db.rows(conn.execute(sql, args))]


@router.post("/contacts")
def create_contact(body: ContactIn):
    if not importer.EMAIL_RE.match(body.email.strip()):
        _err(Exception("That email address doesn't look valid."))
    with db.connect() as conn:
        cid, reason = outreach.add_contact(conn, body.model_dump())
        if reason == "duplicate":
            _err(Exception("That email is already in your contacts."), 409)
        if reason == "opted_out":
            _err(Exception("That email is on the opt-out list — they asked not to be contacted."), 409)
        return _contact_view(conn, outreach.get_contact(conn, cid))


@router.get("/contacts/{contact_id}")
def contact_detail(contact_id: int):
    with db.connect() as conn:
        try:
            c = _contact_view(conn, outreach.get_contact(conn, contact_id))
        except outreach.OutreachError as e:
            _err(e, 404)
        c["messages"] = db.rows(conn.execute("SELECT * FROM messages WHERE contact_id = ? ORDER BY created_at DESC", (contact_id,)))
        c["inbound"] = db.rows(conn.execute("SELECT * FROM inbound WHERE contact_id = ? ORDER BY received_at DESC", (contact_id,)))
        if c["pending_message"]:
            c["pending_message"]["qa"] = outreach.check_message(conn, c["pending_message"], c)
        return c


@router.patch("/contacts/{contact_id}")
def patch_contact(contact_id: int, body: ContactPatch):
    fields = {k: v for k, v in body.model_dump().items() if v is not None}
    manual_key = fields.pop("specialty_key", None)
    if manual_key and manual_key not in specialties.PLAYBOOKS:
        _err(Exception(f"Unknown specialty {manual_key!r}."))
    with db.connect() as conn:
        outreach.get_contact(conn, contact_id)
        if fields:
            fields["updated_at"] = time.time()
            conn.execute(f"UPDATE contacts SET {', '.join(f'{k} = ?' for k in fields)} WHERE id = ?", (*fields.values(), contact_id))
        if manual_key:
            conn.execute("UPDATE contacts SET specialty_key = ?, specialty_source = 'manual' WHERE id = ?", (manual_key, contact_id))
        elif manual_key == "" or fields:
            if manual_key == "":
                conn.execute("UPDATE contacts SET specialty_source = '' WHERE id = ?", (contact_id,))
            outreach.redetect_specialty(conn, contact_id)
        return _contact_view(conn, outreach.get_contact(conn, contact_id))


class StatusIn(BaseModel):
    status: str


@router.post("/contacts/{contact_id}/status")
def contact_status(contact_id: int, body: StatusIn):
    with db.connect() as conn:
        try:
            return _contact_view(conn, outreach.set_status(conn, contact_id, body.status, "set by hand"))
        except outreach.OutreachError as e:
            _err(e)


@router.delete("/contacts/{contact_id}")
def delete_contact(contact_id: int):
    with db.connect() as conn:
        c = outreach.get_contact(conn, contact_id)
        conn.execute("DELETE FROM contacts WHERE id = ?", (contact_id,))
        db.log_event("contact", f"Deleted {c['name']} <{c['email']}>", conn=conn)
    return {"ok": True}


@router.post("/contacts/{contact_id}/draft")
def draft_contact(contact_id: int, kind: str = "initial"):
    try:
        m = outreach.draft(contact_id, kind)
    except (drafting.DraftError, outreach.OutreachError) as e:
        _err(e)
    with db.connect() as conn:
        m["qa"] = outreach.check_message(conn, m)
    return m


class ManualDraft(BaseModel):
    subject: str
    body: str


@router.post("/contacts/{contact_id}/manual-draft")
def manual_draft(contact_id: int, body: ManualDraft):
    with db.connect() as conn:
        c = outreach.get_contact(conn, contact_id)
        now = time.time()
        conn.execute("UPDATE messages SET status = 'discarded', updated_at = ? WHERE contact_id = ? AND status IN ('draft','approved')", (now, contact_id))
        cur = conn.execute(
            "INSERT INTO messages (contact_id, kind, subject, body, status, drafted_by, created_at, updated_at) VALUES (?, 'initial', ?, ?, 'draft', 'manual', ?, ?)",
            (contact_id, body.subject, body.body, now, now),
        )
        if c["status"] == "new":
            conn.execute("UPDATE contacts SET status = 'drafted' WHERE id = ?", (contact_id,))
        return outreach.get_message(conn, cur.lastrowid)


# --- Import ------------------------------------------------------------------

@router.post("/import/preview")
async def import_preview(file: UploadFile = File(...)):
    data = await file.read()
    if len(data) > 10 * 1024 * 1024:
        _err(Exception("That file is over 10 MB."))
    try:
        headers, rows = importer.read_table(file.filename or "", data)
    except importer.ImportError_ as e:
        _err(e)
    token = uuid.uuid4().hex
    _pending_imports[token] = {"headers": headers, "rows": rows, "filename": file.filename, "created": time.time()}
    for k in [k for k, v in _pending_imports.items() if time.time() - v["created"] > 3600]:
        _pending_imports.pop(k, None)
    return {
        "token": token,
        "filename": file.filename,
        "headers": headers,
        "row_count": len(rows),
        "preview": rows[:8],
        "mapping": importer.guess_mapping(headers, rows),
        "fields": importer.FIELDS,
    }


class ImportCommit(BaseModel):
    token: str
    mapping: dict
    default_type: str = "doctor"


@router.post("/import/commit")
def import_commit(body: ImportCommit):
    pending = _pending_imports.pop(body.token, None)
    if not pending:
        _err(Exception("That upload expired — upload the file again."), 410)
    if body.mapping.get("email") is None:
        _err(Exception("Pick which column holds the email address."))
    contacts, problems = importer.to_contacts(pending["headers"], pending["rows"], body.mapping, body.default_type)
    added = dup = opted = 0
    with db.connect() as conn:
        for c in contacts:
            cid, reason = outreach.add_contact(conn, c, source=f"import:{pending['filename']}")
            if cid:
                added += 1
            elif reason == "duplicate":
                dup += 1
            else:
                opted += 1
        db.log_event("import", f"Imported {pending['filename']}: {added} added, {dup} duplicates, {opted} opted-out, {len(problems)} invalid rows", conn=conn)
    return {"added": added, "duplicates": dup, "opted_out": opted, "invalid": problems[:50], "invalid_count": len(problems)}


@router.get("/import/template.csv")
def import_template():
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["name", "email", "type", "specialty", "clinic", "product", "city", "notes"])
    w.writerow(["Dr. Aditi Sharma", "aditi.sharma@example.in", "doctor", "MD, DM (Cardiology)", "Sharma Heart Clinic", "", "Pune", "Runs a busy OPD; follow-up no-shows are a pain point"])
    w.writerow(["Dr. Rahul Nair", "rahul.nair@example.in", "doctor", "", "Kochi Bone & Joint Centre", "", "Kochi", "Mostly knee replacements"])
    w.writerow(["Dr. Rakesh Malhotra", "ms@example-hospital.in", "hospital", "Medical Superintendent", "Navjeevan Hospital", "", "Delhi", "Expanding to a second campus"])
    w.writerow(["Ritu Varma", "ritu@example-diagnostics.in", "company", "Head of Partnerships", "Sanjeevani Diagnostics", "", "Mumbai", "Expanding to Tier 2 cities"])
    w.writerow(["Neha Kapoor", "neha@example-devices.in", "partner", "Head of BD", "VitalSense", "VitalSense home BP monitor", "Bengaluru", "Bluetooth BP cuff with its own app"])
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv",
                             headers={"Content-Disposition": "attachment; filename=contacts-template.csv"})


# --- Review / messages -------------------------------------------------------

@router.get("/review")
def review_queue():
    with db.connect() as conn:
        items = db.rows(conn.execute(
            "SELECT m.*, c.name AS contact_name, c.email AS contact_email, c.type AS contact_type, c.specialty, c.clinic, c.city "
            "FROM messages m JOIN contacts c ON c.id = m.contact_id WHERE m.status IN ('draft', 'approved', 'failed') "
            "ORDER BY CASE m.status WHEN 'draft' THEN 0 WHEN 'failed' THEN 1 ELSE 2 END, m.created_at"
        ))
        for m in items:
            m["qa"] = outreach.check_message(conn, m)
        return items


class MessagePatch(BaseModel):
    subject: str | None = None
    body: str | None = None


def _msg_action(fn, message_id: int, *args):
    with db.connect() as conn:
        try:
            m = fn(conn, message_id, *args)
        except outreach.OutreachError as e:
            _err(e)
        m["qa"] = outreach.check_message(conn, m)
        return m


@router.put("/messages/{message_id}")
def edit_message(message_id: int, body: MessagePatch):
    return _msg_action(outreach.update_message, message_id, body.subject, body.body)


@router.post("/messages/{message_id}/approve")
def approve_message(message_id: int):
    return _msg_action(outreach.approve, message_id)


@router.post("/messages/{message_id}/unapprove")
def unapprove_message(message_id: int):
    return _msg_action(outreach.unapprove, message_id)


@router.post("/messages/{message_id}/discard")
def discard_message(message_id: int):
    return _msg_action(outreach.discard, message_id)


@router.post("/messages/{message_id}/retry")
def retry_message(message_id: int):
    with db.connect() as conn:
        conn.execute("UPDATE messages SET status = 'draft', error = NULL, updated_at = ? WHERE id = ? AND status = 'failed'", (time.time(), message_id))
    return _msg_action(lambda conn, mid: outreach.get_message(conn, mid), message_id)


@router.post("/messages/{message_id}/send")
def send_now(message_id: int):
    """Send immediately (outside the window), but never past today's cap."""
    with db.connect() as conn:
        cap = int(settings_store.get_section("campaign", conn)["daily_cap"])
        if outreach.sent_since(conn, schedule.start_of_ist_day()) >= cap:
            _err(Exception(f"Today's daily cap of {cap} emails is reached — it resets at midnight IST."), 429)
        m = outreach.get_message(conn, message_id)
        if m["status"] == "draft":
            try:
                outreach.approve(conn, message_id)
            except outreach.OutreachError as e:
                _err(e)
    try:
        return outreach.send(message_id)
    except outreach.OutreachError as e:
        _err(e)


@router.post("/review/approve-clean")
def approve_clean():
    """Approve every draft with no pre-send errors."""
    approved = skipped = 0
    with db.connect() as conn:
        for m in db.rows(conn.execute("SELECT * FROM messages WHERE status = 'draft'")):
            if qa.has_errors(outreach.check_message(conn, m)):
                skipped += 1
                continue
            outreach.approve(conn, m["id"])
            approved += 1
    return {"approved": approved, "skipped": skipped}


# --- Campaign / worker -------------------------------------------------------

@router.get("/campaign")
def campaign_status():
    return worker.status()


@router.post("/campaign/start")
def campaign_start():
    s = settings_store.get_all()
    problems = []
    if not s["sender"]["name"]:
        problems.append("sender name (Setup)")
    if not s["sender"]["address"]:
        problems.append("business address (Setup)")
    if s["email"]["mode"] == "smtp" and not (s["email"]["from_email"] or s["email"]["smtp_username"]):
        problems.append("From address (Connections)")
    if problems:
        _err(Exception("Fill in " + ", ".join(problems) + " before starting."))
    settings_store.update_section("campaign", {"running": True})
    db.log_event("campaign", "Campaign started" + (" (dry run)" if s["email"]["mode"] == "dry_run" else ""))
    return worker.status()


@router.post("/campaign/pause")
def campaign_pause():
    settings_store.update_section("campaign", {"running": False})
    db.log_event("campaign", "Campaign paused")
    return worker.status()


@router.post("/campaign/tick")
def campaign_tick():
    """Run one worker tick now (ignores nothing — window, cap and pacing still apply)."""
    return {"result": worker.tick(), "status": worker.status()}


@router.post("/campaign/check-inbox")
def check_inbox():
    return worker.poll_inbox(force=True)


# --- Tracker / activity ------------------------------------------------------

@router.get("/dashboard")
def dashboard():
    with db.connect() as conn:
        counts = {s: 0 for s in outreach.CONTACT_STATUSES}
        for r in conn.execute("SELECT status, COUNT(*) n FROM contacts GROUP BY status"):
            counts[r["status"]] = r["n"]
        by_type = {r["type"]: r["n"] for r in conn.execute("SELECT type, COUNT(*) n FROM contacts GROUP BY type")}
        sent_total = conn.execute("SELECT COUNT(*) FROM messages WHERE status = 'sent'").fetchone()[0]
        contacted = conn.execute("SELECT COUNT(*) FROM contacts WHERE last_sent_at IS NOT NULL").fetchone()[0]
        replied = conn.execute("SELECT COUNT(*) FROM contacts WHERE replied_at IS NOT NULL").fetchone()[0]
        recent_replies = db.rows(conn.execute(
            "SELECT i.*, c.name AS contact_name FROM inbound i LEFT JOIN contacts c ON c.id = i.contact_id "
            "WHERE i.kind IN ('reply', 'unsubscribe') ORDER BY i.received_at DESC LIMIT 8"
        ))
        daily = db.rows(conn.execute(
            "SELECT date(sent_at + 19800, 'unixepoch') AS day, COUNT(*) AS n FROM messages WHERE status = 'sent' "
            "AND sent_at >= ? GROUP BY day ORDER BY day", (time.time() - 14 * 86400,)
        ))
    return {
        "counts": counts, "by_type": by_type, "sent_total": sent_total, "contacted": contacted, "replied": replied,
        "reply_rate": round(100 * replied / contacted, 1) if contacted else None,
        "recent_replies": recent_replies, "daily_sends": daily, "campaign": worker.status(),
    }


@router.get("/activity")
def activity(limit: int = 200):
    with db.connect() as conn:
        return db.rows(conn.execute(
            "SELECT e.*, c.name AS contact_name FROM events e LEFT JOIN contacts c ON c.id = e.contact_id ORDER BY e.ts DESC LIMIT ?",
            (min(limit, 1000),),
        ))


@router.get("/outbox")
def outbox(limit: int = 200):
    with db.connect() as conn:
        return db.rows(conn.execute(
            "SELECT m.*, c.name AS contact_name, c.email AS contact_email FROM messages m JOIN contacts c ON c.id = m.contact_id "
            "WHERE m.status = 'sent' ORDER BY m.sent_at DESC LIMIT ?", (min(limit, 1000),),
        ))


@router.get("/inbox")
def inbound_list(limit: int = 200):
    with db.connect() as conn:
        return db.rows(conn.execute(
            "SELECT i.*, c.name AS contact_name FROM inbound i LEFT JOIN contacts c ON c.id = i.contact_id ORDER BY i.received_at DESC LIMIT ?",
            (min(limit, 1000),),
        ))


@router.get("/preview/{message_id}")
def preview(message_id: int):
    """The exact text a recipient receives, footer included."""
    from .services import compliance
    with db.connect() as conn:
        m = outreach.get_message(conn, message_id)
        s = settings_store.get_all(conn)
    from_email, _ = mailer.from_address(s["email"], s["sender"])
    return {"subject": m["subject"], "body": compliance.full_body(m["body"], s["sender"], from_email)}


# --- Suppression -------------------------------------------------------------

class SuppressIn(BaseModel):
    email: str
    reason: str = "added by hand"


@router.get("/suppression")
def suppression_list():
    with db.connect() as conn:
        return db.rows(conn.execute("SELECT * FROM suppression ORDER BY created_at DESC"))


@router.post("/suppression")
def suppression_add(body: SuppressIn):
    with db.connect() as conn:
        outreach.suppress(conn, body.email, body.reason)
        row = conn.execute("SELECT id FROM contacts WHERE email = ?", (body.email.strip().lower(),)).fetchone()
        if row:
            outreach.set_status(conn, row["id"], "unsubscribed", "added to opt-out list")
    return {"ok": True}


@router.delete("/suppression/{email}")
def suppression_remove(email: str):
    with db.connect() as conn:
        conn.execute("DELETE FROM suppression WHERE email = ?", (email.lower(),))
        db.log_event("suppression", f"Removed {email} from the opt-out list", conn=conn)
    return {"ok": True}


# --- Export ------------------------------------------------------------------

@router.get("/export/contacts.csv")
def export_contacts():
    with db.connect() as conn:
        rows = db.rows(conn.execute("SELECT * FROM contacts ORDER BY created_at"))
    buf = io.StringIO()
    cols = ["name", "email", "type", "specialty", "specialty_key", "clinic", "product", "city", "notes", "status", "followups_sent", "last_sent_at", "replied_at", "source"]
    w = csv.writer(buf)
    w.writerow(cols)
    for r in rows:
        w.writerow([
            time.strftime("%Y-%m-%d %H:%M", time.localtime(r[c])) if c in ("last_sent_at", "replied_at") and r[c] else r[c]
            for c in cols
        ])
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv",
                             headers={"Content-Disposition": "attachment; filename=drtracker-contacts.csv"})
