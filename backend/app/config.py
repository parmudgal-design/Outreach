"""Process-level configuration. Everything a user sets day to day (API keys,
sender details, campaign pacing) lives in the database and is edited from the
UI; only paths and bind options come from the environment."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = Path(os.environ.get("OUTREACH_DATA_DIR", ROOT / "data"))
DB_PATH = Path(os.environ.get("OUTREACH_DB", DATA_DIR / "outreach.db"))
FRONTEND_DIR = ROOT / "frontend"

# Optional HTTP basic-auth password for the whole app. Leave unset for local
# use; set it before exposing the server on a network.
APP_PASSWORD = os.environ.get("OUTREACH_PASSWORD", "")

# Seconds between background worker ticks.
WORKER_TICK_SECONDS = float(os.environ.get("OUTREACH_TICK_SECONDS", "15"))
# Set to "0" to run the API without the background worker (tests do this).
WORKER_ENABLED = os.environ.get("OUTREACH_WORKER", "1") != "0"
