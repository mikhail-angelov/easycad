"""Feedback endpoint (in-app "leave feedback" button).

Stores to SQLite and best-effort emails the operator. The mail transport is
unconfigured in tests, so `send_mail` just logs — the endpoint must still succeed.
"""

import re

import pytest
from fastapi.testclient import TestClient

from app import db, main
from app.main import app
from app.cadquery_exec import ExecResult


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


# ── W2: operator dashboard ───────────────────────────────────────────────────

def test_admin_page_is_gated(monkeypatch):
    monkeypatch.setattr(main, "ADMIN_EMAIL", "admin@example.com")
    client = TestClient(app)
    assert client.get("/admin").status_code == 404  # anonymous → hidden
    _login(client, monkeypatch, "admin@example.com")
    r = client.get("/admin")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "admin" in r.text and "/api/admin/stats" in r.text


def test_admin_stats_includes_worker_block(monkeypatch):
    # No EASYCAD_WORKER_URL in tests → worker proxy degrades gracefully.
    monkeypatch.setattr(main, "ADMIN_EMAIL", "admin@example.com")
    monkeypatch.delenv("EASYCAD_WORKER_URL", raising=False)
    client = TestClient(app)
    _login(client, monkeypatch, "admin@example.com")
    worker = client.get("/api/admin/stats").json()["worker"]
    assert worker["reachable"] is False and "reason" in worker


def test_admin_stats_includes_signup_count(monkeypatch):
    # SPEC19 W2 requires the dashboard to show the signup count. Signing in creates
    # the admin account, so the payload's signup count matches the users table.
    monkeypatch.setattr(main, "ADMIN_EMAIL", "admin@example.com")
    client = TestClient(app)
    _login(client, monkeypatch, "admin@example.com")
    stats = client.get("/api/admin/stats").json()
    assert stats["signups"] == db.count_users()
    assert stats["signups"] >= 1  # at least the signed-in admin


def test_admin_stats_counts_failed_generation(monkeypatch):
    # A failed generation turn must be reflected in the dashboard's failure metric.
    # The template reads `gen_exec_fail` (the counterpart of gen_ok); the old
    # `gen_fail` key was never emitted.
    monkeypatch.setattr(main, "ADMIN_EMAIL", "admin@example.com")
    async def generate_none(*_args, **_kwargs):
        return "result = None\n"

    monkeypatch.setattr(main, "generate_code", generate_none)
    monkeypatch.setattr(main, "execute", lambda code: ExecResult(False, error="NameError"))
    monkeypatch.setattr(main, "MAX_REPAIR", 0)
    main.metrics._reset_for_tests()
    client = TestClient(app)
    box = "import cadquery as cq\nresult = cq.Workplane('XY').box(1, 1, 1)\n"
    r = client.post(
        "/api/chat",
        json={"prompt": "x", "auto_refine": False, "current_code": box},
        headers={"x-real-ip": "55.0.0.9"},
    )
    assert r.status_code == 200 and r.json()["step"]["success"] is False
    _login(client, monkeypatch, "admin@example.com")
    counters = client.get("/api/admin/stats").json()["counters"]
    assert counters.get("gen_exec_fail", 0) >= 1
    assert "gen_fail" not in counters  # the dead metric the template used to read
