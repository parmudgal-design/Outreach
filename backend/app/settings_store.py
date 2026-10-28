"""All user-editable configuration, stored as one JSON document per section.

Secrets (API key, mail passwords) are write-only from the UI's point of view:
reads return a masked hint, and an update that leaves a secret blank keeps the
stored value.
"""
import copy
import os

from . import db, secretbox

SECRET_FIELDS = {
    ("ai", "anthropic_api_key"),
    ("email", "smtp_password"),
    ("email", "imap_password"),
}

DEFAULTS: dict[str, dict] = {
    "sender": {
        "name": "",
        "role": "",
        "email": "",
        "phone": "",
        "tone": "warm",          # direct | warm | formal
        "language": "english",   # english | hinglish | hindi
        "address": "",           # registered/business address for the compliance footer
    },
    "pitches": {
        "doctor":   {"pitch": "", "props": []},
        "hospital": {"pitch": "", "props": []},
        "company":  {"pitch": "", "props": []},
        "partner":  {"pitch": "", "props": []},
    },
    # Brand and professional (marketplace) emails, editable on Setup → "Marketplace pitch".
    # Each value proposition sits in the email just before a sentence the template adds about the
    # recipient's own audience, so it says what Dr.Tracker is and why a listing is worth it, and
    # nothing more. Only claims Dr.Tracker can stand behind: no user numbers, prices or promises.
    "marketplace": {
        "value_prop_brands": (
            "Dr.Tracker is an app people use to keep track of their health day to day, and its marketplace is where "
            "they discover products that fit what they're working on. Broad paid ads reach a lot of people who "
            "aren't looking; a listing on Dr.Tracker reaches people who already are."
        ),
        "value_prop_professionals": (
            "Dr.Tracker is an app people use to keep track of their health, and its marketplace helps them find the "
            "right professional when they need one. We take care of the visibility, so being found doesn't become "
            "another job on your list."
        ),
        "mention_trial": False,     # off: no prices, fees, trials or offers at all
        "trial_text": "We're offering early partners a free first month to try it.",
        "signature_name": "",       # blank = the sender name (Setup → Sender)
        "signature_role": "",       # blank = the sender role
        # Ends the ask in emails written to a team guide (the call link in brand_outreach_agent.md).
        # Blank: the ask ends with a question instead.
        "meeting_link": "",
    },
    "import": {
        # Rows whose Name or Notes match any of these are never imported: crisis and government
        # helplines must not receive marketing email. Matched as whole words, case-insensitive.
        "blocked_patterns": [
            "helpline", "help line", "crisis", "suicide", "suicide prevention", "tele-manas", "telemanas", "tele manas",
            "icall", "sumaitri", "vandrevala", "aasra", "sneha india", "lifeline", "hotline", "kiran helpline",
            "toll-free helpline", "national helpline", "government helpline",
        ],
    },
    "ai": {
        "anthropic_api_key": "",
        "model": "claude-opus-5-5",
        "effort": "medium",
        # Read each brand's or professional's website before Claude writes their first email.
        "website_research": True,
        # When someone replies: work out what they're asking and draft a response for approval.
        "reply_drafts": True,
    },
    "email": {
        # dry_run writes every "sent" email to the local outbox instead of sending.
        "mode": "dry_run",       # dry_run | smtp
        "smtp_host": "smtp.gmail.com",
        "smtp_port": 465,
        "smtp_security": "ssl",  # ssl | starttls | none
        "smtp_username": "",
        "smtp_password": "",
        "from_email": "",
        "from_name": "",
        "reply_to": "",
        "imap_enabled": False,
        "imap_host": "imap.gmail.com",
        "imap_port": 993,
        "imap_username": "",     # blank = same as SMTP username
        "imap_password": "",     # blank = same as SMTP password
        "imap_folder": "INBOX",
        # Where reply alerts go (a person's inbox, not the sending mailbox). Blank = no alerts.
        "alert_to": "",
    },
    "campaign": {
        "running": False,
        "require_approval": True,   # a human approves each draft before it can be sent
        "auto_draft": True,         # worker drafts new contacts in the background
        "daily_cap": 0,            # 0 = no daily limit
        "min_delay_seconds": 90,
        "jitter_seconds": 60,
        "ist_window": True,
        "window_start_hour": 10,
        "window_end_hour": 18,
        "send_days": [0, 1, 2, 3, 4, 5],   # Mon..Sat (Python weekday numbers)
        "followups_enabled": True,
        "followup_after_days": 4,
        "max_followups": 1,
        "inbox_poll_minutes": 5,
    },
}


# Settings that can come from the environment / .env instead of the UI. A non-empty variable wins
# over the value saved in the database; the Connections page shows those fields as "set in .env".
# Secrets set this way are never written to the database.
ENV_FIELDS: dict[str, tuple[str, str, type]] = {
    "ANTHROPIC_API_KEY": ("ai", "anthropic_api_key", str),
    "ANTHROPIC_MODEL": ("ai", "model", str),
    "EMAIL_MODE": ("email", "mode", str),
    "SMTP_HOST": ("email", "smtp_host", str),
    "SMTP_PORT": ("email", "smtp_port", int),
    "SMTP_SECURITY": ("email", "smtp_security", str),
    "SMTP_USERNAME": ("email", "smtp_username", str),
    "SMTP_PASSWORD": ("email", "smtp_password", str),
    "FROM_EMAIL": ("email", "from_email", str),
    "FROM_NAME": ("email", "from_name", str),
    "REPLY_TO": ("email", "reply_to", str),
    "IMAP_ENABLED": ("email", "imap_enabled", bool),
    "IMAP_HOST": ("email", "imap_host", str),
    "IMAP_PORT": ("email", "imap_port", int),
    "IMAP_USERNAME": ("email", "imap_username", str),
    "IMAP_PASSWORD": ("email", "imap_password", str),
    "IMAP_FOLDER": ("email", "imap_folder", str),
    "ALERT_TO": ("email", "alert_to", str),
}
_CHOICES = {("email", "mode"): {"dry_run", "smtp"}, ("email", "smtp_security"): {"ssl", "starttls", "none"}}


def env_overrides(section: str) -> dict:
    """The settings for `section` that the environment sets. Raises ValueError on a malformed value."""
    out = {}
    for var, (sec, field, kind) in ENV_FIELDS.items():
        raw = os.environ.get(var, "").strip()
        if sec != section or not raw:
            continue
        if kind is int:
            try:
                value = int(raw)
            except ValueError:
                raise ValueError(f"{var} in .env must be a number, not {raw!r}") from None
        elif kind is bool:
            if raw.lower() not in {"1", "0", "true", "false", "yes", "no", "on", "off"}:
                raise ValueError(f"{var} in .env must be true or false, not {raw!r}")
            value = raw.lower() in {"1", "true", "yes", "on"}
        else:
            value = raw
        allowed = _CHOICES.get((sec, field))
        if allowed and value not in allowed:
            raise ValueError(f"{var} in .env must be one of {', '.join(sorted(allowed))}, not {raw!r}")
        out[field] = value
    return out


def _merge(defaults: dict, stored: dict) -> dict:
    out = copy.deepcopy(defaults)
    for k, v in (stored or {}).items():
        if isinstance(out.get(k), dict) and isinstance(v, dict) and k != "pitches":
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def get_section(section: str, conn=None) -> dict:
    if conn is None:
        with db.connect() as c:
            return get_section(section, c)
    stored = db.get_json(conn, f"section:{section}", {})
    stored = {k: (secretbox.decrypt(v) if (section, k) in SECRET_FIELDS else v) for k, v in (stored or {}).items()}
    if section == "pitches":
        merged = copy.deepcopy(DEFAULTS["pitches"])
        for t, v in (stored or {}).items():
            if t in merged and isinstance(v, dict):
                merged[t].update(v)
        return merged
    return {**_merge(DEFAULTS[section], stored), **env_overrides(section)}


def _get_stored(section: str, conn) -> dict:
    """Defaults + database values, without the environment (what the UI saves into)."""
    stored = db.get_json(conn, f"section:{section}", {})
    stored = {k: (secretbox.decrypt(v) if (section, k) in SECRET_FIELDS else v) for k, v in (stored or {}).items()}
    return _merge(DEFAULTS[section], stored)


def get_all(conn=None) -> dict:
    return {s: get_section(s, conn) for s in DEFAULTS}


def mask(secret: str) -> str:
    if not secret:
        return ""
    return "•" * 8 + secret[-4:] if len(secret) > 8 else "•" * 8


def public_view(conn=None) -> dict:
    """Settings as the UI may see them: secrets replaced by a mask + a has_ flag."""
    out = get_all(conn)
    for section, field in SECRET_FIELDS:
        value = out[section].get(field, "")
        out[section][field] = ""
        out[section][f"{field}_set"] = bool(value)
        out[section][f"{field}_hint"] = mask(value)
    for section in DEFAULTS:
        if section != "pitches":
            out[section]["from_env"] = sorted(env_overrides(section))
    return out


def update_section(section: str, patch: dict, conn=None) -> dict:
    if section not in DEFAULTS:
        raise KeyError(section)
    if conn is None:
        with db.connect() as c:
            return update_section(section, patch, c)
    # Save on top of the database values, so a value from .env is never copied into the database.
    current = get_section(section, conn) if section == "pitches" else _get_stored(section, conn)
    for key, value in patch.items():
        if key.endswith("_set") or key.endswith("_hint"):
            continue
        if (section, key) in SECRET_FIELDS:
            # Blank means "unchanged"; the explicit clear flag removes it.
            if value in (None, ""):
                continue
        if section != "pitches" and key not in DEFAULTS[section]:
            continue
        current[key] = value
    for section_name, field in SECRET_FIELDS:
        if section_name == section and patch.get(f"clear_{field}"):
            current[field] = ""
    db.set_json(conn, f"section:{section}", _sealed(section, current))
    return {**current, **env_overrides(section)}


def _sealed(section: str, values: dict) -> dict:
    return {k: (secretbox.encrypt(v) if (section, k) in SECRET_FIELDS else v) for k, v in values.items()}


def migrate_secrets(conn) -> dict:
    """Run at startup. With a key: encrypt any plaintext secrets. Without one: refuse if any are encrypted.
    Either way, prove every encrypted value decrypts, so a wrong key fails at startup, not mid-campaign."""
    encrypted = plaintext = 0
    for section in {s for s, _ in SECRET_FIELDS}:
        stored = db.get_json(conn, f"section:{section}", None)
        if not stored:
            continue
        for (sec, field) in SECRET_FIELDS:
            if sec != section or not stored.get(field):
                continue
            if secretbox.is_encrypted(stored[field]):
                secretbox.decrypt(stored[field])  # raises SecretKeyError if the key is missing or wrong
                encrypted += 1
            else:
                plaintext += 1
        if secretbox.enabled() and plaintext:
            db.set_json(conn, f"section:{section}", _sealed(section, stored))
    migrated = plaintext if secretbox.enabled() else 0
    if migrated:
        db.log_event("security", f"Encrypted {migrated} stored secret(s) with OUTREACH_SECRET_KEY", conn=conn)
    return {"encrypted": encrypted + migrated, "plaintext": plaintext - migrated, "migrated": migrated}
