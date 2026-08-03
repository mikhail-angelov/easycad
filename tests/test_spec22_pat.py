"""SPEC22: Personal Access Tokens — creation cap, IDOR-safe revoke, PAT→cookie
exchange with a short non-refreshing cookie, deleted-owner anonymisation."""

import re

import app.main as m
from app.main import app
from fastapi.testclient import TestClient


def _capture_link(monkeypatch) -> dict:
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
    resp = client.get(f"/api/auth/callback?token={captured['token']}", follow_redirects=False)
    assert resp.status_code == 302


def _mint(client: TestClient, name: str = "agent") -> str:
    r = client.post("/api/tokens", json={"name": name})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["token"].startswith("pat_")
    return body["token"]


def test_create_requires_auth():
    client = TestClient(app)
    assert client.post("/api/tokens", json={"name": "x"}).status_code == 401


def test_create_returns_secret_once_and_list_hides_it(monkeypatch):
    client = TestClient(app)
    _login(client, monkeypatch, "pat@example.com")
    raw = _mint(client)
    listed = client.get("/api/tokens").json()
    assert len(listed) == 1
    assert "token" not in listed[0] and "hash" not in listed[0]
    assert listed[0]["revoked_at"] is None
    # The raw secret is not retrievable again anywhere.
    assert not any(raw in str(v) for v in listed[0].values())


def test_exchange_issues_short_pat_cookie(monkeypatch):
    client = TestClient(app)
    _login(client, monkeypatch, "pat2@example.com")
    raw = _mint(client)
    client.post("/api/auth/logout")

    agent = TestClient(app)
    r = agent.post("/api/auth/token", json={"token": raw})
    assert r.status_code == 200
    assert r.json()["email"] == "pat2@example.com"
    # Cookie carries a short max-age (≤12h), not the 1-year magic-link window.
    setc = [v for k, v in r.headers.multi_items() if k.lower() == "set-cookie" and "auth_token" in v]
    assert setc, "auth cookie not set"
    max_age = int(re.search(r"Max-Age=(\d+)", setc[0]).group(1))
    assert max_age == m.PAT_COOKIE_MAX_AGE <= 12 * 3600
    # The agent is now signed in as the owner.
    assert agent.get("/api/auth/me").json()["email"] == "pat2@example.com"


def test_pat_cookie_is_never_refreshed(monkeypatch):
    # Even with refresh-on-every-request, a PAT cookie must not roll to 1 year.
    monkeypatch.setattr(m, "AUTH_REFRESH_AFTER", -1)
    client = TestClient(app)
    _login(client, monkeypatch, "pat3@example.com")
    raw = _mint(client)

    agent = TestClient(app)
    agent.post("/api/auth/token", json={"token": raw})
    r = agent.get("/api/session")
    refreshed = [v for k, v in r.headers.multi_items() if k.lower() == "set-cookie" and "auth_token" in v]
    assert not refreshed, "PAT cookie must never be refreshed"


def test_revoked_token_cannot_exchange(monkeypatch):
    client = TestClient(app)
    _login(client, monkeypatch, "pat4@example.com")
    tok = client.post("/api/tokens", json={"name": "t"}).json()
    raw, tid = tok["token"], tok["id"]
    assert client.delete(f"/api/tokens/{tid}").json() == {"ok": True}

    agent = TestClient(app)
    assert agent.post("/api/auth/token", json={"token": raw}).status_code == 401


def test_revoke_is_owner_scoped_no_idor(monkeypatch):
    a = TestClient(app)
    _login(a, monkeypatch, "owner-a@example.com")
    tok_a = a.post("/api/tokens", json={"name": "a"}).json()

    b = TestClient(app)
    _login(b, monkeypatch, "owner-b@example.com")
    # B tries to revoke A's token by id → safe no-op, A's token still works.
    assert b.delete(f"/api/tokens/{tok_a['id']}").json() == {"ok": True}

    agent = TestClient(app)
    assert agent.post("/api/auth/token", json={"token": tok_a["token"]}).status_code == 200


def test_create_cap_returns_429(monkeypatch):
    client = TestClient(app)
    _login(client, monkeypatch, "cap@example.com")
    for _ in range(m.db.MAX_ACTIVE_TOKENS):
        assert client.post("/api/tokens", json={"name": "t"}).status_code == 200
    assert client.post("/api/tokens", json={"name": "over"}).status_code == 429


def test_expired_token_rejected(monkeypatch):
    client = TestClient(app)
    _login(client, monkeypatch, "exp@example.com")
    # TTL 0 → expires immediately (expires_at not > now on lookup).
    monkeypatch.setattr(m.db, "TOKEN_TTL_SECONDS", 0)
    raw = _mint(client)
    agent = TestClient(app)
    assert agent.post("/api/auth/token", json={"token": raw}).status_code == 401


def test_malformed_token_rejected():
    agent = TestClient(app)
    # No prefix, right prefix but wrong length, and bad alphabet — all rejected by
    # the exact-shape pre-filter (SPEC22 §2.2) before any DB hit.
    for bad in ["not-a-pat", "pat_x", "pat_" + "a" * 42, "pat_" + "a" * 44, "pat_" + "!" * 43]:
        assert agent.post("/api/auth/token", json={"token": bad}).status_code == 401, bad


def test_well_formed_but_unknown_token_rejected():
    # Correct shape (prefix + 43 urlsafe chars) but never issued → 401 at lookup.
    agent = TestClient(app)
    assert agent.post("/api/auth/token", json={"token": "pat_" + "A" * 43}).status_code == 401


def test_padded_token_rejected(monkeypatch):
    # A real, valid PAT with surrounding whitespace is NOT the exact §2.2 form and
    # must be rejected before the DB lookup (no lenient strip on the exchange).
    client = TestClient(app)
    _login(client, monkeypatch, "pad@example.com")
    raw = _mint(client)
    agent = TestClient(app)
    assert agent.post("/api/auth/token", json={"token": f"  {raw}  "}).status_code == 401
    assert agent.post("/api/auth/token", json={"token": f"{raw}\n"}).status_code == 401
    # The unpadded token still works.
    assert agent.post("/api/auth/token", json={"token": raw}).status_code == 200


def test_deleted_account_cascade_and_anonymises_cookie(monkeypatch):
    client = TestClient(app)
    _login(client, monkeypatch, "del@example.com")
    raw = _mint(client)
    uid = client.get("/api/auth/me")  # ensure signed in
    assert uid.json()["authenticated"] is True

    # A second context holding a valid PAT cookie.
    agent = TestClient(app)
    agent.post("/api/auth/token", json={"token": raw})
    assert agent.get("/api/auth/me").json()["authenticated"] is True

    # Delete the account in the first context.
    assert client.delete("/api/auth/me").json() == {"ok": True}

    # The agent's still-valid cookie now resolves anonymous (deleted owner).
    assert agent.get("/api/auth/me").json()["authenticated"] is False
    # Tokens cascade-deleted → the PAT no longer exchanges.
    fresh = TestClient(app)
    assert fresh.post("/api/auth/token", json={"token": raw}).status_code == 401


def test_deleted_account_cookie_not_refreshed(monkeypatch):
    monkeypatch.setattr(m, "AUTH_REFRESH_AFTER", -1)
    client = TestClient(app)
    _login(client, monkeypatch, "del2@example.com")
    # Capture the magic-link cookie value, then delete the account.
    me = client.get("/api/auth/me")
    assert me.json()["authenticated"] is True
    # Re-use the same client cookies but delete via a parallel authed context.
    client.delete("/api/auth/me")
    r = client.get("/api/session")
    # No rolling refresh for a deleted user; the stale cookie is cleared.
    setc = [v for k, v in r.headers.multi_items() if k.lower() == "set-cookie" and "auth_token" in v]
    # Either no refresh, or an explicit delete_cookie (Max-Age=0) — never a fresh year.
    for c in setc:
        mo = re.search(r"Max-Age=(\d+)", c)
        assert mo is None or int(mo.group(1)) == 0
