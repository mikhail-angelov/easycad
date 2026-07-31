"""Feedback endpoint (in-app "leave feedback" button).

Stores to SQLite and best-effort emails the operator. The mail transport is
unconfigured in tests, so `send_mail` just logs — the endpoint must still succeed.
"""

import re

import pytest
from fastapi.testclient import TestClient

from app import db, main
from app.main import app


@pytest.fixture(autouse=True)
def _no_real_mail(monkeypatch):
    """Never hit real SMTP in tests (and keep them fast)."""
    monkeypatch.setattr(main, "send_mail", lambda *a, **k: None)


def _login(client: TestClient, monkeypatch, email: str) -> None:
    """Complete the magic-link flow so `client` is signed in as `email`."""
    captured: dict = {}

    def fake_send(to, subject, text):
        m = re.search(r"token=([\w.\-]+)", text)
        captured["token"] = m.group(1) if m else None

    monkeypatch.setattr(main, "send_mail", fake_send)
    assert client.post("/api/auth/login", json={"email": email}).json() == {"ok": True}
    resp = client.get(f"/api/auth/callback?token={captured['token']}", follow_redirects=False)
    assert resp.status_code == 302


def test_feedback_is_stored():
    client = TestClient(app)
    res = client.post("/api/feedback", json={"message": "Great tool!", "rating": 5})
    assert res.status_code == 200 and res.json() == {"ok": True}
    rows = db.list_feedback()
    assert len(rows) == 1
    assert rows[0]["message"] == "Great tool!"
    assert rows[0]["rating"] == 5
    assert rows[0]["email"] is None  # anonymous, no email supplied


def test_feedback_keeps_optional_email():
    client = TestClient(app)
    client.post("/api/feedback", json={"message": "call me", "email": "You@Example.com"})
    row = db.list_feedback()[0]
    assert row["email"] == "you@example.com"  # normalised


def test_feedback_discards_invalid_contact_email():
    client = TestClient(app)
    client.post("/api/feedback", json={"message": "hi", "email": "not-an-email"})
    assert db.list_feedback()[0]["email"] is None  # invalid value dropped


def test_no_notification_without_admin_email(monkeypatch):
    sent: list = []
    monkeypatch.setattr(main, "ADMIN_EMAIL", "")
    monkeypatch.setattr(main, "send_mail", lambda *a, **k: sent.append(a))
    client = TestClient(app)
    assert client.post("/api/feedback", json={"message": "no ops"}).json() == {"ok": True}
    assert db.count_feedback() == 1  # still stored
    assert sent == []               # but no mail sent to any fallback mailbox


def test_notification_goes_to_admin_email(monkeypatch):
    sent: list = []
    monkeypatch.setattr(main, "ADMIN_EMAIL", "admin@example.com")
    monkeypatch.setattr(main, "send_mail", lambda to, subj, body: sent.append(to))
    client = TestClient(app)
    client.post("/api/feedback", json={"message": "hey"})
    assert sent == ["admin@example.com"]


def test_feedback_rejects_empty_message():
    client = TestClient(app)
    # Pydantic min_length=1 → 422 for empty; whitespace-only → 400 from the handler.
    assert client.post("/api/feedback", json={"message": ""}).status_code == 422
    assert client.post("/api/feedback", json={"message": "   "}).status_code == 400
    assert db.count_feedback() == 0


def test_feedback_rejects_bad_rating():
    client = TestClient(app)
    assert client.post("/api/feedback", json={"message": "x", "rating": 9}).status_code == 422


def test_admin_stats_requires_admin_email(monkeypatch):
    monkeypatch.setattr(main, "ADMIN_EMAIL", "admin@example.com")
    client = TestClient(app)
    client.post("/api/feedback", json={"message": "hello", "rating": 4})

    # Anonymous / non-admin → hidden (404).
    assert client.get("/api/admin/stats").status_code == 404

    # Signed in as the admin email → visible, with feedback included.
    _login(client, monkeypatch, "admin@example.com")
    stats = client.get("/api/admin/stats").json()
    assert stats["feedback"]["count"] == 1
    assert stats["feedback"]["recent"][0]["message"] == "hello"


def test_admin_stats_hidden_for_other_users(monkeypatch):
    monkeypatch.setattr(main, "ADMIN_EMAIL", "admin@example.com")
    client = TestClient(app)
    _login(client, monkeypatch, "someone-else@example.com")
    assert client.get("/api/admin/stats").status_code == 404
