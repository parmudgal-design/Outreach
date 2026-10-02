"""Per-user logins: scrypt password hashes, server-side sessions, startup safety checks.

Rules (see README → Security):
  * once at least one user exists, every API call needs a signed-in session;
  * with no users, the app is open only to requests from this machine, and
    refuses to start at all on a non-localhost HOST;
  * on a non-localhost HOST it also refuses to start without OUTREACH_SECRET_KEY.
"""
import base64
import hashlib
import hmac
import secrets
import time

from . import config, db, secretbox, settings_store

COOKIE = "outreach_session"
MIN_PASSWORD = 10
ROLES = ("admin", "member")
# scrypt cost: ~16 MB and a few tens of ms per hash.
N, R, P = 2 ** 14, 8, 1

# Failed logins per email, to slow down guessing: (count, first failure time).
_failures: dict[str, tuple[int, float]] = {}
MAX_FAILURES, FAILURE_WINDOW = 5, 15 * 60


class AuthError(Exception):
    pass


# --- Passwords ---------------------------------------------------------------

def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=N, r=R, p=P, dklen=32)
    b64 = lambda b: base64.b64encode(b).decode()
    return f"scrypt${N}${R}${P}${b64(salt)}${b64(digest)}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, n, r, p, salt, digest = stored.split("$")
        if algo != "scrypt":
            return False
        expected = base64.b64decode(digest)
        got = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt), n=int(n), r=int(r), p=int(p), dklen=len(expected))
        return hmac.compare_digest(got, expected)
    except (ValueError, TypeError):
        return False


def check_password_rules(password: str) -> None:
    if len(password or "") < MIN_PASSWORD:
        raise AuthError(f"Passwords need at least {MIN_PASSWORD} characters.")


# --- Users -------------------------------------------------------------------

def public(user: dict | None) -> dict | None:
    if not user:
        return None
    return {k: user[k] for k in ("id", "email", "name", "role", "created_at", "last_login_at")}


def user_count(conn) -> int:
    return conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]


def create_user(conn, email: str, name: str, password: str, role: str = "member") -> dict:
    email = (email or "").strip().lower()
    if "@" not in email:
        raise AuthError("That email address doesn't look valid.")
    if role not in ROLES:
        raise AuthError(f"Role must be one of {', '.join(ROLES)}.")
    check_password_rules(password)
    if conn.execute("SELECT 1 FROM users WHERE email = ?", (email,)).fetchone():
        raise AuthError("A user with that email already exists.")
    cur = conn.execute("INSERT INTO users (email, name, password_hash, role, created_at) VALUES (?, ?, ?, ?, ?)",
                       (email, (name or "").strip(), hash_password(password), role, time.time()))
    db.log_event("user", f"User {email} created ({role})", conn=conn)
    return public(db.one(conn.execute("SELECT * FROM users WHERE id = ?", (cur.lastrowid,))))


def set_password(conn, user_id: int, password: str) -> None:
    check_password_rules(password)
    conn.execute("UPDATE users SET password_hash = ? WHERE id = ?", (hash_password(password), user_id))
    conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))  # sign out everywhere


def delete_user(conn, user_id: int, by: dict | None) -> None:
    u = db.one(conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)))
    if not u:
        raise AuthError("User not found.")
    if by and by["id"] == user_id:
        raise AuthError("You can't remove yourself.")
    if u["role"] == "admin" and conn.execute("SELECT COUNT(*) FROM users WHERE role = 'admin'").fetchone()[0] <= 1:
        raise AuthError("That's the last admin — make someone else an admin first.")
    conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
    db.log_event("user", f"User {u['email']} removed", conn=conn)


# --- Sessions ----------------------------------------------------------------

def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


_DUMMY: list[str] = []


def _dummy_hash() -> str:
    if not _DUMMY:
        _DUMMY.append(hash_password(secrets.token_urlsafe(16)))
    return _DUMMY[0]


def login(conn, email: str, password: str) -> tuple[str, dict]:
    email = (email or "").strip().lower()
    count, first = _failures.get(email, (0, 0.0))
    if count >= MAX_FAILURES and time.time() - first < FAILURE_WINDOW:
        raise AuthError("Too many failed sign-ins. Wait 15 minutes and try again.")
    u = db.one(conn.execute("SELECT * FROM users WHERE email = ?", (email,)))
    # Hash even for unknown emails so response time doesn't reveal which accounts exist.
    ok = verify_password(password or "", u["password_hash"] if u else _dummy_hash())
    if not (u and ok):
        _failures[email] = (count + 1, first if count and time.time() - first < FAILURE_WINDOW else time.time())
        raise AuthError("Wrong email or password.")
    _failures.pop(email, None)
    token = secrets.token_urlsafe(32)
    now = time.time()
    conn.execute("DELETE FROM sessions WHERE expires_at < ?", (now,))
    conn.execute("INSERT INTO sessions (token_hash, user_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
                 (_token_hash(token), u["id"], now, now + config.SESSION_DAYS * 86400))
    conn.execute("UPDATE users SET last_login_at = ? WHERE id = ?", (now, u["id"]))
    db.log_event("user", f"{u['email']} signed in", conn=conn, actor=u["email"])
    return token, public(u)


def user_for_token(conn, token: str | None) -> dict | None:
    if not token:
        return None
    return public(db.one(conn.execute(
        "SELECT u.* FROM sessions s JOIN users u ON u.id = s.user_id WHERE s.token_hash = ? AND s.expires_at > ?",
        (_token_hash(token), time.time()),
    )))


def logout(conn, token: str | None) -> None:
    if token:
        conn.execute("DELETE FROM sessions WHERE token_hash = ?", (_token_hash(token),))


# --- Startup -----------------------------------------------------------------

def startup_checks() -> None:
    """Called once at startup, after db.init(). Raises to stop the app from starting unsafely."""
    with db.connect() as conn:
        settings_store.migrate_secrets(conn)  # raises SecretKeyError on a missing/wrong key
        users = user_count(conn)
    if config.is_local_bind():
        return
    if not users:
        raise RuntimeError(
            f"Refusing to start on HOST={config.HOST}: no users exist, so anyone who can reach it could use it. "
            "Create one first: python -m backend.app.cli create-admin --email you@example.com --name \"Your Name\""
        )
    if not secretbox.enabled():
        raise RuntimeError(
            f"Refusing to start on HOST={config.HOST} without OUTREACH_SECRET_KEY (it encrypts the API key and mail "
            "passwords). Generate one: python -m backend.app.cli gen-key"
        )


def warnings(conn) -> list[str]:
    out = []
    if not secretbox.enabled():
        out.append("Secrets (API key, mail passwords) are stored unencrypted because OUTREACH_SECRET_KEY isn't set. "
                   "Fine on your own computer; set it before running this anywhere else.")
    if not user_count(conn):
        out.append("No user accounts yet, so anyone on this computer can open the app. Add one under Setup → Users.")
    return out
