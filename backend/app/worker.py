"""Background worker: the part that makes the system run without the browser open.

Every tick it:
  1. polls the inbox (if configured and due) for replies, bounces and opt-outs,
  2. drafts a suggested response to each new reply and alerts the team,
  3. drafts new contacts (when auto-draft is on) and due follow-ups (while running),
  4. sends at most one approved message, if the campaign is running, the IST
     window is open, today's cap isn't reached and the pacing delay has passed.

Sending one message per tick, with a randomised gap, is deliberate: it keeps a
single inbox well under provider rate limits and looks like a person sending.
"""
import asyncio
import logging
import random
import threading
import time

from . import config, db, settings_store
from .services import alerts, drafting, inbox, outreach, schedule

log = logging.getLogger("outreach.worker")
DRAFTS_PER_TICK = 3
# A reply's alert waits for its suggested response, but never longer than this.
ALERT_WAIT_SECONDS = 30 * 60
INBOX_ALERT_GAP = 6 * 3600

# Serialises ticks: the API's "run now" and the timer loop never overlap.
_tick_lock = threading.Lock()


def _runtime(conn) -> dict:
    return db.get_json(conn, "runtime", {}) or {}


def _set_runtime(conn, **values) -> None:
    rt = _runtime(conn)
    rt.update(values)
    db.set_json(conn, "runtime", rt)


def status() -> dict:
    with db.connect() as conn:
        s = settings_store.get_all(conn)
        rt = _runtime(conn)
        sent_today = outreach.sent_since(conn, schedule.start_of_ist_day())
        approved = conn.execute("SELECT COUNT(*) FROM messages WHERE status = 'approved'").fetchone()[0]
        drafts = conn.execute("SELECT COUNT(*) FROM messages WHERE status = 'draft'").fetchone()[0]
        replies = conn.execute("SELECT COUNT(*) FROM messages WHERE status = 'draft' AND kind = 'reply'").fetchone()[0]
        to_draft = conn.execute(
            "SELECT COUNT(*) FROM contacts c WHERE c.status = 'new' AND c.draft_attempts < ? AND NOT EXISTS "
            "(SELECT 1 FROM messages m WHERE m.contact_id = c.id AND m.status IN ('draft','approved','sending'))",
            (outreach.MAX_DRAFT_ATTEMPTS,),
        ).fetchone()[0]
    camp = s["campaign"]
    nxt = schedule.next_window_open(camp)
    return {
        "running": camp["running"],
        "in_window": schedule.in_window(camp),
        "ist_now": schedule.now_ist().strftime("%a %d %b, %I:%M %p IST"),
        "next_window": nxt.strftime("%a %d %b, %I:%M %p IST") if nxt else None,
        "sent_today": sent_today,
        "daily_cap": camp["daily_cap"],
        "approved_waiting": approved,
        "drafts_to_review": drafts,
        "replies_to_review": replies,
        "contacts_to_draft": to_draft,
        "next_send_at": rt.get("next_send_at"),
        "last_tick": rt.get("last_tick"),
        "last_inbox_poll": rt.get("last_inbox_poll"),
        "last_inbox_error": rt.get("last_inbox_error"),
        "email_mode": s["email"]["mode"],
        "drafter": "claude" if s["ai"]["anthropic_api_key"] else "template",
        "worker_enabled": config.WORKER_ENABLED,
    }


# --- Stages ------------------------------------------------------------------

def poll_inbox(force: bool = False) -> dict:
    with db.connect() as conn:
        s = settings_store.get_all(conn)
        rt = _runtime(conn)
    e = s["email"]
    if e["mode"] != "smtp" or not e["imap_enabled"]:
        return {"skipped": "Inbox checking is off (needs SMTP mode + IMAP enabled under Connections)."}
    due = time.time() - (rt.get("last_inbox_poll") or 0) >= s["campaign"]["inbox_poll_minutes"] * 60
    if not (force or due):
        return {"skipped": "not due"}
    counts = {"reply": 0, "bounce": 0, "unsubscribe": 0, "auto_reply": 0, "unmatched": 0}
    try:
        items = inbox.fetch_new(e, rt.get("last_uid_key"))
    except inbox.InboxError as err:
        with db.connect() as conn:
            _set_runtime(conn, last_inbox_poll=time.time(), last_inbox_error=str(err))
            db.log_event("inbox", str(err), level="error", conn=conn)
        alerts.send("Outreach: can't read the inbox",
                    f"The outreach app couldn't check the inbox, so replies, bounces and unsubscribes aren't being "
                    f"picked up until this is fixed (Connections → Reading replies).\n\nError: {err}",
                    throttle_key="inbox_error", min_gap=INBOX_ALERT_GAP)
        return {"error": str(err)}
    last_key = rt.get("last_uid_key")
    for uid_key, raw in items:
        verdict = inbox.classify(raw)
        contact = outreach.record_inbound(uid_key, verdict)
        counts[verdict["kind"] if contact else "unmatched"] += 1
        last_key = uid_key
    with db.connect() as conn:
        _set_runtime(conn, last_inbox_poll=time.time(), last_uid_key=last_key, last_inbox_error=None)
        matched = {k: v for k, v in counts.items() if v and k != "unmatched"}
        if matched:
            db.log_event("inbox", "Inbox: " + ", ".join(f"{v} {k.replace('_', '-')}" for k, v in matched.items()), conn=conn)
    return counts


def reply_stage(drafter=None, now: float | None = None) -> dict:
    """Suggested responses to new replies, then one alert per reply."""
    with db.connect() as conn:
        ids = [r["id"] for r in conn.execute(
            "SELECT i.id FROM inbound i JOIN contacts c ON c.id = i.contact_id "
            "WHERE i.kind = 'reply' AND i.handled_at IS NULL AND i.reply_attempts < ? AND i.body != '' "
            "AND c.last_sent_at IS NOT NULL AND c.status NOT IN ('unsubscribed', 'bounced') "
            "ORDER BY i.received_at LIMIT ?",
            (outreach.MAX_REPLY_ATTEMPTS, DRAFTS_PER_TICK),
        )]
    drafted = 0
    for inbound_id in ids:
        try:
            if outreach.draft_reply(inbound_id, drafter=drafter):
                drafted += 1
        except drafting.DraftError:
            break  # same back-off as draft_stage
        except outreach.OutreachError:
            continue
    return {"drafted": drafted, "alerted": alert_stage(now)}


def alert_stage(now: float | None = None) -> int:
    now = now or time.time()
    with db.connect() as conn:
        due = db.rows(conn.execute(
            "SELECT i.* FROM inbound i WHERE i.kind = 'reply' AND i.alerted_at IS NULL AND i.contact_id IS NOT NULL "
            "AND i.body != '' AND (i.handled_at IS NOT NULL OR i.reply_attempts >= ? OR i.received_at < ?) "
            "ORDER BY i.received_at LIMIT 20",
            (outreach.MAX_REPLY_ATTEMPTS, now - ALERT_WAIT_SECONDS),
        ))
    sent = 0
    for theirs in due:
        with db.connect() as conn:
            contact = outreach.get_contact(conn, theirs["contact_id"])
            has_draft = conn.execute("SELECT 1 FROM messages WHERE inbound_id = ? AND status IN ('draft', 'approved')",
                                     (theirs["id"],)).fetchone() is not None
            # Marked first: a failing mail server must not turn one reply into an alert every tick.
            conn.execute("UPDATE inbound SET alerted_at = ? WHERE id = ?", (now, theirs["id"]))
        if alerts.reply_alert(contact, theirs, has_draft, outreach.INTENT_LABEL.get(theirs["intent"])):
            sent += 1
    return sent


def draft_stage(drafter=None) -> int:
    with db.connect() as conn:
        camp = settings_store.get_section("campaign", conn)
        ids: list[tuple[int, str]] = []
        if camp["auto_draft"]:
            ids += [(r["id"], "initial") for r in conn.execute(
                "SELECT c.id FROM contacts c WHERE c.status = 'new' AND c.draft_attempts < ? AND NOT EXISTS "
                "(SELECT 1 FROM messages m WHERE m.contact_id = c.id AND m.status IN ('draft','approved','sending')) "
                "ORDER BY c.created_at LIMIT ?",
                (outreach.MAX_DRAFT_ATTEMPTS, DRAFTS_PER_TICK),
            )]
        if camp["running"] and camp["followups_enabled"] and len(ids) < DRAFTS_PER_TICK:
            cutoff = time.time() - camp["followup_after_days"] * 86400
            ids += [(r["id"], "followup") for r in conn.execute(
                "SELECT c.id FROM contacts c WHERE c.status = 'sent' AND c.followups_sent < ? AND c.last_sent_at < ? "
                "AND c.draft_attempts < ? AND NOT EXISTS "
                "(SELECT 1 FROM messages m WHERE m.contact_id = c.id AND m.status IN ('draft','approved','sending')) "
                "ORDER BY c.last_sent_at LIMIT ?",
                (camp["max_followups"], cutoff, outreach.MAX_DRAFT_ATTEMPTS, DRAFTS_PER_TICK - len(ids)),
            )]
    done = 0
    for contact_id, kind in ids:
        try:
            outreach.draft(contact_id, kind, drafter=drafter)
            done += 1
        except drafting.DraftError:
            # Rate limits and outages hit every call, and so do config problems (bad key, model):
            # back off until the next tick rather than hammering the API.
            break
        except outreach.OutreachError:
            continue
    return done


def send_stage(transport=None, now: float | None = None) -> dict:
    now = now or time.time()
    with db.connect() as conn:
        s = settings_store.get_all(conn)
        camp = s["campaign"]
        rt = _runtime(conn)
        if not camp["running"]:
            return {"skipped": "paused"}
        if not schedule.in_window(camp, now):
            return {"skipped": "outside sending window"}
        if outreach.sent_since(conn, schedule.start_of_ist_day(now)) >= int(camp["daily_cap"]):
            return {"skipped": "daily cap reached"}
        if (rt.get("next_send_at") or 0) > now:
            return {"skipped": "pacing"}
        row = conn.execute(
            "SELECT m.id FROM messages m JOIN contacts c ON c.id = m.contact_id "
            "WHERE m.status = 'approved' AND c.status NOT IN ('unsubscribed','bounced') AND (c.status != 'declined' OR m.kind = 'reply') "
            "ORDER BY CASE m.kind WHEN 'reply' THEN 0 WHEN 'followup' THEN 1 ELSE 2 END, m.created_at LIMIT 1"
        ).fetchone()
        if not row:
            return {"skipped": "nothing approved"}
        delay = int(camp["min_delay_seconds"]) + random.randint(0, max(0, int(camp["jitter_seconds"])))
        _set_runtime(conn, next_send_at=now + delay)
    try:
        m = outreach.send(row["id"], transport=transport)
        return {"sent": m["id"]}
    except outreach.DailyCapReached:
        return {"skipped": "daily cap reached"}
    except outreach.OutreachError as e:
        return {"error": str(e)}


def tick(drafter=None, transport=None) -> dict:
    if not _tick_lock.acquire(blocking=False):
        return {"skipped": "tick already running"}
    try:
        result = {"inbox": poll_inbox(), "replies": reply_stage(drafter), "drafted": draft_stage(drafter),
                  "send": send_stage(transport)}
        with db.connect() as conn:
            _set_runtime(conn, last_tick=time.time())
        return result
    except Exception as e:  # never let one bad tick kill the loop
        log.exception("worker tick failed")
        db.log_event("worker", f"Worker error: {e}", level="error")
        return {"error": str(e)}
    finally:
        _tick_lock.release()


async def run_forever() -> None:
    log.info("worker started (tick every %ss)", config.WORKER_TICK_SECONDS)
    while True:
        await asyncio.to_thread(tick)
        await asyncio.sleep(config.WORKER_TICK_SECONDS)
