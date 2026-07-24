"""SPEC11 CadQuery Chat API, made multi-tenant per SPEC13.

Text-only incremental 3D-model builder. Each visitor gets an in-memory session
(keyed by the `easycad_session` cookie) with a sliding idle TTL — no working
state touches disk. Passwordless magic-link auth (SPEC13) lets users store their
own LLM key + settings; anonymous users keep settings in their session only.
Users persist CAD work themselves via project export/import.
"""

import asyncio
import base64
import json
import os
import secrets
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import db, jwt_utils
from .cadquery_exec import execute
from .llm import (
    DEFAULT_PROVIDER,
    INITIAL_CODE,
    LLMError,
    PROVIDERS,
    TRIAL_MODEL,
    TRIAL_PROVIDER,
    generate_code,
    key_prefix_ok,
    ui_providers,
    validate_key_live,
)
from .mail import send_mail
from .ratelimit import RateLimiter
from .refiner import triage
from .session_registry import Session, build_registry

ROOT = Path(__file__).resolve().parent.parent
STATIC_DIR = ROOT / "static"

SESSION_COOKIE = "easycad_session"
AUTH_COOKIE = "auth_token"
SECURE_COOKIES = os.getenv("EASYCAD_SECURE_COOKIES") == "1"
# Long-lived session, rolled forward on activity → users stay logged in ("once
# logged in, always logged in"). Logout only happens after a full year idle.
COOKIE_MAX_AGE = 365 * 24 * 3600  # 1 year
# Re-issue the auth cookie at most once per this interval of activity (keeps the
# rolling window fresh without setting a cookie on every single request).
AUTH_REFRESH_AFTER = 24 * 3600  # 1 day
MAGIC_TTL = 15 * 60  # 15 minutes

# Free-trial grants on the operator's DeepSeek key (SPEC14). Lifetime, not
# periodic. Set both to 0 to disable the trial entirely (supersedes the old
TRIAL_ANON = int(os.getenv("EASYCAD_TRIAL_ANON", "1"))
TRIAL_USER = int(os.getenv("EASYCAD_TRIAL_USER", "10"))
# Prune anon_trial rows older than this (reuses no external scheduler — swept in
# the same background loop as sessions).
ANON_TRIAL_TTL = float(os.getenv("EASYCAD_ANON_TRIAL_TTL", str(30 * 24 * 3600)))  # 30 days

# Input bounds (review C1) — reject oversized payloads before parsing/retention.
MAX_BODY_BYTES = int(os.getenv("EASYCAD_MAX_BODY_BYTES", str(2_000_000)))  # 2 MB
MAX_PROMPT = 20_000
MAX_CODE = 200_000
MAX_NAME = 500
MAX_EMAIL = 320

load_dotenv(ROOT / ".env")

registry = build_registry()
limiter = RateLimiter()


@asynccontextmanager
async def _lifespan(app: FastAPI):
    async def sweep_loop() -> None:
        ticks = 0
        while True:
            await asyncio.sleep(60)
            try:
                registry.sweep()
            except Exception:
                pass
            # Prune stale anon-trial rows roughly hourly (60 × 60s ticks).
            ticks += 1
            if ticks % 60 == 0:
                try:
                    db.sweep_anon_trial(ANON_TRIAL_TTL)
                except Exception:
                    pass

    task = asyncio.create_task(sweep_loop())
    try:
        yield
    finally:
        task.cancel()


app = FastAPI(title="EasyCAD — CadQuery Chat", lifespan=_lifespan)


# ── Session middleware & dependency ───────────────────────────────────────────


@app.middleware("http")
async def _body_size_limit(request: Request, call_next):
    """Reject grossly oversized bodies before JSON parsing/retention (review C1)."""
    cl = request.headers.get("content-length")
    if cl is not None:
        try:
            if int(cl) > MAX_BODY_BYTES:
                return JSONResponse({"detail": "Request body too large."}, status_code=413)
        except ValueError:
            return JSONResponse({"detail": "Invalid Content-Length."}, status_code=400)
    return await call_next(request)


def _maybe_refresh_auth(request: Request, response) -> None:
    """Rolling session: re-issue the auth cookie with a fresh 1-year expiry on
    activity, so a returning user is never logged out (Facebook-style). Skips
    endpoints that just set/cleared the cookie (login/logout/delete) and tokens
    younger than AUTH_REFRESH_AFTER (avoids a Set-Cookie on every request)."""
    token = request.cookies.get(AUTH_COOKIE)
    if not token:
        return
    if any(k == b"set-cookie" and b"auth_token" in v for k, v in response.raw_headers):
        return  # an endpoint is authoritative about the cookie this request
    payload = jwt_utils.verify(token)
    if not payload or not payload.get("user_id"):
        return
    if time.time() - float(payload.get("iat", 0)) < AUTH_REFRESH_AFTER:
        return
    fresh = jwt_utils.sign({"user_id": payload["user_id"], "email": payload["email"]}, COOKIE_MAX_AGE)
    response.set_cookie(
        AUTH_COOKIE, fresh, max_age=COOKIE_MAX_AGE,
        httponly=True, samesite="lax", secure=SECURE_COOKIES,
    )


@app.middleware("http")
async def _session_cookie(request: Request, call_next):
    sid = request.cookies.get(SESSION_COOKIE)
    new = sid is None
    if new:
        sid = secrets.token_urlsafe(24)
    request.state.session_id = sid
    response = await call_next(request)
    if new:
        response.set_cookie(
            SESSION_COOKIE, sid, max_age=COOKIE_MAX_AGE,
            httponly=True, samesite="lax", secure=SECURE_COOKIES,
        )
    _maybe_refresh_auth(request, response)
    return response


def current_session(request: Request) -> Session:
    """Resolve the caller's session and (re)link it to their user, if logged in."""
    session = registry.get_or_create(request.state.session_id)
    token = request.cookies.get(AUTH_COOKIE)
    payload = jwt_utils.verify(token) if token else None
    session.user_id = int(payload["user_id"]) if payload and payload.get("user_id") else None
    return session


def locked_session(session: Session = Depends(current_session)):
    """Session dependency that serializes mutating requests per session (H1).

    The lock is held for the whole endpoint (generator dependency), so two
    concurrent requests on one cookie can't interleave a read-then-append.
    """
    with session.lock:
        yield session


def _check_capacity(session: Session) -> None:
    if session.store.at_capacity():
        raise HTTPException(429, f"Session step limit reached ({session.store.MAX_STEPS}).")


# ── Settings / auth helpers ───────────────────────────────────────────────────


def _coded_error(status: int, code: str, message: str) -> HTTPException:
    """HTTPException whose body carries a stable machine-readable `code` so the
    frontend maps code → notice instead of matching on prose (SPEC14)."""
    return HTTPException(status_code=status, detail={"code": code, "message": message})


def _provider_error(context: str, exc: Exception) -> HTTPException:
    return _coded_error(502, "provider_error", f"{context}: {exc}")


def _resolve_settings(session: Session) -> dict:
    if session.user_id:
        user = db.get_user(session.user_id)
        if user:
            return user.get("settings") or {}
    return session.settings


@dataclass(frozen=True)
class TrialIdent:
    """Who a trial generation is charged to — exactly one field is set. Kept as a
    small type (not a `(str, obj)` tuple) so counting is a method call, not a
    branch on a magic string with an unsafe cast."""
    user_id: int | None = None
    ip: str | None = None

    def count(self) -> None:
        if self.user_id is not None:
            db.incr_user_trial(self.user_id)
        elif self.ip is not None:
            db.incr_anon_trial(self.ip)


@dataclass(frozen=True)
class TrialStatus:
    tier: str  # "anon" | "user" | "byok"
    remaining: int | None  # None for byok (unlimited)
    ident: TrialIdent | None  # None for byok (nothing to charge)


def _trial_status(session: Session, request: Request) -> TrialStatus:
    """Return the trial tier + remaining count for the caller.

    - byok:  a saved key ⇒ unlimited (their key, their cost).
    - user:  signed in, no key ⇒ TRIAL_USER lifetime grant, tracked by user_id.
    - anon:  no account, no key ⇒ TRIAL_ANON lifetime grant, tracked by client IP.
    """
    s = _resolve_settings(session)
    if s.get("key"):
        return TrialStatus("byok", None, None)
    if session.user_id:
        used = db.get_user_trial(session.user_id)
        return TrialStatus("user", max(0, TRIAL_USER - used), TrialIdent(user_id=session.user_id))
    ip = _client_ip(request)
    used = db.get_anon_trial(ip)
    return TrialStatus("anon", max(0, TRIAL_ANON - used), TrialIdent(ip=ip))


def _settings_summary(session: Session, request: Request | None = None) -> dict:
    s = _resolve_settings(session)
    out = {
        "provider": s.get("provider") or DEFAULT_PROVIDER,
        "model": s.get("model"),
        "has_key": bool(s.get("key")),
        "providers": ui_providers(),
    }
    if request is not None:
        trial = _trial_status(session, request)
        out["trial_tier"] = trial.tier
        out["trial_remaining"] = trial.remaining
    return out


def _auth_summary(session: Session) -> dict:
    if session.user_id:
        user = db.get_user(session.user_id)
        if user:
            return {"authenticated": True, "email": user["email"]}
    return {"authenticated": False, "email": None}


def _apply_settings(session: Session, patch: dict) -> None:
    if session.user_id:
        user = db.get_user(session.user_id)
        current = (user.get("settings") if user else {}) or {}
        current.update(patch)
        db.update_settings(session.user_id, current)
    else:
        session.settings.update(patch)


def _resolve_llm(session: Session, request: Request, req_provider: str | None, req_model: str | None):
    """Resolve (provider, model, api_key, trial_ident) for a generation call.

    Precedence (SPEC14):
      1. Saved key → use it. Provider is the key's provider; model is the user's
         selection (their key, their cost). No trial counting (`trial_ident=None`).
      2. No key, trial remaining → operator DeepSeek key, provider+model hard-
         forced to deepseek/deepseek-chat (any request-supplied provider/model is
         ignored so nobody runs an expensive model on our key). `trial_ident` is
         returned so the caller increments the counter on success only.
      3. No key, trial exhausted → 402 with a machine-readable code.
    """
    s = _resolve_settings(session)
    api_key = s.get("key") or None
    if api_key:
        provider = s.get("provider") or req_provider or DEFAULT_PROVIDER
        model = s.get("model") or req_model
        return provider, model, api_key, None

    trial = _trial_status(session, request)
    if trial.remaining and trial.remaining > 0:
        # api_key stays None → make_client falls back to the operator env key.
        return TRIAL_PROVIDER, TRIAL_MODEL, None, trial.ident

    if trial.tier == "user":
        raise _coded_error(
            402, "trial_exhausted_user",
            f"You've used your {TRIAL_USER} free generations — add your LLM key to continue.",
        )
    raise _coded_error(
        402, "trial_exhausted_anon",
        f"Register for {TRIAL_USER} free generations, or add your own LLM key.",
    )


def _client_ip(request: Request) -> str:
    # redoproxy sets X-Real-Ip from the TCP peer via Header.Set (overwrite), so
    # it is the real client and cannot be spoofed by a client-supplied header.
    real = request.headers.get("x-real-ip")
    if real:
        return real.strip()
    # Fallback: redoproxy's SetXForwarded appends the real IP to any client-sent
    # X-Forwarded-For, so the LAST hop is the trusted one — never the first.
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[-1].strip()
    return request.client.host if request.client else "unknown"


def _gen_guard(session: Session) -> None:
    limit = int(os.getenv("EASYCAD_GEN_RATE_LIMIT", "30"))
    if not limiter.allow(f"gen:{session.id}", limit, 60):
        raise HTTPException(429, "Rate limit exceeded — slow down a moment.")


# ── Request models ────────────────────────────────────────────────────────────


class ChatRequest(BaseModel):
    prompt: str = Field(max_length=MAX_PROMPT)
    current_code: str | None = Field(default=None, max_length=MAX_CODE)
    provider: str = Field(default=DEFAULT_PROVIDER, max_length=MAX_NAME)
    model: str | None = Field(default=None, max_length=MAX_NAME)
    auto_refine: bool = True
    refined_prompt: str | None = Field(default=None, max_length=MAX_PROMPT)


class RefineRequest(BaseModel):
    prompt: str = Field(max_length=MAX_PROMPT)
    current_code: str | None = Field(default=None, max_length=MAX_CODE)
    provider: str = Field(default=DEFAULT_PROVIDER, max_length=MAX_NAME)
    model: str | None = Field(default=None, max_length=MAX_NAME)


class VariationsRequest(BaseModel):
    prompt: str = Field(max_length=MAX_PROMPT)
    current_code: str | None = Field(default=None, max_length=MAX_CODE)
    provider: str = Field(default=DEFAULT_PROVIDER, max_length=MAX_NAME)
    model: str | None = Field(default=None, max_length=MAX_NAME)
    auto_refine: bool = True
    count: int = Field(default=3, ge=1, le=4)


class CommitRequest(BaseModel):
    code: str = Field(max_length=MAX_CODE)
    original_prompt: str | None = Field(default=None, max_length=MAX_PROMPT)
    refined_prompt: str | None = Field(default=None, max_length=MAX_PROMPT)


class ExecuteRequest(BaseModel):
    code: str = Field(max_length=MAX_CODE)


class LoginRequest(BaseModel):
    email: str = Field(max_length=MAX_EMAIL)


class SettingsRequest(BaseModel):
    provider: str | None = Field(default=None, max_length=MAX_NAME)
    model: str | None = Field(default=None, max_length=MAX_NAME)
    key: str | None = Field(default=None, max_length=MAX_NAME)


class ValidateKeyRequest(BaseModel):
    provider: str = Field(max_length=MAX_NAME)
    key: str = Field(max_length=MAX_NAME)


# ── CAD session helpers ───────────────────────────────────────────────────────


_INITIAL_RESULT = None


def _initial_result():
    """Cached execution of INITIAL_CODE.

    The starting box is constant and deterministic, so we run CadQuery for it
    once per process and reuse the STL/geometry for every new session. This
    keeps session bootstrap (GET /api/session) cheap — a crawler hitting it no
    longer triggers a worker/CadQuery run.
    """
    global _INITIAL_RESULT
    if _INITIAL_RESULT is None:
        _INITIAL_RESULT = execute(INITIAL_CODE)
    return _INITIAL_RESULT


def _create_initial(store) -> None:
    res = _initial_result()
    store.add(
        kind="initial",
        code=res.code_with_geometry or INITIAL_CODE,
        stl_base64=res.stl_base64,
        geometry_info=res.geometry_info,
        success=res.success,
        error=res.error,
    )


def _ensure_initial(store) -> None:
    if not store.all():
        _create_initial(store)


def _ensure_step_stl(step) -> None:
    if step is None or step.stl_base64 or not step.success or not step.code:
        return
    res = execute(step.code)
    if res.success:
        step.stl_base64 = res.stl_base64
        if res.geometry_info:
            step.geometry_info = res.geometry_info


def _session_payload(session: Session, request: Request) -> dict:
    store = session.store
    current = store.current()
    _ensure_step_stl(current)
    return {
        "current_id": store.current_id,
        "current": current.to_public() if current else None,
        "steps": [s.to_public(include_stl=False) for s in store.all()],
        "providers": ui_providers(),
        "default_provider": DEFAULT_PROVIDER,
        "auth": _auth_summary(session),
        "settings": _settings_summary(session, request),
    }


def _base_code(store, current_code: str | None) -> str:
    if current_code is not None:
        return current_code
    current = store.current()
    return current.code if current else INITIAL_CODE


# ── Auth endpoints ────────────────────────────────────────────────────────────


@app.post("/api/auth/login")
def auth_login(req: LoginRequest, request: Request) -> dict:
    email = req.email.strip().lower()
    if not email or "@" not in email:
        raise HTTPException(400, "Invalid email address.")
    ip = _client_ip(request)
    if not limiter.allow(f"login:{email}", 5, 3600) or not limiter.allow(f"loginip:{ip}", 20, 3600):
        raise HTTPException(429, "Too many sign-in attempts. Try again later.")

    user = db.get_or_create_user(email)
    token = jwt_utils.sign(
        {"user_id": user["id"], "email": user["email"], "type": "magic"}, MAGIC_TTL
    )
    app_url = os.getenv("APP_URL", "http://localhost:8852")
    link = f"{app_url}/api/auth/callback?token={token}"
    try:
        send_mail(
            email,
            "Your EasyCAD sign-in link",
            f"Click to sign in to EasyCAD:\n{link}\n\nThis link expires in 15 minutes.",
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, "Could not send the email. Try again later.") from exc
    # Never leak whether the account existed.
    return {"ok": True}


@app.get("/api/auth/callback")
def auth_callback(token: str) -> Response:
    payload = jwt_utils.verify(token)
    if not payload or payload.get("type") != "magic" or not payload.get("user_id"):
        raise HTTPException(400, "Invalid or expired sign-in link.")
    session_token = jwt_utils.sign(
        {"user_id": payload["user_id"], "email": payload["email"]}, COOKIE_MAX_AGE
    )
    resp = RedirectResponse(url="/", status_code=302)
    resp.set_cookie(
        AUTH_COOKIE, session_token, max_age=COOKIE_MAX_AGE,
        httponly=True, samesite="lax", secure=SECURE_COOKIES,
    )
    return resp


@app.post("/api/auth/logout")
def auth_logout() -> Response:
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(AUTH_COOKIE)
    return resp


@app.get("/api/auth/me")
def auth_me(request: Request, session: Session = Depends(current_session)) -> dict:
    return {**_auth_summary(session), "settings": _settings_summary(session, request)}


@app.delete("/api/auth/me")
def auth_delete(session: Session = Depends(current_session)) -> Response:
    if not session.user_id:
        raise HTTPException(401, "Not signed in.")
    db.delete_user(session.user_id)
    session.user_id = None
    session.settings = {}
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(AUTH_COOKIE)
    return resp


# ── Settings endpoints ────────────────────────────────────────────────────────


@app.get("/api/settings")
def get_settings(request: Request, session: Session = Depends(current_session)) -> dict:
    return _settings_summary(session, request)


def _validate_settings_patch(session: Session, patch: dict) -> None:
    """Enforce the provider/model allow-list on save (SPEC14), so a direct API
    client can't persist a hidden provider (e.g. openai) or an off-list model
    that `_resolve_llm` would then pass through to the provider for a BYOK call."""
    provider = patch.get("provider")
    if provider is not None:
        cfg = PROVIDERS.get(provider)
        if not cfg or not cfg.get("ui"):
            raise _coded_error(400, "invalid_provider", f"Unknown or unavailable provider '{provider}'.")
    model = patch.get("model")
    if model is not None:
        effective = provider or _resolve_settings(session).get("provider") or DEFAULT_PROVIDER
        cfg = PROVIDERS.get(effective)
        if not cfg or model not in cfg["models"]:
            raise _coded_error(400, "invalid_model", f"Model '{model}' is not available for provider '{effective}'.")


@app.put("/api/settings")
def put_settings(
    req: SettingsRequest, request: Request, session: Session = Depends(current_session)
) -> dict:
    patch = {k: v for k, v in req.model_dump().items() if v is not None}
    _validate_settings_patch(session, patch)
    _apply_settings(session, patch)
    return _settings_summary(session, request)


@app.post("/api/settings/validate-key")
def validate_key(req: ValidateKeyRequest, request: Request, session: Session = Depends(current_session)) -> dict:
    """Validate a BYOK key before saving (SPEC14): fast prefix check, then a
    minimal live call. Prefix failures short-circuit before the live call so a
    bad-format key never spends a rate-limit unit or a provider call."""
    provider = req.provider
    if provider not in PROVIDERS:
        raise _coded_error(400, "invalid_provider", f"Unknown provider '{provider}'.")
    key = req.key.strip()
    if not key_prefix_ok(provider, key):
        expected = PROVIDERS[provider]["key_prefix"]
        return {"ok": False, "reason": f"This does not look like a {provider} key (expected it to start with '{expected}')."}

    # Rate-limit the live check (a real provider call → potential free key-tester).
    ip = _client_ip(request)
    if not limiter.allow(f"validatekey:{ip}", 10, 3600) or not limiter.allow(f"validatekey:{session.id}", 10, 3600):
        raise HTTPException(429, "Too many key checks. Try again later.")

    ok, reason = validate_key_live(provider, key)
    return {"ok": ok, "reason": reason}


# ── CAD session endpoints ─────────────────────────────────────────────────────


@app.get("/api/session")
def get_session(request: Request, session: Session = Depends(current_session)) -> dict:
    _ensure_initial(session.store)
    return _session_payload(session, request)


@app.post("/api/session/reset")
def reset_session(request: Request, session: Session = Depends(locked_session)) -> dict:
    session.store.reset()
    _create_initial(session.store)
    return _session_payload(session, request)


@app.get("/api/steps")
def list_steps(session: Session = Depends(current_session)) -> list[dict]:
    _ensure_initial(session.store)
    return [s.to_public(include_stl=False) for s in session.store.all()]


@app.get("/api/steps/{step_id}")
def get_step(step_id: int, session: Session = Depends(current_session)) -> dict:
    step = session.store.get(step_id)
    if step is None:
        raise HTTPException(404, f"Step {step_id} not found")
    _ensure_step_stl(step)
    return step.to_public()


@app.post("/api/steps/{step_id}/revert")
def revert_step(step_id: int, request: Request, session: Session = Depends(locked_session)) -> dict:
    if session.store.revert(step_id) is None:
        raise HTTPException(404, f"Step {step_id} not found")
    return _session_payload(session, request)


@app.post("/api/execute")
def api_execute(req: ExecuteRequest, session: Session = Depends(current_session)) -> dict:
    _gen_guard(session)
    res = execute(req.code)
    return {
        "success": res.success,
        "stl_base64": res.stl_base64,
        "geometry_info": res.geometry_info,
        "code_with_geometry": res.code_with_geometry,
        "error": res.error,
    }


@app.post("/api/execute-manual")
def api_execute_manual(req: ExecuteRequest, request: Request, session: Session = Depends(locked_session)) -> dict:
    _gen_guard(session)
    _check_capacity(session)
    res = execute(req.code)
    step = session.store.add(
        kind="manual",
        code=res.code_with_geometry or req.code,
        stl_base64=res.stl_base64,
        geometry_info=res.geometry_info,
        success=res.success,
        error=res.error,
        make_current=res.success,
    )
    return {"step": step.to_public(), "session": _session_payload(session, request)}


@app.post("/api/refine")
def api_refine(req: RefineRequest, request: Request, session: Session = Depends(current_session)) -> dict:
    # Rate-limit like /api/chat: /api/refine also spends the operator key on a
    # trial (a triage LLM call), so without this an anonymous caller could hit it
    # unbounded — triage is uncounted by design, so the gate is the only bound.
    _gen_guard(session)
    _ensure_initial(session.store)
    provider, model, api_key, _trial = _resolve_llm(session, request, req.provider, req.model)
    try:
        t = triage(req.prompt, _base_code(session.store, req.current_code), provider, model, api_key)
    except LLMError as exc:
        raise _provider_error("Triage error", exc) from exc
    return {
        "verdict": t.verdict,
        "refined_prompt": t.refined_prompt,
        "questions": t.questions,
        "reason": t.reason,
        "original_prompt": req.prompt,
    }


def _generate_and_step(
    session: Session,
    request: Request,
    base_code: str,
    gen_prompt: str,
    original_prompt: str,
    refined_prompt: str | None,
    provider: str,
    model: str | None,
    api_key: str | None,
    trial_ident: TrialIdent | None,
) -> dict:
    try:
        code = generate_code(base_code, gen_prompt, provider, model, api_key=api_key)
    except LLMError as exc:
        raise _provider_error("LLM error", exc) from exc

    # The LLM produced code (operator tokens spent) → this trial turn counts.
    # A failed provider call raised above, so the quota is only burned on success.
    # NB: charged on generation, not on a successful CadQuery execute — spending
    # is at the LLM call, and a per-execute gate would let bad-code prompts burn
    # operator tokens for free. See docs/CODE_REVIEW_HEAD.md (Spec #5).
    if trial_ident is not None:
        trial_ident.count()

    res = execute(code)
    step = session.store.add(
        kind="chat",
        original_prompt=original_prompt,
        refined_prompt=refined_prompt,
        code=res.code_with_geometry or code,
        stl_base64=res.stl_base64,
        geometry_info=res.geometry_info,
        success=res.success,
        error=res.error,
        make_current=res.success,
    )
    return {
        "action": "generated",
        "original_prompt": original_prompt,
        "refined_prompt": refined_prompt,
        "reason": None,
        "questions": [],
        "step": step.to_public(),
        "session": _session_payload(session, request),
    }


def _no_step(session: Session, request: Request, action: str, original_prompt: str, **extra) -> dict:
    payload = {
        "action": action,
        "original_prompt": original_prompt,
        "refined_prompt": None,
        "reason": None,
        "questions": [],
        "step": None,
        "session": _session_payload(session, request),
    }
    payload.update(extra)
    return payload


@app.post("/api/chat")
def api_chat(req: ChatRequest, request: Request, session: Session = Depends(locked_session)) -> dict:
    _gen_guard(session)
    _check_capacity(session)
    _ensure_initial(session.store)
    base_code = _base_code(session.store, req.current_code)
    provider, model, api_key, trial_ident = _resolve_llm(session, request, req.provider, req.model)

    if not req.auto_refine:
        gen_prompt = req.refined_prompt or req.prompt
        return _generate_and_step(
            session, request, base_code, gen_prompt, req.prompt, req.refined_prompt,
            provider, model, api_key, trial_ident,
        )

    try:
        t = triage(req.prompt, base_code, provider, model, api_key)
    except LLMError as exc:
        raise _provider_error("Triage error", exc) from exc

    if t.verdict == "clarify":
        return _no_step(session, request, "clarify", req.prompt, questions=t.questions)
    if t.verdict == "invalid":
        return _no_step(session, request, "invalid", req.prompt, reason=t.reason)
    if t.verdict == "refine":
        return _no_step(session, request, "confirm_refine", req.prompt, refined_prompt=t.refined_prompt)

    return _generate_and_step(
        session, request, base_code, req.prompt, req.prompt, None,
        provider, model, api_key, trial_ident,
    )


@app.post("/api/variations")
def api_variations(req: VariationsRequest, request: Request, session: Session = Depends(current_session)) -> dict:
    _gen_guard(session)
    _ensure_initial(session.store)
    base_code = _base_code(session.store, req.current_code)
    provider, model, api_key, trial_ident = _resolve_llm(session, request, req.provider, req.model)

    gen_prompt = req.prompt
    refined_prompt: str | None = None
    if req.auto_refine:
        try:
            t = triage(req.prompt, base_code, provider, model, api_key)
        except LLMError as exc:
            raise _provider_error("Triage error", exc) from exc
        if t.verdict == "clarify":
            return {"action": "clarify", "questions": t.questions, "reason": None,
                    "original_prompt": req.prompt, "refined_prompt": None, "candidates": []}
        if t.verdict == "invalid":
            return {"action": "invalid", "questions": [], "reason": t.reason,
                    "original_prompt": req.prompt, "refined_prompt": None, "candidates": []}
        if t.verdict == "refine":
            gen_prompt = t.refined_prompt or req.prompt
            refined_prompt = t.refined_prompt

    count = max(1, min(req.count, 4))
    candidates: list[dict] = []
    for _ in range(count):
        try:
            code = generate_code(base_code, gen_prompt, provider, model, temperature=0.7, api_key=api_key)
        except LLMError as exc:
            candidates.append(
                {"code": None, "stl_base64": None, "geometry_info": None, "success": False, "error": str(exc)}
            )
            continue
        res = execute(code)
        candidates.append({
            "code": res.code_with_geometry or code,
            "stl_base64": res.stl_base64,
            "geometry_info": res.geometry_info,
            "success": res.success,
            "error": res.error,
        })

    # One variations turn = one trial unit (like /api/chat), charged once if any
    # candidate's code was actually generated. Prevents unlimited free use of the
    # operator key via the ×N button before the first chat exhausts the grant.
    if trial_ident is not None and any(c["code"] for c in candidates):
        trial_ident.count()

    # Echo the post-charge trial status so the client just applies it, rather than
    # re-implementing the "charge once if any candidate" rule (which could drift).
    trial = _trial_status(session, request)
    return {
        "action": "generated",
        "questions": [],
        "reason": None,
        "original_prompt": req.prompt,
        "refined_prompt": refined_prompt,
        "candidates": candidates,
        "trial_tier": trial.tier,
        "trial_remaining": trial.remaining,
    }


@app.post("/api/commit")
def api_commit(req: CommitRequest, request: Request, session: Session = Depends(locked_session)) -> dict:
    _gen_guard(session)
    _check_capacity(session)
    res = execute(req.code)
    step = session.store.add(
        kind="chat",
        original_prompt=req.original_prompt,
        refined_prompt=req.refined_prompt,
        code=res.code_with_geometry or req.code,
        stl_base64=res.stl_base64,
        geometry_info=res.geometry_info,
        success=res.success,
        error=res.error,
        make_current=res.success,
    )
    return {"step": step.to_public(), "session": _session_payload(session, request)}


@app.get("/api/project/export")
def export_project(session: Session = Depends(current_session)) -> Response:
    _ensure_initial(session.store)
    body = json.dumps(session.store.to_project(), indent=2, ensure_ascii=False)
    return Response(
        content=body,
        media_type="application/json; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="easycad-project.json"'},
    )


@app.post("/api/project/import")
def import_project(project: dict, request: Request, session: Session = Depends(locked_session)) -> dict:
    try:
        session.store.load_project(project)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, f"Invalid project file: {exc}") from exc
    if not session.store.all():
        raise HTTPException(400, "Project file has no steps")
    return _session_payload(session, request)


@app.get("/api/export/{step_id}")
def export_step(step_id: int, session: Session = Depends(current_session)) -> Response:
    step = session.store.get(step_id)
    if step is not None:
        _ensure_step_stl(step)
    if step is None or not step.stl_base64:
        raise HTTPException(404, f"No STL available for step {step_id}")
    data = base64.b64decode(step.stl_base64)
    return Response(
        content=data,
        media_type="model/stl",
        headers={"Content-Disposition": f'attachment; filename="model_step_{step_id}.stl"'},
    )


# The landing page at "/" is light static content and may be indexed; the app
# (/app) and API are heavy/interactive and are kept off-limits to crawlers.
_SITE_URL = os.getenv("APP_URL", "https://easycad.bconf.com").rstrip("/")


@app.get("/robots.txt")
def robots() -> Response:
    body = f"User-agent: *\nDisallow: /app\nDisallow: /api\nSitemap: {_SITE_URL}/sitemap.xml\n"
    return Response(body, media_type="text/plain", headers={"Cache-Control": "public, max-age=86400"})


@app.get("/sitemap.xml")
def sitemap() -> Response:
    body = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        f"<url><loc>{_SITE_URL}/</loc></url></urlset>"
    )
    return Response(body, media_type="application/xml", headers={"Cache-Control": "public, max-age=86400"})


# Serve the built frontend (if present).
#   /       → static marketing landing (light, cacheable, crawler-friendly)
#   /app    → the SPA (interactive app; hashed assets under /assets)
# There is no global catch-all: unknown paths 404 rather than returning a 200
# SPA shell to probing bots.
if STATIC_DIR.exists():
    app.mount("/assets", StaticFiles(directory=STATIC_DIR / "assets"), name="assets")
    _INDEX = STATIC_DIR / "index.html"
    _LANDING = STATIC_DIR / "landing.html"

    @app.get("/")
    def landing() -> FileResponse:
        if _LANDING.exists():
            return FileResponse(_LANDING, headers={"Cache-Control": "public, max-age=300"})
        return FileResponse(_INDEX, headers={"Cache-Control": "no-cache"})

    # Static-root assets referenced by the landing (no global catch-all serves them).
    @app.get("/og-image.png")
    def og_image() -> FileResponse:
        return FileResponse(STATIC_DIR / "og-image.png", headers={"Cache-Control": "public, max-age=604800"})

    @app.get("/favicon.svg")
    def favicon() -> FileResponse:
        return FileResponse(STATIC_DIR / "favicon.svg", media_type="image/svg+xml",
                            headers={"Cache-Control": "public, max-age=604800"})

    @app.get("/app")
    @app.get("/app/{_path:path}")
    def spa(_path: str = "") -> FileResponse:
        return FileResponse(_INDEX, headers={"Cache-Control": "no-cache"})
