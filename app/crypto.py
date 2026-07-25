"""Stdlib-only authenticated encryption for the BYOK key at rest (SPEC14).

Encrypt-then-MAC using HMAC-SHA256 both as a CTR keystream and as the MAC — no
third-party crypto dependency, matching the app's stdlib-only stance (see
`jwt_utils`). Threat model: protect stored keys if the SQLite file leaks (backup,
volume); the running app legitimately holds the secret.

Keyed by `EASYCAD_SECRETS_KEY`, else derived from `JWT_SECRET`. Values are lazily
migrated — a legacy plaintext value (no `enc1:` prefix) reads through untouched,
and is re-encrypted on the next save. If the secret changes, old values fail the
MAC and decrypt to None (the key is treated as absent → the user re-enters it).

This is a deliberately small, auditable construction (encrypt-then-MAC over a
short secret), not a general crypto library. For anything broader, use a vetted
library instead of extending this.
"""

import base64
import hashlib
import hmac
import os
import secrets
import struct

_PREFIX = "enc1:"
_NONCE = 16
_MAC = 32
# Shared with jwt_utils — the value used when no real secret is configured.
INSECURE_DEFAULT = "dev-insecure-secret-change-me"


def _secret_str() -> str:
    return os.getenv("EASYCAD_SECRETS_KEY") or os.getenv("JWT_SECRET", INSECURE_DEFAULT)


def _master() -> bytes:
    return _secret_str().encode("utf-8")


def secret_is_secure() -> bool:
    """False when encryption would fall back to the public dev default — i.e.
    neither EASYCAD_SECRETS_KEY nor JWT_SECRET is set to a real value. In that
    case 'encrypted at rest' is a hollow promise (the key is publicly known)."""
    return _secret_str() != INSECURE_DEFAULT


def _subkeys() -> tuple[bytes, bytes]:
    """Two independent 32-byte subkeys (encryption, MAC) from the master secret."""
    m = _master()
    enc = hmac.new(m, b"easycad-enc-v1", hashlib.sha256).digest()
    mac = hmac.new(m, b"easycad-mac-v1", hashlib.sha256).digest()
    return enc, mac


def _keystream(enc_key: bytes, nonce: bytes, length: int) -> bytes:
    out = bytearray()
    counter = 0
    while len(out) < length:
        out.extend(hmac.new(enc_key, nonce + struct.pack(">I", counter), hashlib.sha256).digest())
        counter += 1
    return bytes(out[:length])


def is_encrypted(value: str) -> bool:
    return value.startswith(_PREFIX)


def encrypt(plaintext: str) -> str:
    enc_key, mac_key = _subkeys()
    nonce = secrets.token_bytes(_NONCE)
    data = plaintext.encode("utf-8")
    ct = bytes(a ^ b for a, b in zip(data, _keystream(enc_key, nonce, len(data))))
    mac = hmac.new(mac_key, nonce + ct, hashlib.sha256).digest()
    return _PREFIX + base64.urlsafe_b64encode(nonce + ct + mac).decode("ascii")


def decrypt(token: str) -> str | None:
    """Plaintext, or None if corrupt / secret changed. A non-`enc1:` value is
    legacy plaintext and is returned unchanged (lazy migration)."""
    if not token.startswith(_PREFIX):
        return token
    try:
        raw = base64.urlsafe_b64decode(token[len(_PREFIX):])
    except Exception:  # noqa: BLE001 — malformed token
        return None
    if len(raw) < _NONCE + _MAC:
        return None
    nonce, ct, mac = raw[:_NONCE], raw[_NONCE:-_MAC], raw[-_MAC:]
    enc_key, mac_key = _subkeys()
    if not hmac.compare_digest(mac, hmac.new(mac_key, nonce + ct, hashlib.sha256).digest()):
        return None
    return bytes(a ^ b for a, b in zip(ct, _keystream(enc_key, nonce, len(ct)))).decode("utf-8", "replace")
