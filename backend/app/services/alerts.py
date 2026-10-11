"""Alert emails to the team: someone replied, or the inbox can't be read.

Alerts go to Connections → "Email me when someone replies" (a person's own inbox),
through the same mail account the campaign sends from. They are not outreach: they
don't count toward the daily cap. In dry-run mode they are only logged.
"""
import time

from .. import config, db, settings_store
from . import mailer


def send(subject: str, body: str, throttle_key: str | None = None, min_gap: float = 0) -> bool:
    """Send one alert. Returns True if it went out (or was logged in dry-run mode)."""
    with db.connect() as conn:
        s = settings_store.get_all(conn)
        to = (s["email"].get("alert_to") or "").strip()
        if not to:
            return False
        sent = (db.get_json(conn, "runtime", {}) or {}).get("alerts", {})
        if throttle_key and time.time() - sent.get(throttle_key, 0) < min_gap:
            return False
    transport = mailer.get_transport(s["email"])
    from_email, from_name = mailer.from_address(s["email"], s["sender"])
    link = f"\n\nOpen the outreach app: {config.PUBLIC_URL}/#/review" if config.PUBLIC_URL else ""
    msg = mailer.build_message(
        from_email=from_email or "dry-run@localhost", from_name=(from_name + " (outreach alerts)").strip(),
        to_email=to, to_name="", subject=subject, body=body + link,
    )
    try:
        transport.send(msg)
    except mailer.SendError as e:
        db.log_event("alert", f"Alert to {to} failed: {e}", level="warn")
        return False
    with db.connect() as conn:
        rt = db.get_json(conn, "runtime", {}) or {}
        if throttle_key:
            rt.setdefault("alerts", {})[throttle_key] = time.time()
            db.set_json(conn, "runtime", rt)
        label = "Alert (dry run, not sent)" if transport.name == "dry_run" else "Alert sent"
        db.log_event("alert", f"{label} to {to}: {subject}", conn=conn)
    return True


def reply_alert(contact: dict, theirs: dict, has_draft: bool, intent_label: str | None) -> bool:
    what = theirs.get("intent_note") or ("Not classified: no Anthropic API key, or reply drafting is off." if not intent_label else "")
    lines = [
        f"{contact['name']} <{contact['email']}> replied to your outreach email.",
        "",
        f"What they want: {intent_label or 'not classified'}" + (f". {what}" if what else ""),
        "",
        "Their message:",
        (theirs.get("body") or theirs.get("snippet") or "").strip() or "(no text)",
        "",
        "A suggested response is waiting in Review for your approval." if has_draft
        else "No suggested response was drafted. Answer from your mailbox, then set their status in the app.",
    ]
    subject = f"Reply from {contact['name']}" + (f": {intent_label}" if intent_label else "")
    return send(subject, "\n".join(lines))
