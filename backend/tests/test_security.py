"""Per-user logins, encrypted secrets and the startup safety rules."""
import io
import sys

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from backend.app import auth, cli, config, db, secretbox, settings_store, worker
from backend.app.main import app
from backend.app.services import outreach

PASSWORD = "correct horse battery"


@pytest.fixture(autouse=True)
def _reset_failures():
    auth._failures.clear()
    yield
    auth._failures.clear()


def make_user(c, email="ananya@drtracker.test", role="admin"):
    r = c.post("/api/users", json={"email": email, "name": "Ananya", "password": PASSWORD, "role": role})
    assert r.status_code == 200, r.text
    return r.json()


def sign_in(c, email="ananya@drtracker.test", password=PASSWORD):
    return c.post("/api/auth/login", json={"email": email, "password": password})


def test_password_hashing():
    h = auth.hash_password(PASSWORD)
    assert h.startswith("scrypt$") and PASSWORD not in h
    assert auth.verify_password(PASSWORD, h) and not auth.verify_password("wrong password!", h)
    assert auth.hash_password(PASSWORD) != h  # salted
    assert not auth.verify_password(PASSWORD, "garbage")


def test_open_on_localhost_only_while_no_users_exist(app_client):
    assert app_client.get("/api/contacts").status_code == 200
    with TestClient(app, client=("203.0.113.9", 4000)) as remote:
        assert remote.get("/api/contacts").status_code == 401
        assert remote.post("/api/users", json={"email": "x@y.z", "password": PASSWORD}).status_code == 401


def test_sign_in_required_once_a_user_exists(app_client):
    c = app_client
    first = make_user(c, role="member")
    assert first["role"] == "admin"  # the first account is always an admin
    assert c.get("/api/contacts").status_code == 401
    assert c.get("/healthz").json() == {"ok": True}  # health check stays public
    assert c.get("/api/auth/state").json()["users_exist"] is True
    assert sign_in(c, password="not the password").status_code == 401
    r = sign_in(c)
    assert r.status_code == 200 and "httponly" in r.headers["set-cookie"].lower()
    assert c.get("/api/contacts").status_code == 200
    assert c.get("/api/auth/state").json()["user"]["email"] == "ananya@drtracker.test"
    c.post("/api/auth/logout")
    assert c.get("/api/contacts").status_code == 401


def test_repeated_failures_lock_the_account_for_a_while(app_client):
    make_user(app_client)
    for _ in range(auth.MAX_FAILURES):
        assert sign_in(app_client, password="nope nope nope").status_code == 401
    r = sign_in(app_client)  # even the right password, for now
    assert r.status_code == 401 and "Too many" in r.json()["detail"]


def test_members_cannot_manage_users_and_the_last_admin_stays(app_client):
    c = app_client
    admin = make_user(c)
    sign_in(c)
    member = make_user(c, "rohit@drtracker.test", role="member")
    assert c.delete(f"/api/users/{admin['id']}").status_code == 400  # can't remove yourself
    c.post("/api/auth/logout")
    sign_in(c, "rohit@drtracker.test")
    assert c.post("/api/users", json={"email": "x@drtracker.test", "password": PASSWORD}).status_code == 403
    assert c.delete(f"/api/users/{admin['id']}").status_code == 403
    assert member["role"] == "member"


def test_cross_site_post_is_refused(app_client):
    r = app_client.post("/api/campaign/pause", headers={"Origin": "https://evil.example"})
    assert r.status_code == 403


def test_activity_log_records_who_approved_and_sent(configured):
    c = configured
    make_user(c)
    sign_in(c)
    cid = c.post("/api/contacts", json={"name": "Dr. Aditi Sharma", "email": "aditi@x.in"}).json()["id"]
    mid = c.post(f"/api/contacts/{cid}/draft").json()["id"]
    assert c.post(f"/api/messages/{mid}/send").status_code == 200  # Send now approves, then sends
    cid2 = c.post("/api/contacts", json={"name": "Dr. Rahul Nair", "email": "rahul@x.in"}).json()["id"]
    mid2 = c.post(f"/api/contacts/{cid2}/draft").json()["id"]
    c.post(f"/api/messages/{mid2}/approve")
    c.post("/api/campaign/start")
    worker.tick()  # the background campaign sends the second one
    with db.connect() as conn:
        m1, m2 = outreach.get_message(conn, mid), outreach.get_message(conn, mid2)
        events = db.rows(conn.execute("SELECT kind, actor, message FROM events WHERE kind IN ('approve', 'send') ORDER BY id"))
    assert (m1["approved_by"], m1["sent_by"]) == ("ananya@drtracker.test", "ananya@drtracker.test")
    assert (m2["approved_by"], m2["sent_by"]) == ("ananya@drtracker.test", "campaign")
    assert [e["actor"] for e in events] == ["ananya@drtracker.test"] * 3 + ["campaign"]
    assert "approved by ananya@drtracker.test" in events[-1]["message"]
    activity = c.get("/api/activity").json()
    assert any(e["actor"] == "campaign" for e in activity)


def test_editing_an_approved_draft_clears_the_approval(configured):
    c = configured
    c.put("/api/settings/campaign", json={"require_approval": True})
    cid = c.post("/api/contacts", json={"name": "Dr. Aditi Sharma", "email": "aditi@x.in"}).json()["id"]
    m = c.post(f"/api/contacts/{cid}/draft").json()
    assert c.post(f"/api/messages/{m['id']}/approve").json()["approved_by"]
    edited = c.put(f"/api/messages/{m['id']}", json={"body": m["body"] + "\n\nOne more line."}).json()
    assert edited["status"] == "draft" and edited["approved_by"] is None


# --- Secrets at rest ---------------------------------------------------------

def raw_secret(section="ai", field="anthropic_api_key"):
    with db.connect() as conn:
        return db.get_json(conn, f"section:{section}")[field]


def test_secrets_are_encrypted_with_a_key(app_client, monkeypatch):
    monkeypatch.setattr(config, "SECRET_KEY", Fernet.generate_key().decode())
    app_client.put("/api/settings/ai", json={"anthropic_api_key": "sk-ant-api-secretvalue1234"})
    app_client.put("/api/settings/email", json={"smtp_password": "app-password-xyz"})
    assert raw_secret().startswith(secretbox.PREFIX) and "secretvalue" not in raw_secret()
    assert raw_secret("email", "smtp_password").startswith(secretbox.PREFIX)
    assert settings_store.get_section("ai")["anthropic_api_key"] == "sk-ant-api-secretvalue1234"
    view = app_client.get("/api/settings").json()["ai"]
    assert view["anthropic_api_key"] == "" and view["anthropic_api_key_hint"].endswith("1234")


def test_plaintext_secrets_are_migrated_on_startup(app_client, monkeypatch):
    app_client.put("/api/settings/ai", json={"anthropic_api_key": "sk-ant-api-plaintext9999"})
    assert raw_secret() == "sk-ant-api-plaintext9999"  # no key yet: plaintext (local use)
    assert "unencrypted" in " ".join(app_client.get("/api/auth/state").json()["warnings"])
    monkeypatch.setattr(config, "SECRET_KEY", Fernet.generate_key().decode())
    auth.startup_checks()
    assert raw_secret().startswith(secretbox.PREFIX)
    assert settings_store.get_section("ai")["anthropic_api_key"] == "sk-ant-api-plaintext9999"


def test_refuses_to_start_with_encrypted_secrets_and_a_missing_or_wrong_key(app_client, monkeypatch):
    monkeypatch.setattr(config, "SECRET_KEY", Fernet.generate_key().decode())
    app_client.put("/api/settings/ai", json={"anthropic_api_key": "sk-ant-api-secretvalue1234"})
    monkeypatch.setattr(config, "SECRET_KEY", "")
    with pytest.raises(secretbox.SecretKeyError, match="isn't set"):
        auth.startup_checks()
    monkeypatch.setattr(config, "SECRET_KEY", Fernet.generate_key().decode())
    with pytest.raises(secretbox.SecretKeyError, match="doesn't match"):
        auth.startup_checks()
    monkeypatch.setattr(config, "SECRET_KEY", "not-a-fernet-key")
    with pytest.raises(secretbox.SecretKeyError, match="isn't a valid key"):
        auth.startup_checks()


# --- Exposed (non-localhost) startup rules -------------------------------------

def test_exposed_host_needs_a_user_and_a_key(app_client, monkeypatch):
    monkeypatch.setattr(config, "HOST", "0.0.0.0")
    with pytest.raises(RuntimeError, match="no users exist"):
        auth.startup_checks()
    make_user(app_client)
    with pytest.raises(RuntimeError, match="OUTREACH_SECRET_KEY"):
        auth.startup_checks()
    monkeypatch.setattr(config, "SECRET_KEY", Fernet.generate_key().decode())
    auth.startup_checks()  # now fine
    with TestClient(app):  # and the app actually starts
        pass


def test_app_does_not_start_when_exposed_without_users(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "x.db")
    monkeypatch.setattr(config, "HOST", "0.0.0.0")
    with pytest.raises(RuntimeError, match="Refusing to start"):
        with TestClient(app):
            pass


# --- CLI ---------------------------------------------------------------------

def test_cli_create_admin_and_gen_key(app_client, monkeypatch, capsys):
    monkeypatch.setattr(sys, "stdin", io.StringIO(PASSWORD + "\n"))
    assert cli.main(["create-admin", "--email", "Founder@DrTracker.test", "--name", "Founder", "--password-stdin"]) == 0
    assert sign_in(app_client, "founder@drtracker.test").status_code == 200
    monkeypatch.setattr(sys, "stdin", io.StringIO("short\n"))
    assert cli.main(["add-user", "--email", "b@drtracker.test", "--password-stdin"]) == 1  # too short
    capsys.readouterr()
    assert cli.main(["gen-key"]) == 0
    Fernet(capsys.readouterr().out.strip().encode())  # a valid key


def test_pending_uploads_are_not_kept_in_memory():
    from backend.app import api
    assert not hasattr(api, "_pending_imports")  # restart survival is tested in test_partner_sheet
