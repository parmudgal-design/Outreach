"""All user-editable configuration, stored as one JSON document per section.

Secrets (API key, mail passwords) are write-only from the UI's point of view:
reads return a masked hint, and an update that leaves a secret blank keeps the
stored value.
"""
import copy

from . import db

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
    # TODO(founder): the marketplace wording below is a placeholder until the founder supplies the real
    # value propositions. While it's unchanged the pre-send check shows a warning (qa.py).
    "marketplace": {
        "value_prop_brands": (
            "Dr.Tracker is where people actively managing their health discover the products and professionals "
            "that help them. Brands that list with us reach that audience at the moment they're making health decisions."
        ),
        "value_prop_professionals": (
            "Dr.Tracker helps people who are working on their health find the right professionals. Practices that "
            "list with us are found by people looking for exactly the care they offer."
        ),
        "mention_trial": False,     # off: no prices, fees, trials or offers at all
        "trial_text": "We're offering early partners a free first month to try it.",
        "signature_name": "",       # blank = the sender name (Setup → Sender)
        "signature_role": "",       # blank = the sender role
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
    },
    "campaign": {
        "running": False,
        "require_approval": True,   # a human approves each draft before it can be sent
        "auto_draft": True,         # worker drafts new contacts in the background
        "daily_cap": 20,
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
    if section == "pitches":
        merged = copy.deepcopy(DEFAULTS["pitches"])
        for t, v in (stored or {}).items():
            if t in merged and isinstance(v, dict):
                merged[t].update(v)
        return merged
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
    return out


def update_section(section: str, patch: dict, conn=None) -> dict:
    if section not in DEFAULTS:
        raise KeyError(section)
    if conn is None:
        with db.connect() as c:
            return update_section(section, patch, c)
    current = get_section(section, conn)
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
    db.set_json(conn, f"section:{section}", current)
    return current
