"""Minimal HS256 JWT (stdlib only) for magic-link + session tokens (SPEC13).

Mirrors playground's `jwtUtils` (sign/verify) without pulling in a dependency —
the app already prefers stdlib (urllib, sqlite3, smtplib) over third-party libs.
"""

import base64
import hashlib
import hmac
import json
import os
import time


def _secret() -> bytes:
    return os.getenv("JWT_SECRET", "dev-insecure-secret-change-me").encode()


def _b64u_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64u_decode(seg: str) -> bytes:
    pad = "=" * (-len(seg) % 4)
    return base64.urlsafe_b64decode(seg + pad)


def sign(payload: dict, ttl_seconds: int) -> str:
    """Return a signed HS256 JWT carrying `payload` plus iat/exp."""
    header = {"alg": "HS256", "typ": "JWT"}
    now = int(time.time())
    body = {**payload, "iat": now, "exp": now + ttl_seconds}
    signing_input = (
        _b64u_encode(json.dumps(header, separators=(",", ":")).encode())
        + "."
        + _b64u_encode(json.dumps(body, separators=(",", ":")).encode())
    )
    sig = hmac.new(_secret(), signing_input.encode(), hashlib.sha256).digest()
    return signing_input + "." + _b64u_encode(sig)


def verify(token: str) -> dict | None:
    """Return the payload if the signature is valid and not expired, else None."""
    try:
        header_seg, body_seg, sig_seg = token.split(".")
        signing_input = f"{header_seg}.{body_seg}"
        expected = _b64u_encode(
            hmac.new(_secret(), signing_input.encode(), hashlib.sha256).digest()
        )
        if not hmac.compare_digest(expected, sig_seg):
            return None
        payload = json.loads(_b64u_decode(body_seg))
        if int(payload.get("exp", 0)) < int(time.time()):
            return None
        return payload
    except Exception:
        return None
