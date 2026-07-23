"""SPEC13: magic-link auth, per-user vs anonymous settings, account deletion."""

import re

from fastapi.testclient import TestClient

import app.main as m
from app.main import app


def _capture_link(monkeypatch) -> dict:
    """Monkeypatch the mailer to capture the magic link it would send."""
    captured: dict = {}

    def fake_send(to, subject, text):
        captured["to"] = to
        match = re.search(r"token=([\w.\-]+)", text)
        captured["token"] = match.group(1) if match else None

    monkeypatch.setattr(m, "send_mail", fake_send)
    return captured


def _login(client: TestClient, monkeypatch, email: str) -> None:
    captured = _capture_link(monkeypatch)
    assert client.post("/api/auth/login", json={"email": email}).json() == {"ok": True}
    token = captured["token"]
    assert token
    resp = client.get(f"/api/auth/callback?token={token}", follow_redirects=False)
    assert resp.status_code == 302
    assert "auth_token" in resp.cookies


def test_login_and_callback_authenticates(monkeypatch):
    client = TestClient(app)
    _login(client, monkeypatch, "user@example.com")
    me = client.get("/api/auth/me").json()
    assert me["authenticated"] is True
    assert me["email"] == "user@example.com"


def test_bad_email_rejected():
    client = TestClient(app)
    assert client.post("/api/auth/login", json={"email": "not-an-email"}).status_code == 400


def test_invalid_callback_token_rejected():
    client = TestClient(app)
    assert client.get("/api/auth/callback?token=garbage", follow_redirects=False).status_code == 400


def test_anonymous_settings_are_per_session():
    a = TestClient(app)
    b = TestClient(app)
    a.get("/api/session")
    a.put("/api/settings", json={"provider": "deepseek", "key": "sk-anon"})
    assert a.get("/api/settings").json()["has_key"] is True
    # A different anonymous session does not see it.
    assert b.get("/api/settings").json()["has_key"] is False


def test_anon_settings_never_return_the_key():
    client = TestClient(app)
    client.put("/api/settings", json={"key": "sk-secret"})
    body = client.get("/api/settings").json()
    assert body["has_key"] is True
    assert "key" not in body


def test_user_settings_persist_in_db(monkeypatch):
    client = TestClient(app)
    _login(client, monkeypatch, "persist@example.com")
    client.put("/api/settings", json={"provider": "openrouter", "key": "sk-user", "model": "x"})
    assert client.get("/api/settings").json() == {"provider": "openrouter", "model": "x", "has_key": True}

    from app import db
    user = db.get_or_create_user("persist@example.com")
    assert user["settings"]["key"] == "sk-user"  # stored (plaintext, per decision)


def test_logout_clears_auth(monkeypatch):
    client = TestClient(app)
    _login(client, monkeypatch, "out@example.com")
    assert client.get("/api/auth/me").json()["authenticated"] is True
    client.post("/api/auth/logout")
    assert client.get("/api/auth/me").json()["authenticated"] is False


def test_delete_account(monkeypatch):
    client = TestClient(app)
    _login(client, monkeypatch, "gone@example.com")
    client.put("/api/settings", json={"key": "sk-doomed"})
    assert client.delete("/api/auth/me").json() == {"ok": True}
    assert client.get("/api/auth/me").json()["authenticated"] is False

    from app import db
    # The account (and its stored settings) is gone; re-creating is a fresh row.
    user = db.get_or_create_user("gone@example.com")
    assert user["settings"] == {}
