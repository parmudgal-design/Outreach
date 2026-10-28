import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.environ["OUTREACH_WORKER"] = "0"
# Never read the developer's real .env or shell keys: tests must not send mail or call Claude.
os.environ["OUTREACH_ENV_FILE"] = ""
from backend.app.settings_store import ENV_FIELDS  # noqa: E402

for _var in ENV_FIELDS:
    os.environ.pop(_var, None)


@pytest.fixture()
def app_client(tmp_path, monkeypatch):
    from backend.app import config, db

    monkeypatch.setattr(config, "DB_PATH", tmp_path / "test.db")
    db.init()
    from fastapi.testclient import TestClient
    from backend.app.main import app

    # Connect as this machine: with no user accounts the app is only open to localhost.
    with TestClient(app, client=("127.0.0.1", 50000)) as client:
        yield client


@pytest.fixture()
def configured(app_client):
    """Sender + pitch filled in, approval on, campaign window off so tests run any time."""
    c = app_client
    c.put("/api/settings/sender", json={
        "name": "Ananya Gupta", "role": "Founder, Dr.Tracker", "email": "ananya@drtracker.test",
        "phone": "+91 98765 43210", "tone": "warm", "address": "HSR Layout, Bengaluru 560102",
    })
    c.put("/api/settings/campaign", json={"ist_window": False, "min_delay_seconds": 0, "jitter_seconds": 0})
    # The brand guide (brand_outreach_agent.md) won't write without a call link.
    c.put("/api/settings/marketplace", json={"meeting_link": "https://cal.example/drtracker"})
    return c
