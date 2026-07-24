"""SPEC14: free trial, provider/model pickers, key validation, coded errors.

The LLM is stubbed (`generate_code` / `triage`) so these exercise the trial
gating, counters, and validation wiring without a live provider. CadQuery still
runs on the generated code (requires the .venv-poc interpreter).
"""

import re

from fastapi.testclient import TestClient

import app.main as m
from app.main import app
from app import db

BOX = "import cadquery as cq\nresult = cq.Workplane('XY').box(10, 10, 10)\n"


def _stub_llm(monkeypatch, code: str = BOX):
    """Replace the LLM generator with a deterministic stub; count invocations."""
    calls = {"n": 0}

    def fake_generate(base_code, prompt, provider, model=None, temperature=0.2, api_key=None):
        calls["n"] += 1
        return code

    monkeypatch.setattr(m, "generate_code", fake_generate)
    return calls


def _chat(client: TestClient, prompt="add a hole", ip="9.9.9.9"):
    return client.post(
        "/api/chat",
        json={"prompt": prompt, "auto_refine": False, "current_code": BOX},
        headers={"x-real-ip": ip},
    )


def _login(client: TestClient, monkeypatch, email: str) -> None:
    captured: dict = {}

    def fake_send(to, subject, text):
        captured["token"] = re.search(r"token=([\w.\-]+)", text).group(1)

    monkeypatch.setattr(m, "send_mail", fake_send)
    client.post("/api/auth/login", json={"email": email})
    client.get(f"/api/auth/callback?token={captured['token']}", follow_redirects=False)


# ── Providers / model metadata in payloads ────────────────────────────────────


def test_session_exposes_provider_models():
    client = TestClient(app)
    data = client.get("/api/session").json()
    assert data["providers"]["deepseek"]["models"] == ["deepseek-chat", "deepseek-reasoner"]
    assert data["providers"]["openrouter"]["default_model"] == "deepseek/deepseek-chat"
    # openai is kept in code but hidden from the UI dropdown.
    assert "openai" not in data["providers"]


def test_anon_trial_fields_present():
    client = TestClient(app)
    data = client.get("/api/session", headers={"x-real-ip": "5.5.5.5"}).json()
    assert data["settings"]["trial_tier"] == "anon"
    assert data["settings"]["trial_remaining"] == 1


# ── Trial gating: anonymous ────────────────────────────────────────────────────


def test_anon_gets_one_free_then_exhausted(monkeypatch):
    calls = _stub_llm(monkeypatch)
    client = TestClient(app)

    r1 = _chat(client, ip="1.1.1.1")
    assert r1.status_code == 200
    assert r1.json()["step"]["success"] is True
    assert calls["n"] == 1

    r2 = _chat(client, ip="1.1.1.1")
    assert r2.status_code == 402
    assert r2.json()["detail"]["code"] == "trial_exhausted_anon"
    # Exhausted → no second LLM call spent.
    assert calls["n"] == 1


def test_anon_trial_keyed_by_ip(monkeypatch):
    _stub_llm(monkeypatch)
    client = TestClient(app)
    assert _chat(client, ip="2.2.2.2").status_code == 200
    # Same cookie/session, different IP → fresh grant (IP is the durable key).
    assert _chat(client, ip="3.3.3.3").status_code == 200


def test_failed_provider_call_does_not_burn_trial(monkeypatch):
    def boom(*a, **k):
        raise m.LLMError("provider down")

    monkeypatch.setattr(m, "generate_code", boom)
    client = TestClient(app)
    r = _chat(client, ip="4.4.4.4")
    assert r.status_code == 502
    assert r.json()["detail"]["code"] == "provider_error"
    # Quota untouched: still 1 remaining.
    assert db.get_anon_trial("4.4.4.4") == 0


# ── Trial gating: registered user ──────────────────────────────────────────────


def test_registered_user_gets_ten(monkeypatch):
    _stub_llm(monkeypatch)
    client = TestClient(app)
    _login(client, monkeypatch, "trial@example.com")
    for _ in range(10):
        assert _chat(client).status_code == 200
    r = _chat(client)
    assert r.status_code == 402
    assert r.json()["detail"]["code"] == "trial_exhausted_user"


def test_saved_key_bypasses_trial(monkeypatch):
    _stub_llm(monkeypatch)
    client = TestClient(app)
    client.get("/api/session", headers={"x-real-ip": "8.8.8.8"})
    client.put("/api/settings", json={"provider": "deepseek", "key": "sk-mykey"})
    # BYOK → unlimited, no trial counting.
    for _ in range(3):
        assert _chat(client, ip="8.8.8.8").status_code == 200
    assert db.get_anon_trial("8.8.8.8") == 0


# ── Key validation ─────────────────────────────────────────────────────────────


def test_validate_key_prefix_mismatch_no_live_call(monkeypatch):
    called = {"live": False}

    def fake_live(provider, key):
        called["live"] = True
        return True, None

    monkeypatch.setattr(m, "validate_key_live", fake_live)
    client = TestClient(app)
    r = client.post("/api/settings/validate-key", json={"provider": "openrouter", "key": "sk-nope"})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    assert "openrouter" in body["reason"]
    assert called["live"] is False  # short-circuited before the live call


def test_validate_key_live_ok(monkeypatch):
    monkeypatch.setattr(m, "validate_key_live", lambda p, k: (True, None))
    client = TestClient(app)
    r = client.post("/api/settings/validate-key", json={"provider": "openrouter", "key": "sk-or-good"})
    assert r.json() == {"ok": True, "reason": None}


def test_validate_key_live_rejected(monkeypatch):
    monkeypatch.setattr(m, "validate_key_live", lambda p, k: (False, "Key rejected by deepseek."))
    client = TestClient(app)
    r = client.post("/api/settings/validate-key", json={"provider": "deepseek", "key": "sk-bad"})
    body = r.json()
    assert body["ok"] is False
    assert "rejected" in body["reason"]


# ── Settings allow-list enforcement ────────────────────────────────────────────


def test_put_settings_rejects_hidden_provider():
    client = TestClient(app)
    client.get("/api/session")
    r = client.put("/api/settings", json={"provider": "openai", "key": "sk-x"})
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "invalid_provider"


def test_put_settings_rejects_off_list_model():
    client = TestClient(app)
    client.get("/api/session")
    r = client.put("/api/settings", json={"provider": "deepseek", "model": "gpt-4-turbo", "key": "sk-x"})
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "invalid_model"


def test_put_settings_accepts_allow_listed_model():
    client = TestClient(app)
    client.get("/api/session")
    r = client.put("/api/settings", json={"provider": "deepseek", "model": "deepseek-reasoner", "key": "sk-x"})
    assert r.status_code == 200
    assert r.json()["model"] == "deepseek-reasoner"


# ── Exhaustion copy carries the configured limit ───────────────────────────────


def test_exhaustion_message_mentions_the_limit(monkeypatch):
    _stub_llm(monkeypatch)
    client = TestClient(app)
    _chat(client, ip="6.6.6.6")  # consume the single anon grant
    r = _chat(client, ip="6.6.6.6")
    assert str(m.TRIAL_USER) in r.json()["detail"]["message"]


# ── /api/refine is rate-limited (no unbounded operator-key spend) ──────────────


def test_refine_is_rate_limited(monkeypatch):
    monkeypatch.setattr(m, "triage", lambda *a, **k: __import__("app.refiner", fromlist=["TriageResult"]).TriageResult("ready"))
    monkeypatch.setenv("EASYCAD_GEN_RATE_LIMIT", "3")
    client = TestClient(app)
    codes = [
        client.post("/api/refine", json={"prompt": "x"}, headers={"x-real-ip": "7.7.7.1"}).status_code
        for _ in range(5)
    ]
    assert 429 in codes  # the gate eventually trips


# ── Variations echoes trial status (client need not re-count) ──────────────────


def test_variations_echoes_trial_remaining(monkeypatch):
    _stub_llm(monkeypatch)
    monkeypatch.setattr(
        m, "triage", lambda *a, **k: __import__("app.refiner", fromlist=["TriageResult"]).TriageResult("ready")
    )
    client = TestClient(app)
    r = client.post(
        "/api/variations",
        json={"prompt": "add a hole", "current_code": BOX, "count": 1},
        headers={"x-real-ip": "12.12.12.12"},
    )
    body = r.json()
    assert body["trial_tier"] == "anon"
    assert body["trial_remaining"] == 0  # the one anon grant was just spent


# ── DB helpers ─────────────────────────────────────────────────────────────────


def test_db_trial_counters_are_atomic_and_isolated():
    assert db.get_anon_trial("10.0.0.1") == 0
    assert db.incr_anon_trial("10.0.0.1") == 1
    assert db.incr_anon_trial("10.0.0.1") == 2
    assert db.get_anon_trial("10.0.0.2") == 0  # distinct IP unaffected

    u = db.get_or_create_user("counter@example.com")
    assert db.get_user_trial(u["id"]) == 0
    assert db.incr_user_trial(u["id"]) == 1


def test_migration_preserves_existing_registrations(tmp_path, monkeypatch):
    """A pre-SPEC14 DB (users without `trial_used`, no `anon_trial`) with real
    accounts must migrate in place — existing registrations kept, new column
    defaulted to 0, new table usable. This is the path production actually takes."""
    import sqlite3

    from app import db

    old = tmp_path / "pre-spec14.db"
    raw = sqlite3.connect(old)
    raw.execute(
        """
        CREATE TABLE users (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            email      TEXT NOT NULL UNIQUE,
            settings   TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    raw.execute(
        "INSERT INTO users (email, settings) VALUES (?, ?)",
        ("existing@example.com", '{"provider": "deepseek", "key": "sk-existing"}'),
    )
    raw.commit()
    raw.close()

    # Point the app at the old file and force a reconnect (runs the migration).
    monkeypatch.setenv("EASYCAD_DB_PATH", str(old))
    db._conn = None
    db._conn_path = None
    try:
        user = db.get_or_create_user("existing@example.com")
        assert user["settings"]["key"] == "sk-existing"  # registration preserved
        assert user["id"] == 1  # same row, not a new account
        assert db.get_user_trial(user["id"]) == 0  # migrated column, defaulted
        assert db.incr_user_trial(user["id"]) == 1  # counter works post-migration
        assert db.incr_anon_trial("1.2.3.4") == 1  # new table created & usable
    finally:
        db._conn = None  # let the next test's fixture reopen the isolated path
        db._conn_path = None


def test_sweep_anon_trial_prunes_old_rows():
    db.incr_anon_trial("11.0.0.1")
    assert db.get_anon_trial("11.0.0.1") == 1
    # Everything older than 0s is stale → pruned.
    assert db.sweep_anon_trial(0) >= 1
    assert db.get_anon_trial("11.0.0.1") == 0
