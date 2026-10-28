"""Outgoing mail.

SMTP covers Gmail / Google Workspace (with an App Password), Zoho, Outlook and
every transactional provider's SMTP relay (SES, SendGrid, Postmark, Brevo…),
so moving to a proper sending domain later is a settings change, not a code
change. Dry-run mode builds the exact same message but only records it.
"""
import html
import re
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid

from . import templates


class SendError(Exception):
    def __init__(self, message: str, permanent: bool = False):
        super().__init__(message)
        # Permanent = this recipient will never accept it (bad address); don't retry.
        self.permanent = permanent


def build_message(
    *,
    from_email: str,
    from_name: str,
    to_email: str,
    to_name: str,
    subject: str,
    body: str,
    reply_to: str = "",
    in_reply_to: str | None = None,
    images: "list[templates.Banner] | None" = None,
) -> EmailMessage:
    msg = EmailMessage()
    domain = from_email.split("@")[-1] if "@" in from_email else "localhost"
    msg["Message-ID"] = make_msgid(domain=domain)
    msg["Date"] = formatdate(localtime=True)
    msg["From"] = formataddr((from_name, from_email)) if from_name else from_email
    msg["To"] = formataddr((to_name, to_email)) if to_name else to_email
    msg["Subject"] = subject
    if reply_to:
        msg["Reply-To"] = reply_to
    # Lets mail clients show a native "unsubscribe" button; replies land in the inbox the worker polls.
    unsub_to = reply_to or from_email
    msg["List-Unsubscribe"] = f"<mailto:{unsub_to}?subject=unsubscribe>"
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
        msg["References"] = in_reply_to
    if not images:
        msg.set_content(body)
        return msg
    # With images: plain text for clients that don't show HTML, and HTML with the images inline (cid:), in
    # order where the marker is, so they show without "load remote images" and nothing is fetched when it's opened.
    parts = templates.segments(body, len(images))
    msg.set_content("\n\n".join(p.strip() for p in parts if isinstance(p, str)))
    cids = [make_msgid(domain=domain) for _ in images]
    msg.add_alternative(_html(parts, [(c[1:-1], img) for c, img in zip(cids, images)]), subtype="html")
    for cid, img in zip(cids, images):
        # "inline", so mail clients show it in the body and don't list it as an attachment too.
        msg.get_payload()[1].add_related(img.data, maintype="image", subtype=img.subtype, cid=cid,
                                         disposition="inline", filename=img.filename)
    return msg


def _paragraphs(text: str) -> str:
    out = []
    for para in re.split(r"\n\s*\n", text.strip()):
        if para.strip():
            lines = [html.escape(line) for line in para.strip().splitlines()]
            out.append('<p style="margin:0 0 14px">' + "<br>\n".join(lines) + "</p>")
    return "\n".join(out)


def _html(parts: list, images: "list[tuple[str, templates.Banner]]") -> str:
    # Shown at up to 600px wide; each image itself is EMAIL_WIDTH for sharp text on high-density screens.
    def img(i: int) -> str:
        cid, b = images[i]
        return (f'<p style="margin:0 0 14px"><img src="cid:{cid}" alt="{html.escape(b.alt)}" width="600" '
                'style="display:block;width:100%;max-width:600px;height:auto;border:0"></p>')
    inner = "\n".join(img(p) if isinstance(p, int) else _paragraphs(p) for p in parts)
    return ('<!doctype html><html><body style="margin:0;padding:0">'
            '<div style="max-width:600px;font-family:Arial,Helvetica,sans-serif;font-size:15px;line-height:1.5;color:#1d2433">\n'
            f"{inner}\n</div></body></html>")


class DryRunTransport:
    name = "dry_run"

    def send(self, msg: EmailMessage) -> None:
        return None

    def test(self) -> str:
        return "Dry-run mode: nothing is sent. Emails appear in Activity → Outbox."


class SMTPTransport:
    name = "smtp"

    def __init__(self, host: str, port: int, security: str, username: str, password: str, timeout: int = 30):
        self.host, self.port, self.security = host, int(port), security
        self.username, self.password, self.timeout = username, password, timeout

    def _open(self) -> smtplib.SMTP:
        ctx = ssl.create_default_context()
        if self.security == "ssl":
            server = smtplib.SMTP_SSL(self.host, self.port, timeout=self.timeout, context=ctx)
        else:
            server = smtplib.SMTP(self.host, self.port, timeout=self.timeout)
            server.ehlo()
            if self.security == "starttls":
                server.starttls(context=ctx)
                server.ehlo()
        if self.username:
            server.login(self.username, self.password)
        return server

    def send(self, msg: EmailMessage) -> None:
        try:
            with self._open() as server:
                server.send_message(msg)
        except smtplib.SMTPRecipientsRefused as e:
            raise SendError(f"Recipient refused: {e.recipients}", permanent=True) from e
        except smtplib.SMTPAuthenticationError as e:
            raise SendError("SMTP login failed — check the username and app password under Connections.") from e
        except smtplib.SMTPResponseException as e:
            permanent = 500 <= e.smtp_code < 600 and e.smtp_code not in (421, 450, 451, 452)
            raise SendError(f"SMTP error {e.smtp_code}: {e.smtp_error!r}", permanent=permanent) from e
        except (smtplib.SMTPException, OSError) as e:
            raise SendError(f"Couldn't send via {self.host}: {e}") from e

    def test(self) -> str:
        try:
            with self._open():
                pass
        except smtplib.SMTPAuthenticationError as e:
            raise SendError("SMTP login failed — check the username and app password.") from e
        except (smtplib.SMTPException, OSError) as e:
            raise SendError(f"Couldn't connect to {self.host}:{self.port} — {e}") from e
        return f"Connected and logged in to {self.host}."


def get_transport(email_settings: dict):
    if email_settings.get("mode") == "smtp":
        return SMTPTransport(
            email_settings["smtp_host"],
            email_settings["smtp_port"],
            email_settings.get("smtp_security", "ssl"),
            email_settings.get("smtp_username", ""),
            email_settings.get("smtp_password", ""),
        )
    return DryRunTransport()


def from_address(email_settings: dict, sender: dict) -> tuple[str, str]:
    email = email_settings.get("from_email") or email_settings.get("smtp_username") or sender.get("email") or ""
    name = email_settings.get("from_name") or sender.get("name") or ""
    return email, name
