"""Encryption at rest for the secrets in the settings table (API key, SMTP/IMAP passwords).

Values are stored as "enc:v1:<Fernet token>" when OUTREACH_SECRET_KEY is set.
Without a key (local use only) they stay plaintext and the UI shows a warning;
the app refuses to start if encrypted values exist but the key is missing or
wrong, rather than silently losing them.
"""
from cryptography.fernet import Fernet, InvalidToken

from . import config

PREFIX = "enc:v1:"


class SecretKeyError(RuntimeError):
    pass


def generate_key() -> str:
    return Fernet.generate_key().decode()


def enabled() -> bool:
    return bool(config.SECRET_KEY)


def _fernet() -> Fernet | None:
    if not config.SECRET_KEY:
        return None
    try:
        return Fernet(config.SECRET_KEY.encode())
    except (ValueError, TypeError) as e:
        raise SecretKeyError(
            "OUTREACH_SECRET_KEY isn't a valid key. Generate one with: python -m backend.app.cli gen-key"
        ) from e


def is_encrypted(value) -> bool:
    return isinstance(value, str) and value.startswith(PREFIX)


def encrypt(value: str) -> str:
    if not value or is_encrypted(value):
        return value
    f = _fernet()
    return PREFIX + f.encrypt(value.encode()).decode() if f else value


def decrypt(value: str) -> str:
    if not is_encrypted(value):
        return value
    f = _fernet()
    if f is None:
        raise SecretKeyError(
            "Stored secrets are encrypted but OUTREACH_SECRET_KEY isn't set. Start the app with the key "
            "that encrypted them (or clear the secrets and re-enter them)."
        )
    try:
        return f.decrypt(value[len(PREFIX):].encode()).decode()
    except InvalidToken as e:
        raise SecretKeyError("OUTREACH_SECRET_KEY doesn't match the key the stored secrets were encrypted with.") from e
