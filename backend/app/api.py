"""JSON API used by the frontend."""
import csv
import io
import json
import time
import uuid
from urllib.parse import quote

from fastapi import APIRouter, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from . import auth, config, db, settings_store, worker
from .services import drafting, importer, inbox, mailer, marketplace, outreach, partner_sheet, qa, schedule, specialties

router = APIRouter(prefix="/api")



def _err(e: Exception, code: int = 400):
    raise HTTPException(status_code=code, detail=str(e))


# --- Sign-in and users ---------------------------------------------------------

class LoginIn(BaseModel):
    email: str
    password: str


class UserIn(BaseModel):
    email: str
    name: str = ""
    password: str
    role: str = "member"


class PasswordIn(BaseModel):
    current: str = ""
    new: str


def _user(request: Request) -> dict | None:
    return getattr(request.state, "user", None)


def _require_admin(request: Request) -> None:
    """Admins manage users. With no users yet (local only — the middleware enforces that) anyone here may."""
    u = _user(request)
    if u is not None and u["role"] != "admin":
        _err(Exception("Only an admin can manage users."), 403)


@router.get("/auth/state")
def auth_state(request: Request):
    with db.connect() as conn:
        user = auth.user_for_token(conn, request.cookies.get(auth.COOKIE))
        users_exist = auth.user_count(conn) > 0
        local_open = not users_exist and (request.client.host if request.client else "") in ("127.0.0.1", "::1", "localhost")
        return {"user": user, "users_exist": users_exist, "local_open": local_open,
                "warnings": auth.warnings(conn) if (user or local_open) else []}


@router.post("/auth/login")
def auth_login(body: LoginIn, response: Response):
    with db.connect() as conn:
        try:
            token, user = auth.login(conn, body.email, body.password)
        except auth.AuthError as e:
            _err(e, 401)
    response.set_cookie(auth.COOKIE, token, httponly=True, samesite="lax", secure=config.COOKIE_SECURE,
                        max_age=config.SESSION_DAYS * 86400, path="/")
    return {"user": user}


@router.post("/auth/logout")
def auth_logout(request: Request, response: Response):
    with db.connect() as conn:
        auth.logout(conn, request.cookies.get(auth.COOKIE))
    response.delete_cookie(auth.COOKIE, path="/")
    return {"ok": True}


@router.post("/auth/password")
def auth_password(body: PasswordIn, request: Request, response: Response):
    u = _user(request)
    if u is None:
        _err(Exception("Sign in first."), 401)
    with db.connect() as conn:
        row = db.one(conn.execute("SELECT * FROM users WHERE id = ?", (u["id"],)))
        if not auth.verify_password(body.current, row["password_hash"]):
            _err(Exception("Your current password isn't right."), 400)
        try:
            auth.set_password(conn, u["id"], body.new)
        except auth.AuthError as e:
            _err(e)
        db.log_event("user", f"{u['email']} changed their password", conn=conn)
    response.delete_cookie(auth.COOKIE, path="/")
    return {"ok": True, "message": "Password changed — sign in again."}


@router.get("/users")
def list_users(request: Request):
    _require_admin(request)
    with db.connect() as conn:
        return [auth.public(u) for u in db.rows(conn.execute("SELECT * FROM users ORDER BY created_at"))]


@router.post("/users")
def add_user(body: UserIn, request: Request):
    _require_admin(request)
    with db.connect() as conn:
        role = "admin" if auth.user_count(conn) == 0 else body.role  # the first account is always an admin
        try:
            return auth.create_user(conn, body.email, body.name, body.password, role)
        except auth.AuthError as e:
            _err(e)


@router.delete("/users/{user_id}")
def remove_user(user_id: int, request: Request):
    _require_admin(request)
    with db.connect() as conn:
        try:
            auth.delete_user(conn, user_id, _user(request))
        except auth.AuthError as e:
            _err(e)
    return {"ok": True}


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
    category: str = ""
    description: str = ""
    needs_check: str = ""
    website: str = ""
    phone: str = ""


class ContactPatch(BaseModel):
    name: str | None = None
    type: str | None = None
    specialty: str | None = None
    clinic: str | None = None
    product: str | None = None
    city: str | None = None
    notes: str | None = None
    category: str | None = None
    description: str | None = None
    needs_check: str | None = None
    website: str | None = None
    phone: str | None = None
    # A canonical key from /api/meta, "" to go back to automatic detection.
    specialty_key: str | None = None


def _check_type(ctype: str) -> None:
    if ctype not in outreach.CONTACT_TYPES:
        _err(Exception(f"Unknown contact type {ctype!r}."))


def _contact_view(conn, c: dict) -> dict:
    pending = outreach.pending_message(conn, c["id"])
    c = dict(c)
    c["pending_message"] = pending
    c["suppressed"] = outreach.is_suppressed(conn, c["email"])
    c["specialty_label"] = specialties.label(c.get("specialty_key"))
    return c


@router.get("/meta")
def meta():
    return {
        "specialties": specialties.choices(),
        "statuses": outreach.CONTACT_STATUSES,
        "categories": {t: [{"key": k, "label": c.label} for k, c in marketplace.categories(t).items()] for t in marketplace.TYPES},
    }


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
    data = body.model_dump()
    _check_type(data["type"])
    data["category"] = marketplace.normalise_category(data["category"], data["type"])
    with db.connect() as conn:
        cid, reason = outreach.add_contact(conn, data)
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
    if "type" in fields:
        _check_type(fields["type"])
    with db.connect() as conn:
        existing = outreach.get_contact(conn, contact_id)
        if "category" in fields:
            fields["category"] = marketplace.normalise_category(fields["category"], fields.get("type") or existing["type"])
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

PENDING_TTL = 3600


def _store_pending(kind: str, filename: str, payload: dict) -> str:
    token = uuid.uuid4().hex
    with db.connect() as conn:
        conn.execute("DELETE FROM pending_imports WHERE created_at < ?", (time.time() - PENDING_TTL,))
        conn.execute("INSERT INTO pending_imports (token, filename, kind, payload, created_at) VALUES (?, ?, ?, ?, ?)",
                     (token, filename or "", kind, json.dumps(payload), time.time()))
    return token


def _take_pending(conn, token: str) -> dict | None:
    r = db.one(conn.execute("SELECT * FROM pending_imports WHERE token = ? AND created_at >= ?",
                            (token, time.time() - PENDING_TTL)))
    if r:
        conn.execute("DELETE FROM pending_imports WHERE token = ?", (token,))
        r["payload"] = json.loads(r["payload"])
    return r


def _sheet_summary(p: dict) -> dict:
    return {
        "counts": p["counts"],
        "skipped": p["skipped"][:200],
        "flags": p["flags"][:200],
        "adds": [{"tab": a["tab"], "row": a["row"], "name": a["contact"]["name"], "email": a["contact"]["email"],
                  "type": a["contact"]["type"], "category": a["contact"]["category"], "status": a["status"]}
                 for a in p["adds"][:200]],
        "updates": [{"tab": u["tab"], "row": u["row"], "name": u["name"], "fields": sorted(u["changes"]),
                     "opt_out": u["opt_out"]} for u in p["updates"][:200]],
    }


@router.post("/import/preview")
async def import_preview(file: UploadFile = File(...)):
    data = await file.read()
    if len(data) > 10 * 1024 * 1024:
        _err(Exception("That file is over 10 MB."))
    filename = file.filename or ""
    if filename.lower().endswith((".xlsx", ".xlsm")):
        try:
            sheet = partner_sheet.read(data)
        except partner_sheet.SheetError as e:
            _err(e)
        if sheet["tabs"]:
            # The founder's workbook: every city tab at once, no column mapping needed.
            with db.connect() as conn:
                p = partner_sheet.plan(conn, sheet["rows"], settings_store.get_section("import", conn))
            token = _store_pending("partner_workbook", filename, {"rows": sheet["rows"]})
            return {"token": token, "filename": filename, "format": "partner_workbook", "tabs": sheet["tabs"],
                    "skipped_tabs": sheet["skipped_tabs"], "row_count": len(sheet["rows"]), **_sheet_summary(p)}
    try:
        headers, rows = importer.read_table(filename, data)
    except importer.ImportError_ as e:
        _err(e)
    token = _store_pending("table", filename, {"headers": headers, "rows": rows})
    return {
        "token": token,
        "filename": filename,
        "format": "table",
        "headers": headers,
        "row_count": len(rows),
        "preview": rows[:8],
        "mapping": importer.guess_mapping(headers, rows),
        "fields": importer.FIELDS,
    }


class ImportCommit(BaseModel):
    token: str
    mapping: dict = Field(default_factory=dict)
    default_type: str = "doctor"


@router.post("/import/commit")
def import_commit(body: ImportCommit):
    with db.connect() as conn:
        pending = _take_pending(conn, body.token)
        if not pending:
            _err(Exception("That upload expired — upload the file again."), 410)
        source = f"import:{pending['filename']}"
        if pending["kind"] == "partner_workbook":
            # Re-plan inside this transaction so the result matches the database as it is now.
            p = partner_sheet.plan(conn, pending["payload"]["rows"], settings_store.get_section("import", conn))
            done = partner_sheet.apply(conn, p, source)
            c = p["counts"]
            reasons = ", ".join(f"{n} {r}" for r, n in sorted(c["skipped_by_reason"].items())) or "none"
            db.log_event("import", f"Imported {pending['filename']}: {done['added']} added, {done['updated']} updated, "
                                   f"{c['unchanged']} unchanged, skipped: {reasons}", conn=conn)
            return {"format": "partner_workbook", **done, **_sheet_summary(p)}
        if body.mapping.get("email") is None:
            _err(Exception("Pick which column holds the email address."))
        payload = pending["payload"]
        contacts, problems = importer.to_contacts(payload["headers"], payload["rows"], body.mapping, body.default_type)
        added = dup = opted = 0
        for c in contacts:
            cid, reason = outreach.add_contact(conn, c, source=source)
            if cid:
                added += 1
            elif reason == "duplicate":
                dup += 1
            else:
                opted += 1
        db.log_event("import", f"Imported {pending['filename']}: {added} added, {dup} duplicates, {opted} opted-out, {len(problems)} invalid rows", conn=conn)
    return {"format": "table", "added": added, "duplicates": dup, "opted_out": opted, "invalid": problems[:50], "invalid_count": len(problems)}


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
    w.writerow(["Crunchwell Foods", "hello@crunchwell.example", "brand", "Wellness food", "", "Millet snack bars with no added sugar", "Mumbai", "Partner ID B-FD-T2-MUM-01"])
    w.writerow(["Dt. Meenal Joshi (Green Bowl Clinic)", "meenal@greenbowl.example", "professional", "Nutritionist / dietitian", "", "Diabetes-friendly meal planning and weekly check-ins", "Hyderabad", "Partner ID P-NT-T3-HYD-01"])
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
        conn.execute("UPDATE messages SET status = 'draft', error = NULL, approved_by = NULL, updated_at = ? WHERE id = ? AND status = 'failed'", (time.time(), message_id))
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
    except outreach.DailyCapReached as e:
        # The worker took the last slot between the check above and the send: leave the draft as it was.
        if m["status"] == "draft":
            with db.connect() as conn:
                outreach.unapprove(conn, message_id)
        _err(e, 429)
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
    cols = ["name", "email", "type", "category", "specialty", "specialty_key", "clinic", "product", "description", "city", "notes",
            "partner_id", "tier", "phone", "website", "needs_check", "status", "followups_sent", "last_sent_at", "replied_at", "source"]
    w = csv.writer(buf)
    w.writerow(cols)
    for r in rows:
        w.writerow([
            time.strftime("%Y-%m-%d %H:%M", time.localtime(r[c])) if c in ("last_sent_at", "replied_at") and r[c] else r[c]
            for c in cols
        ])
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv",
                             headers={"Content-Disposition": "attachment; filename=drtracker-contacts.csv"})


@router.post("/export/partner-workbook")
async def export_partner_workbook(file: UploadFile = File(...)):
    """Upload the founder's current workbook; get the same file back with only the Status column updated."""
    data = await file.read()
    if len(data) > 10 * 1024 * 1024:
        _err(Exception("That file is over 10 MB."))
    name = file.filename or "partners.xlsx"
    if not name.lower().endswith((".xlsx", ".xlsm")):
        _err(Exception("Upload the workbook as .xlsx."))
    with db.connect() as conn:
        try:
            out, summary = partner_sheet.write_status(conn, data, name)
        except partner_sheet.SheetError as e:
            _err(e)
        db.log_event("export", f"Updated Status in {name}: {summary['updated']} changed, {summary['unchanged']} already right, "
                               f"{summary['unmatched']} rows not in the app", conn=conn)
    stem, dot, ext = name.rpartition(".")
    media = "application/vnd.ms-excel.sheet.macroEnabled.12" if ext.lower() == "xlsm" else \
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    counts = {k: summary[k] for k in ("updated", "unchanged", "unmatched")}
    return StreamingResponse(iter([out]), media_type=media, headers={
        "Content-Disposition": f"attachment; filename*=UTF-8''{quote(f'{stem} (status updated).{ext}')}",
        "X-Status-Summary": json.dumps(counts),
    })


@router.get("/export/partner-status.csv")
def export_partner_status():
    """Every marketplace partner's status, keyed by the founder's partner ID."""
    with db.connect() as conn:
        rows = db.rows(conn.execute(
            "SELECT * FROM contacts WHERE partner_id != '' OR type IN ('brand', 'professional') ORDER BY partner_id, name"))
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["partner_id", "name", "email", "type", "category", "tier", "city", "status", "app_status", "last_sent", "replied"])
    fmt = lambda ts: time.strftime("%Y-%m-%d %H:%M", time.localtime(ts)) if ts else ""
    for r in rows:
        w.writerow([r["partner_id"], r["name"], r["email"], r["type"], r["category"], r["tier"], r["city"],
                    partner_sheet.sheet_label(r["status"]), r["status"], fmt(r["last_sent_at"]), fmt(r["replied_at"])])
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv",
                             headers={"Content-Disposition": "attachment; filename=partner-status.csv"})
