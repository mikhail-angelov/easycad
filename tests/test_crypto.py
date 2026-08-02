"""BYOK key encryption at rest (SPEC14)."""

import json

import pytest

import app.main as m
from app import crypto, db


def test_encrypt_roundtrip():
    token = crypto.encrypt("sk-secret-123")
    assert crypto.is_encrypted(token)
    assert token != "sk-secret-123"
    assert crypto.decrypt(token) == "sk-secret-123"


def test_legacy_plaintext_reads_through():
    # A value without the enc1: prefix is treated as legacy plaintext.
    assert crypto.decrypt("sk-plain") == "sk-plain"


def test_tampered_or_wrong_secret_returns_none(monkeypatch):
    token = crypto.encrypt("sk-secret-123")
    # Flip a byte in the base64 body → MAC fails.
    bad = token[:-4] + ("AAAA" if token[-4:] != "AAAA" else "BBBB")
    assert crypto.decrypt(bad) is None
    # A different secret can't decrypt it either.
    monkeypatch.setenv("EASYCAD_SECRETS_KEY", "some-other-key")
    assert crypto.decrypt(token) is None


def test_db_stores_key_encrypted_but_returns_plaintext():
    user = db.get_or_create_user("enc@example.com")
    db.update_settings(user["id"], {"provider": "deepseek", "model": None, "key": "sk-mine"})

    # The API-facing read returns the decrypted key.
    assert db.get_user(user["id"])["settings"]["key"] == "sk-mine"

    # ...but the raw column holds ciphertext, not the plaintext key.
    with db._lock:
        row = db._get().execute("SELECT settings FROM users WHERE id = ?", (user["id"],)).fetchone()
    stored = json.loads(row["settings"])
    assert stored["key"] != "sk-mine"
    assert crypto.is_encrypted(stored["key"])


def test_lost_secret_drops_key(monkeypatch):
    user = db.get_or_create_user("lost@example.com")
    db.update_settings(user["id"], {"key": "sk-mine"})
    # Secret rotates → the stored key no longer decrypts → treated as absent.
    monkeypatch.setenv("EASYCAD_SECRETS_KEY", "rotated-secret")
    settings = db.get_user(user["id"])["settings"]
    assert "key" not in settings


def test_dedicated_secret_takes_priority(monkeypatch):
    # EASYCAD_SECRETS_KEY overrides JWT_SECRET for encryption.
    monkeypatch.setenv("EASYCAD_SECRETS_KEY", "dedicated-A")
    token = crypto.encrypt("sk-x")
    assert crypto.decrypt(token) == "sk-x"
    monkeypatch.setenv("EASYCAD_SECRETS_KEY", "dedicated-B")
    assert crypto.decrypt(token) is None  # different dedicated key → can't decrypt


def test_legacy_plaintext_reencrypts_on_next_save():
    user = db.get_or_create_user("legacy@example.com")
    # Simulate a pre-encryption row: a raw plaintext key on disk.
    with db._lock:
        db._get().execute(
            "UPDATE users SET settings = ? WHERE id = ?",
            (json.dumps({"provider": "deepseek", "key": "sk-legacy"}), user["id"]),
        )
        db._get().commit()
    assert db.get_user(user["id"])["settings"]["key"] == "sk-legacy"  # reads through

    db.update_settings(user["id"], {"provider": "deepseek", "key": "sk-legacy"})
    with db._lock:
        row = db._get().execute("SELECT settings FROM users WHERE id = ?", (user["id"],)).fetchone()
    assert crypto.is_encrypted(json.loads(row["settings"])["key"])  # now ciphertext


def test_secret_is_secure_reflects_env(monkeypatch):
    monkeypatch.delenv("EASYCAD_SECRETS_KEY", raising=False)
    monkeypatch.delenv("JWT_SECRET", raising=False)
    assert crypto.secret_is_secure() is False  # falls back to the public default
    monkeypatch.setenv("JWT_SECRET", "a-real-secret")
    assert crypto.secret_is_secure() is True


def test_production_boot_requires_strong_secret(monkeypatch):
    monkeypatch.delenv("EASYCAD_SECRETS_KEY", raising=False)
    monkeypatch.delenv("JWT_SECRET", raising=False)
    monkeypatch.setattr(m, "SECURE_COOKIES", True)  # production
    monkeypatch.setattr(m, "ADMIN_EMAIL", "ops@example.com")  # required in prod (SPEC21 W2)
    with pytest.raises(RuntimeError):
        m._check_required_env()
    monkeypatch.setenv("JWT_SECRET", "a-strong-secret")
    m._check_required_env()  # now boots


def test_production_requires_jwt_secret_independent_of_encryption_key(monkeypatch):
    # A dedicated encryption key is set, but JWT_SECRET is missing → auth tokens
    # would be signed with the public default → must still refuse to start.
    monkeypatch.delenv("JWT_SECRET", raising=False)
    monkeypatch.setenv("EASYCAD_SECRETS_KEY", "a-strong-encryption-key")
    monkeypatch.setattr(m, "SECURE_COOKIES", True)
    from app import jwt_utils

    assert jwt_utils.secret_is_secure() is False
    with pytest.raises(RuntimeError):
        m._check_required_env()


def test_remove_key_clears_it(monkeypatch):
    import re

    from fastapi.testclient import TestClient
    from app.main import app

    client = TestClient(app)
    captured: dict = {}
    monkeypatch.setattr(m, "send_mail", lambda to, s, tx: captured.update(
        token=re.search(r"token=([\w.\-]+)", tx).group(1)))
    client.post("/api/auth/login", json={"email": "rm@example.com"})
    client.get(f"/api/auth/callback?token={captured['token']}", follow_redirects=False)

    client.put("/api/settings", json={"provider": "deepseek", "key": "sk-remove-me"})
    assert client.get("/api/settings").json()["has_key"] is True
    # Empty key clears it.
    client.put("/api/settings", json={"key": ""})
    assert client.get("/api/settings").json()["has_key"] is False
