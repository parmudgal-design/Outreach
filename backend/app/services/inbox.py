"""Incoming mail: reply, bounce, auto-reply and unsubscribe detection over IMAP.

`classify` is pure (bytes in, verdict out) so it can be tested without a mail
server; `fetch_new` does the IMAP I/O. The worker matches verdicts to contacts
by the Message-IDs we sent with, falling back to the sender's address.
"""
import email
import imaplib
import re
from datetime import datetime, timedelta
from email import policy
from email.utils import getaddresses, parseaddr

MSGID_RE = re.compile(r"<[^<>\s@]+@[^<>\s]+>")
RECIPIENT_RE = re.compile(r"^(?:Final|Original)-Recipient:\s*rfc822;\s*<?([^\s>]+@[^\s>]+)>?", re.I | re.M)
BOUNCE_SUBJECT_RE = re.compile(r"undeliverable|delivery status notification|mail delivery (failed|subsystem)|returned mail|failure notice|delivery has failed", re.I)
AUTO_SUBJECT_RE = re.compile(r"^(automatic reply|auto(-|\s)?reply|out of (the )?office|auto:|away:|on leave)", re.I)
UNSUB_RE = re.compile(
    r"\b(unsubscribe|remove me|take me off|stop (emailing|sending|contacting)|do not (email|contact)|don'?t (email|contact) me|opt(-|\s)?out)\b",
    re.I,
)
QUOTE_START_RE = re.compile(r"^(on .+wrote:|-{2,}\s*original message|from:\s|sent from my )", re.I)


def _text(msg) -> str:
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain" and not part.get_filename():
                try:
                    return part.get_content()
                except Exception:
                    continue
        for part in msg.walk():
            if part.get_content_type() == "text/html":
                try:
                    return re.sub(r"<[^>]+>", " ", part.get_content())
                except Exception:
                    continue
        return ""
    try:
        return msg.get_content()
    except Exception:
        return str(msg.get_payload())


def new_text(body: str) -> str:
    """The part of a reply the person actually wrote (above the quoted original)."""
    out = []
    for line in body.splitlines():
        s = line.strip()
        if s.startswith(">") or QUOTE_START_RE.match(s):
            break
        out.append(line)
    return "\n".join(out).strip()


def classify(raw: bytes) -> dict:
    msg = email.message_from_bytes(raw, policy=policy.default)
    from_name, from_addr = parseaddr(str(msg.get("From", "")))
    subject = str(msg.get("Subject", ""))
    body = _text(msg) or ""
    refs = MSGID_RE.findall(" ".join(str(msg.get(h, "")) for h in ("In-Reply-To", "References")))
    local = from_addr.split("@")[0].lower()

    is_report = msg.get_content_type() == "multipart/report" and "delivery-status" in str(msg.get("Content-Type", "")).lower()
    if is_report or local in ("mailer-daemon", "postmaster") or BOUNCE_SUBJECT_RE.search(subject):
        full = raw.decode("utf-8", errors="replace")
        return {
            "kind": "bounce",
            "from_addr": from_addr,
            "subject": subject,
            "snippet": re.sub(r"\s+", " ", body)[:300],
            "refs": refs + MSGID_RE.findall(full),
            "recipients": [r.lower() for r in RECIPIENT_RE.findall(full)],
        }

    auto = str(msg.get("Auto-Submitted", "no")).lower()
    if (auto and auto != "no") or msg.get("X-Autoreply") or msg.get("X-Autorespond") or \
            str(msg.get("Precedence", "")).lower() in ("auto_reply", "bulk", "junk") or AUTO_SUBJECT_RE.search(subject):
        kind = "auto_reply"
    else:
        written = new_text(body)
        kind = "unsubscribe" if (UNSUB_RE.search(written) or UNSUB_RE.search(subject)) else "reply"
    written = new_text(body)
    return {
        "kind": kind,
        "from_addr": from_addr.lower(),
        "subject": subject,
        "snippet": re.sub(r"\s+", " ", written)[:300],
        "text": written[:6000],
        "message_id": (MSGID_RE.findall(str(msg.get("Message-ID", ""))) or [None])[0],
        "refs": refs,
        "recipients": [],
    }


class InboxError(Exception):
    pass


def _login(settings: dict) -> imaplib.IMAP4_SSL:
    user = settings.get("imap_username") or settings.get("smtp_username")
    password = settings.get("imap_password") or settings.get("smtp_password")
    try:
        imap = imaplib.IMAP4_SSL(settings["imap_host"], int(settings.get("imap_port", 993)), timeout=30)
        imap.login(user, password)
        return imap
    except imaplib.IMAP4.error as e:
        raise InboxError(f"IMAP login failed — check the username and app password ({e}).") from e
    except OSError as e:
        raise InboxError(f"Couldn't connect to {settings.get('imap_host')}: {e}") from e


def test(settings: dict) -> str:
    imap = _login(settings)
    try:
        typ, _ = imap.select(settings.get("imap_folder") or "INBOX", readonly=True)
        if typ != "OK":
            raise InboxError(f"Folder {settings.get('imap_folder')!r} not found.")
        return f"Connected to {settings['imap_host']} and opened {settings.get('imap_folder') or 'INBOX'}."
    finally:
        imap.logout()


def fetch_new(settings: dict, last_uid_key: str | None, since_days: int = 30, limit: int = 200) -> list[tuple[str, bytes]]:
    """Messages newer than `last_uid_key` ("<uidvalidity>:<uid>"), oldest first.
    Uses BODY.PEEK so nothing is marked as read."""
    imap = _login(settings)
    try:
        typ, _ = imap.select(settings.get("imap_folder") or "INBOX", readonly=True)
        if typ != "OK":
            raise InboxError(f"Folder {settings.get('imap_folder')!r} not found.")
        uidvalidity = (imap.response("UIDVALIDITY")[1] or [b"0"])[0].decode()
        last_uid = 0
        if last_uid_key and last_uid_key.split(":")[0] == uidvalidity:
            last_uid = int(last_uid_key.split(":")[1])
        if last_uid:
            typ, data = imap.uid("SEARCH", None, f"UID {last_uid + 1}:*")
        else:
            since = (datetime.now() - timedelta(days=since_days)).strftime("%d-%b-%Y")
            typ, data = imap.uid("SEARCH", None, f"SINCE {since}")
        uids = [int(u) for u in (data[0] or b"").split() if int(u) > last_uid][:limit]
        out = []
        for uid in uids:
            typ, fetched = imap.uid("FETCH", str(uid), "(BODY.PEEK[])")
            raw = next((part[1] for part in fetched if isinstance(part, tuple)), None)
            if raw:
                out.append((f"{uidvalidity}:{uid}", raw))
        return out
    finally:
        try:
            imap.logout()
        except Exception:
            pass


def all_addresses(value: str) -> list[str]:
    return [a.lower() for _, a in getaddresses([value]) if a]
