"""Process-level configuration. Everything a user sets day to day (sender
details, campaign pacing) lives in the database and is edited from the UI.
Paths, bind options and, optionally, the API key and mailbox come from the
environment or the `.env` file in the repository root (see settings_store.ENV_FIELDS)."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def load_env_file(path: Path) -> None:
    """Read KEY=value lines into os.environ. Variables already set in the real environment win.
    Supports comments, blank lines, `export KEY=value` and single or double quotes."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip().removeprefix("export ").strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        elif " #" in value:
            value = value.split(" #", 1)[0].rstrip()
        if key:
            os.environ.setdefault(key, value)


# OUTREACH_ENV_FILE picks another file; set it to an empty string to read none (the tests do).
_env_file = os.environ.get("OUTREACH_ENV_FILE", str(ROOT / ".env"))
if _env_file:
    load_env_file(Path(_env_file))
DATA_DIR = Path(os.environ.get("OUTREACH_DATA_DIR", ROOT / "data"))
DB_PATH = Path(os.environ.get("OUTREACH_DB", DATA_DIR / "outreach.db"))
FRONTEND_DIR = ROOT / "frontend"

# The address the server listens on (run.sh and the Dockerfile pass it to uvicorn too).
# Anything other than localhost counts as "exposed": the app then refuses to start
# without at least one user and without OUTREACH_SECRET_KEY.
HOST = os.environ.get("HOST", "127.0.0.1")
LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}

# Fernet key that encrypts the API key and mail passwords in the database
# (`python -m backend.app.cli gen-key`). Optional on localhost.
SECRET_KEY = os.environ.get("OUTREACH_SECRET_KEY", "").strip()

# Mark the session cookie Secure (set behind HTTPS).
COOKIE_SECURE = os.environ.get("OUTREACH_COOKIE_SECURE", "0") == "1"
SESSION_DAYS = int(os.environ.get("OUTREACH_SESSION_DAYS", "14"))


def is_local_bind() -> bool:
    return HOST in LOCAL_HOSTS

# The address people open the app at (e.g. https://outreach.dr-tracker.com), used for links in alert emails.
PUBLIC_URL = os.environ.get("OUTREACH_PUBLIC_URL", "").strip().rstrip("/")

# Seconds between background worker ticks.
WORKER_TICK_SECONDS = float(os.environ.get("OUTREACH_TICK_SECONDS", "15"))
# Set to "0" to run the API without the background worker (tests do this).
WORKER_ENABLED = os.environ.get("OUTREACH_WORKER", "1") != "0"
