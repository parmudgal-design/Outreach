"""Process-level configuration. Everything a user sets day to day (API keys,
sender details, campaign pacing) lives in the database and is edited from the
UI; only paths and bind options come from the environment."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
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
