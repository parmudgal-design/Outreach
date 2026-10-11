"""The outreach pipeline, shared by the API and the background worker.

Lifecycle of a contact:
  new -> drafted -> sent -> replied -> adopted
                         \\-> bounced / unsubscribed / declined (terminal)
Lifecycle of a message:
  draft -> approved -> sending -> sent | failed
  draft/approved -> discarded (contact replied, opted out, or user discarded it)
Kinds of message: initial, followup (no answer yet), reply (our response to their reply).
"""
import re
import time

from .. import db, settings_store
from . import compliance, drafting, mailer, marketplace, qa, research, schedule, specialties

CONTACT_TYPES = ["doctor", "hospital", "company", "partner", "brand", "professional"]
# Free-text contact fields set from imports and the contact form.
TEXT_FIELDS = ["specialty", "clinic", "product", "city", "notes", "category", "partner_id", "tier", "phone",
               "website", "description", "source_url", "needs_check"]
CONTACT_STATUSES = ["new", "drafted", "sent", "replied", "adopted", "declined", "unsubscribed", "bounced"]
TERMINAL = {"declined", "unsubscribed", "bounced"}
MAX_DRAFT_ATTEMPTS = 3


class OutreachError(Exception):
    pass


class DailyCapReached(OutreachError):
    pass


# --- Suppression ------------------------------------------------------------

def is_suppressed(conn, email: str) -> bool:
    return conn.execute("SELECT 1 FROM suppression WHERE email = ?", (email.strip().lower(),)).fetchone() is not None


def suppress(conn, email: str, reason: str) -> None:
    conn.execute(
        "INSERT INTO suppression (email, reason, created_at) VALUES (?, ?, ?) ON CONFLICT(email) DO NOTHING",
        (email.strip().lower(), reason, time.time()),
    )


# --- Contacts ---------------------------------------------------------------

def add_contact(conn, data: dict, source: str = "manual") -> tuple[int | None, str | None]:
    """Returns (id, None) on success or (None, reason) when skipped."""
    email = data["email"].strip().lower()
    if is_suppressed(conn, email):
        return None, "opted_out"
    if conn.execute("SELECT 1 FROM contacts WHERE email = ?", (email,)).fetchone():
        return None, "duplicate"
    now = time.time()
    data = {**data, "email": email, "type": data.get("type") or "doctor"}
    key, key_source = detect_specialty(data)
    row = {"type": data["type"], "name": data["name"].strip(), "email": email, "specialty_key": key,
           "specialty_source": key_source, "source": source, "created_at": now, "updated_at": now,
           **{f: (data.get(f) or "").strip() for f in TEXT_FIELDS}}
    cur = conn.execute(
        f"INSERT INTO contacts ({', '.join(row)}) VALUES ({', '.join('?' * len(row))})", tuple(row.values())
    )
    return cur.lastrowid, None


def detect_specialty(contact: dict) -> tuple[str | None, str]:
    if (contact.get("type") or "doctor") != "doctor":
        return None, ""
    return specialties.detect(contact)


def redetect_specialty(conn, contact_id: int) -> None:
    """Re-run detection after an edit, unless the user picked the specialty by hand."""
    c = get_contact(conn, contact_id)
    if c["specialty_source"] == "manual" and c["type"] == "doctor":
        return
    key, source = detect_specialty(c)
    conn.execute("UPDATE contacts SET specialty_key = ?, specialty_source = ? WHERE id = ?", (key, source, contact_id))


def get_contact(conn, contact_id: int) -> dict:
    c = db.one(conn.execute("SELECT * FROM contacts WHERE id = ?", (contact_id,)))
    if not c:
        raise OutreachError("Contact not found.")
    return c


def set_status(conn, contact_id: int, status: str, note: str = "") -> dict:
    if status not in CONTACT_STATUSES:
        raise OutreachError(f"Unknown status {status!r}.")
    c = get_contact(conn, contact_id)
    now = time.time()
    fields = {"status": status, "updated_at": now}
    if status == "replied" and not c["replied_at"]:
        fields["replied_at"] = now
    if status in ("unsubscribed", "bounced"):
        suppress(conn, c["email"], status)
    if c["status"] in ("unsubscribed", "bounced") and status not in ("unsubscribed", "bounced"):
        conn.execute("DELETE FROM suppression WHERE email = ?", (c["email"],))
    if status in ("unsubscribed", "bounced"):
        # Nothing at all goes out to someone who opted out or can't receive mail.
        conn.execute(
            "UPDATE messages SET status = 'discarded', updated_at = ? WHERE contact_id = ? AND status IN ('draft', 'approved')",
            (now, contact_id),
        )
    elif status in TERMINAL or status in ("replied", "adopted"):
        # No more pitches or follow-ups to someone who answered; a response to their reply can still go.
        conn.execute(
            "UPDATE messages SET status = 'discarded', updated_at = ? WHERE contact_id = ? AND status IN ('draft', 'approved') AND kind != 'reply'",
            (now, contact_id),
        )
    conn.execute(
        f"UPDATE contacts SET {', '.join(f'{k} = ?' for k in fields)} WHERE id = ?",
        (*fields.values(), contact_id),
    )
    db.log_event("status", f"{c['name']} → {status}" + (f" ({note})" if note else ""), contact_id=contact_id, conn=conn)
    return get_contact(conn, contact_id)


# --- Drafting ---------------------------------------------------------------

def pending_message(conn, contact_id: int) -> dict | None:
    return db.one(conn.execute(
        "SELECT * FROM messages WHERE contact_id = ? AND status IN ('draft', 'approved', 'sending') ORDER BY id DESC LIMIT 1",
        (contact_id,),
    ))


def last_sent(conn, contact_id: int) -> dict | None:
    return db.one(conn.execute(
        "SELECT * FROM messages WHERE contact_id = ? AND status = 'sent' ORDER BY sent_at DESC LIMIT 1", (contact_id,)
    ))


def first_sent(conn, contact_id: int) -> dict | None:
    return db.one(conn.execute(
        "SELECT * FROM messages WHERE contact_id = ? AND status = 'sent' ORDER BY sent_at ASC LIMIT 1", (contact_id,)
    ))


def draft(contact_id: int, kind: str = "initial", drafter=None) -> dict:
    """Write a draft for a contact (replacing any unsent one). Network call happens outside a DB transaction."""
    with db.connect() as conn:
        contact = get_contact(conn, contact_id)
        s = settings_store.get_all(conn)
        if contact["status"] in TERMINAL:
            raise OutreachError(f"{contact['name']} is {contact['status']} — nothing will be drafted.")
        previous = first_sent(conn, contact_id) if kind == "followup" else None
        if kind == "followup" and not previous:
            raise OutreachError("No sent email to follow up on yet.")
    drafter = drafter or drafting.get_drafter(s["ai"])
    if (kind == "initial" and getattr(drafter, "name", "") == "claude" and s["ai"].get("website_research")
            and contact["type"] in marketplace.TYPES and research.is_due(contact)):
        contact = research_contact(contact_id)
    try:
        if kind == "followup":
            d = drafter.followup(contact, s["sender"], s["pitches"], previous, contact["followups_sent"] + 1, s["marketplace"])
        else:
            d = drafter.initial(contact, s["sender"], s["pitches"], s["marketplace"])
    except Exception as raised:
        e = raised if isinstance(raised, drafting.DraftError) else drafting.DraftError(f"Unexpected drafting error: {raised}")
        # Only permanent failures count toward MAX_DRAFT_ATTEMPTS; rate limits, 5xx and
        # network blips are retried on a later tick without using up the contact's attempts.
        attempts = "draft_attempts" if e.retryable else "draft_attempts + 1"
        with db.connect() as conn:
            conn.execute(
                f"UPDATE contacts SET draft_attempts = {attempts}, last_error = ?, updated_at = ? WHERE id = ?",
                (str(e), time.time(), contact_id),
            )
            db.log_event("draft", f"Draft failed for {contact['name']}: {e}", level="warn" if e.retryable else "error",
                         contact_id=contact_id, conn=conn)
        if e is raised:
            raise
        raise e from raised

    now = time.time()
    status = "draft" if s["campaign"]["require_approval"] else "approved"
    with db.connect() as conn:
        conn.execute(
            "UPDATE messages SET status = 'discarded', updated_at = ? WHERE contact_id = ? AND status IN ('draft', 'approved')",
            (now, contact_id),
        )
        cur = conn.execute(
            """INSERT INTO messages (contact_id, kind, subject, body, status, drafted_by, in_reply_to, approved_by, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (contact_id, kind, d.subject, d.body, status, d.drafted_by,
             previous["message_id"] if previous else None, AUTO_APPROVED if status == "approved" else None, now, now),
        )
        if contact["status"] == "new":
            conn.execute("UPDATE contacts SET status = 'drafted' WHERE id = ?", (contact_id,))
        conn.execute("UPDATE contacts SET draft_attempts = 0, last_error = NULL, updated_at = ? WHERE id = ?", (now, contact_id))
        db.log_event("draft", f"{'Follow-up' if kind == 'followup' else 'Draft'} written for {contact['name']} ({d.drafted_by})",
                     contact_id=contact_id, conn=conn)
        return get_message(conn, cur.lastrowid)


def research_contact(contact_id: int, force: bool = False) -> dict:
    """Read the contact's website (when due, or `force`) and store an excerpt for the drafter.
    A site that can't be read is noted on the contact; it never stops a draft."""
    with db.connect() as conn:
        c = get_contact(conn, contact_id)
    if not force and not research.is_due(c):
        return c
    try:
        text, url = research.research(c)
        error = None
    except research.ResearchError as e:
        text, url, error = c.get("site_text") or "", "", str(e)  # an older excerpt stays useful
    now = time.time()
    with db.connect() as conn:
        conn.execute("UPDATE contacts SET site_text = ?, site_checked_at = ?, site_error = ?, updated_at = ? WHERE id = ?",
                     (text, now, error, now, contact_id))
        db.log_event("research", f"Read {url} for {c['name']}" if not error else f"Website not read for {c['name']}: {error}",
                     contact_id=contact_id, conn=conn)
        return get_contact(conn, contact_id)


# --- Messages ---------------------------------------------------------------

def get_message(conn, message_id: int) -> dict:
    m = db.one(conn.execute("SELECT * FROM messages WHERE id = ?", (message_id,)))
    if not m:
        raise OutreachError("Message not found.")
    return m


def check_message(conn, message: dict, contact: dict | None = None) -> list[dict]:
    contact = contact or get_contact(conn, message["contact_id"])
    s = settings_store.get_all(conn)
    issues = qa.check(message["subject"], message["body"], contact, s["sender"], s["pitches"], message["kind"], s["marketplace"])
    if is_suppressed(conn, contact["email"]):
        issues.insert(0, {"level": "error", "message": "This person is on the opt-out list."})
    return issues


def update_message(conn, message_id: int, subject: str | None, body: str | None) -> dict:
    m = get_message(conn, message_id)
    if m["status"] not in ("draft", "approved"):
        raise OutreachError("Only unsent drafts can be edited.")
    new_subject = m["subject"] if subject is None else subject
    new_body = m["body"] if body is None else body
    if (new_subject, new_body) == (m["subject"], m["body"]):
        return m
    by = m["drafted_by"]
    if by != "manual" and not by.endswith("+edited"):
        by += "+edited"
    # An edited approved draft goes back to review when approval is required.
    status = m["status"]
    if status == "approved" and settings_store.get_section("campaign", conn)["require_approval"]:
        status = "draft"
    conn.execute(
        "UPDATE messages SET subject = ?, body = ?, drafted_by = ?, status = ?, approved_by = CASE WHEN ? = 'draft' THEN NULL ELSE approved_by END, updated_at = ? WHERE id = ?",
        (new_subject, new_body, by, status, status, time.time(), message_id),
    )
    return get_message(conn, message_id)


AUTO_APPROVED = "auto (approval not required)"
WORKER = "campaign"


def approve(conn, message_id: int) -> dict:
    m = get_message(conn, message_id)
    if m["status"] != "draft":
        raise OutreachError("Only drafts can be approved.")
    issues = check_message(conn, m)
    if qa.has_errors(issues):
        raise OutreachError("Fix the pre-send errors first: " + "; ".join(i["message"] for i in issues if i["level"] == "error"))
    by = db.current_actor() or "local (no sign-in)"
    conn.execute("UPDATE messages SET status = 'approved', approved_by = ?, updated_at = ? WHERE id = ?", (by, time.time(), message_id))
    contact = get_contact(conn, m["contact_id"])
    db.log_event("approve", f"Approved {'follow-up' if m['kind'] == 'followup' else 'email'} to {contact['name']} <{contact['email']}>",
                 contact_id=contact["id"], conn=conn, actor=by)
    return get_message(conn, message_id)


def unapprove(conn, message_id: int) -> dict:
    conn.execute("UPDATE messages SET status = 'draft', approved_by = NULL, updated_at = ? WHERE id = ? AND status = 'approved'", (time.time(), message_id))
    return get_message(conn, message_id)


def discard(conn, message_id: int) -> dict:
    conn.execute("UPDATE messages SET status = 'discarded', updated_at = ? WHERE id = ? AND status IN ('draft', 'approved', 'failed')", (time.time(), message_id))
    return get_message(conn, message_id)


# --- Sending ----------------------------------------------------------------

def can_receive(contact: dict, kind: str) -> bool:
    """Whether a message of this kind may still go to this contact."""
    if contact["status"] in ("unsubscribed", "bounced"):
        return False
    if kind == "reply":
        return True  # answering someone who wrote to us, even to say "no thanks"
    if contact["status"] == "declined":
        return False
    return not (kind == "followup" and contact["status"] in ("replied", "adopted"))


def sent_since(conn, ts: float) -> int:
    return conn.execute("SELECT COUNT(*) FROM messages WHERE status = 'sent' AND sent_at >= ?", (ts,)).fetchone()[0]


def used_today(conn) -> int:
    """Emails that count toward today's cap: sent since IST midnight, plus any in flight right now."""
    return sent_since(conn, schedule.start_of_ist_day()) + conn.execute(
        "SELECT COUNT(*) FROM messages WHERE status = 'sending'").fetchone()[0]


def send(message_id: int, transport=None) -> dict:
    """Send one approved message. Claims it first ('sending') so two callers can't double-send,
    and claims it only while today's cap has room, so the worker and "Send now" can't overshoot it."""
    with db.connect() as conn:
        # Take the write lock before reading: the cap check and the claim below happen as one step.
        conn.execute("BEGIN IMMEDIATE")
        m = get_message(conn, message_id)
        if m["status"] != "approved":
            raise OutreachError("Only approved messages can be sent.")
        contact = get_contact(conn, m["contact_id"])
        if not can_receive(contact, m["kind"]):
            conn.execute("UPDATE messages SET status = 'discarded', updated_at = ? WHERE id = ?", (time.time(), message_id))
            raise OutreachError(f"{contact['name']} is {contact['status']} — message discarded.")
        issues = check_message(conn, m, contact)
        if qa.has_errors(issues):
            conn.execute("UPDATE messages SET status = 'draft', updated_at = ? WHERE id = ?", (time.time(), message_id))
            raise OutreachError("Blocked by pre-send check: " + "; ".join(i["message"] for i in issues if i["level"] == "error"))
        s = settings_store.get_all(conn)
        cap = int(s["campaign"]["daily_cap"])
        if used_today(conn) >= cap:
            raise DailyCapReached(f"Today's daily cap of {cap} emails is reached — it resets at midnight IST.")
        claimed = conn.execute(
            "UPDATE messages SET status = 'sending', updated_at = ? WHERE id = ? AND status = 'approved'", (time.time(), message_id)
        ).rowcount
        if not claimed:
            raise OutreachError("Message is already being sent.")

    # From here the message is claimed: every way out must leave it 'sent' or 'failed', never 'sending'.
    handed_over = False
    try:
        from_email, from_name = mailer.from_address(s["email"], s["sender"])
        transport = transport or mailer.get_transport(s["email"])
        if transport.name == "smtp" and not from_email:
            _mark_failed(message_id, contact, "No From address — set it under Connections.")
            raise OutreachError("No From address configured.")
        subject = m["subject"] if m["kind"] == "initial" or m["subject"].lower().startswith("re:") else f"Re: {m['subject']}"
        msg = mailer.build_message(
            from_email=from_email or "dry-run@localhost",
            from_name=from_name,
            to_email=contact["email"],
            to_name=contact["name"],
            subject=subject,
            body=compliance.full_body(m["body"], s["sender"], from_email),
            reply_to=s["email"].get("reply_to", ""),
            in_reply_to=m["in_reply_to"],
        )
        try:
            transport.send(msg)
        except mailer.SendError as e:
            _mark_failed(message_id, contact, str(e), bounce=e.permanent)
            raise OutreachError(str(e)) from e
        handed_over = True
        return _record_sent(message_id, m, contact, msg, subject, transport.name)
    except OutreachError:
        raise
    except Exception as e:
        if handed_over:
            error = f"Handed to the mail server, but recording it failed ({e}). Check your Sent folder before retrying."
        else:
            error = f"Unexpected error while sending: {e}"
        _mark_failed(message_id, contact, error)
        raise OutreachError(error) from e


def _record_sent(message_id: int, m: dict, contact: dict, msg, subject: str, transport_name: str) -> dict:
    now = time.time()
    # A person pressing "Send now", or the background campaign sending what someone approved.
    by = db.current_actor() or (WORKER if m.get("approved_by") else "local (no sign-in)")
    with db.connect() as conn:
        conn.execute(
            "UPDATE messages SET status = 'sent', sent_at = ?, message_id = ?, transport = ?, subject = ?, sent_by = ?, updated_at = ? WHERE id = ?",
            (now, msg["Message-ID"], transport_name, subject, by, now, message_id),
        )
        extra = ", followups_sent = followups_sent + 1" if m["kind"] == "followup" else ""
        conn.execute(
            f"UPDATE contacts SET status = CASE WHEN status IN ('new', 'drafted') THEN 'sent' ELSE status END, last_sent_at = ?, updated_at = ?{extra} WHERE id = ?",
            (now, now, contact["id"]),
        )
        label = "Dry run — recorded" if transport_name == "dry_run" else "Sent"
        approved = f" (approved by {m['approved_by']})" if m.get("approved_by") else ""
        db.log_event("send", f"{label} {'follow-up' if m['kind'] == 'followup' else 'email'} to {contact['name']} <{contact['email']}>{approved}",
                     contact_id=contact["id"], conn=conn, actor=by)
        return get_message(conn, message_id)


def _mark_failed(message_id: int, contact: dict, error: str, bounce: bool = False) -> None:
    with db.connect() as conn:
        conn.execute("UPDATE messages SET status = 'failed', error = ?, updated_at = ? WHERE id = ?", (error, time.time(), message_id))
        db.log_event("send", f"Send to {contact['email']} failed: {error}", level="error", contact_id=contact["id"], conn=conn)
        if bounce:
            set_status(conn, contact["id"], "bounced", "rejected by the mail server")


# --- Replies ----------------------------------------------------------------

MAX_REPLY_ATTEMPTS = 3
INTENT_LABEL = {
    "interested": "Interested", "question": "Has a question", "not_now": "Not now", "not_interested": "Not interested",
    "wrong_person": "Wrong person", "other": "Other",
}


def get_inbound(conn, inbound_id: int) -> dict:
    i = db.one(conn.execute("SELECT * FROM inbound WHERE id = ?", (inbound_id,)))
    if not i:
        raise OutreachError("Reply not found.")
    return i


def latest_reply(conn, contact_id: int) -> dict | None:
    return db.one(conn.execute(
        "SELECT * FROM inbound WHERE contact_id = ? AND kind = 'reply' ORDER BY received_at DESC, id DESC LIMIT 1", (contact_id,)))


def _reply_subject(theirs: dict, ours: dict | None) -> str:
    base = re.sub(r"^\s*((re|fwd?|aw)\s*:\s*)+", "", theirs.get("subject") or (ours or {}).get("subject") or "", flags=re.I).strip()
    return f"Re: {base}" if base else "Re: your reply"


def draft_reply(inbound_id: int, drafter=None) -> dict | None:
    """Work out what a reply is asking and draft a response, which always waits for approval.
    Returns the new message, or None when no response is drafted (no Claude key, or turned off)."""
    with db.connect() as conn:
        theirs = get_inbound(conn, inbound_id)
        if theirs["kind"] != "reply" or not theirs["contact_id"]:
            raise OutreachError("That isn't a reply from one of your contacts.")
        contact = get_contact(conn, theirs["contact_id"])
        if not can_receive(contact, "reply"):
            conn.execute("UPDATE inbound SET handled_at = ? WHERE id = ?", (time.time(), inbound_id))
            raise OutreachError(f"{contact['name']} is {contact['status']} — no response will be drafted.")
        ours = last_sent(conn, contact["id"])
        s = settings_store.get_all(conn)
    drafter = drafter or drafting.get_drafter(s["ai"])
    try:
        r = drafter.reply(contact, s["sender"], s["pitches"], s["marketplace"], ours, theirs) if s["ai"].get("reply_drafts") else None
    except Exception as raised:
        e = raised if isinstance(raised, drafting.DraftError) else drafting.DraftError(f"Unexpected drafting error: {raised}")
        with db.connect() as conn:
            if not e.retryable:
                conn.execute("UPDATE inbound SET reply_attempts = reply_attempts + 1 WHERE id = ?", (inbound_id,))
            db.log_event("draft", f"Couldn't draft a response to {contact['name']}: {e}", level="warn" if e.retryable else "error",
                         contact_id=contact["id"], conn=conn)
        if e is raised:
            raise
        raise e from raised

    now = time.time()
    with db.connect() as conn:
        # Earlier unanswered replies from the same person are covered by this response.
        conn.execute("UPDATE inbound SET handled_at = ? WHERE contact_id = ? AND kind = 'reply' AND handled_at IS NULL AND received_at <= ?",
                     (now, contact["id"], theirs["received_at"]))
        conn.execute("UPDATE inbound SET handled_at = ?, intent = ?, intent_note = ? WHERE id = ?",
                     (now, r.intent if r else None, r.summary if r else None, inbound_id))
        if not r or not r.body:
            return None
        conn.execute(
            "UPDATE messages SET status = 'discarded', updated_at = ? WHERE contact_id = ? AND status IN ('draft', 'approved')",
            (now, contact["id"]),
        )
        cur = conn.execute(
            """INSERT INTO messages (contact_id, kind, subject, body, status, drafted_by, in_reply_to, inbound_id, created_at, updated_at)
               VALUES (?, 'reply', ?, ?, 'draft', ?, ?, ?, ?, ?)""",
            (contact["id"], _reply_subject(theirs, ours), r.body, r.drafted_by,
             theirs["message_id"] or (ours or {}).get("message_id"), inbound_id, now, now),
        )
        db.log_event("draft", f"Response drafted for {contact['name']}'s reply ({INTENT_LABEL.get(r.intent, r.intent)})",
                     contact_id=contact["id"], conn=conn)
        return get_message(conn, cur.lastrowid)


# --- Inbound ----------------------------------------------------------------

def record_inbound(uid_key: str, verdict: dict) -> dict | None:
    """Match an incoming message to a contact and apply it. Idempotent per uid_key."""
    with db.connect() as conn:
        if conn.execute("SELECT 1 FROM inbound WHERE uid_key = ?", (uid_key,)).fetchone():
            return None
        contact = None
        for ref in verdict.get("refs", []):
            row = conn.execute("SELECT contact_id FROM messages WHERE message_id = ?", (ref,)).fetchone()
            if row:
                contact = get_contact(conn, row["contact_id"])
                break
        if contact is None:
            candidates = verdict.get("recipients", []) if verdict["kind"] == "bounce" else [verdict.get("from_addr", "")]
            for addr in candidates:
                row = conn.execute("SELECT id FROM contacts WHERE email = ?", (addr,)).fetchone()
                if row:
                    contact = get_contact(conn, row["id"])
                    break
        kind = verdict["kind"] if contact else "unmatched"
        conn.execute(
            "INSERT INTO inbound (contact_id, uid_key, kind, from_addr, subject, snippet, body, message_id, received_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (contact["id"] if contact else None, uid_key, kind, verdict.get("from_addr", ""), verdict.get("subject", ""),
             verdict.get("snippet", ""), verdict.get("text", ""), verdict.get("message_id"), time.time()),
        )
        if not contact:
            return None
        # Only mail about someone we've actually contacted changes their status.
        contacted = contact["last_sent_at"] is not None
        if verdict["kind"] == "unsubscribe":
            set_status(conn, contact["id"], "unsubscribed", "asked by email")
        elif verdict["kind"] == "bounce" and contacted:
            set_status(conn, contact["id"], "bounced", verdict.get("snippet", "")[:120])
        elif verdict["kind"] == "reply" and contacted and contact["status"] in ("sent", "drafted"):
            set_status(conn, contact["id"], "replied")
        elif verdict["kind"] == "auto_reply":
            db.log_event("inbox", f"Auto-reply from {contact['name']} (out of office?) — no change", contact_id=contact["id"], conn=conn)
        return get_contact(conn, contact["id"])
