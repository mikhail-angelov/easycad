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
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import db, jwt_utils
from .cadquery_exec import execute
from .llm import DEFAULT_PROVIDER, INITIAL_CODE, LLMError, PROVIDERS, generate_code
from .mail import send_mail
from .ratelimit import RateLimiter
from .refiner import triage
from .session_registry import Session, build_registry

ROOT = Path(__file__).resolve().parent.parent
STATIC_DIR = ROOT / "static"

SESSION_COOKIE = "easycad_session"
AUTH_COOKIE = "auth_token"
SECURE_COOKIES = os.getenv("EASYCAD_SECURE_COOKIES") == "1"
COOKIE_MAX_AGE = 30 * 24 * 3600  # 30 days
MAGIC_TTL = 15 * 60  # 15 minutes
REQUIRE_USER_KEY = os.getenv("EASYCAD_REQUIRE_USER_KEY") == "1"

load_dotenv(ROOT / ".env")

registry = build_registry()
limiter = RateLimiter()


@asynccontextmanager
async def _lifespan(app: FastAPI):
    async def sweep_loop() -> None:
        while True:
            await asyncio.sleep(60)
            try:
                registry.sweep()
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
    return response


def current_session(request: Request) -> Session:
    """Resolve the caller's session and (re)link it to their user, if logged in."""
    session = registry.get_or_create(request.state.session_id)
    token = request.cookies.get(AUTH_COOKIE)
    payload = jwt_utils.verify(token) if token else None
    session.user_id = int(payload["user_id"]) if payload and payload.get("user_id") else None
    return session


# ── Settings / auth helpers ───────────────────────────────────────────────────


def _resolve_settings(session: Session) -> dict:
    if session.user_id:
        user = db.get_user(session.user_id)
        if user:
            return user.get("settings") or {}
    return session.settings


def _settings_summary(session: Session) -> dict:
    s = _resolve_settings(session)
    return {
        "provider": s.get("provider") or DEFAULT_PROVIDER,
        "model": s.get("model"),
        "has_key": bool(s.get("key")),
    }


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


def _resolve_llm(session: Session, req_provider: str | None, req_model: str | None):
    """Return (provider, model, api_key) for a generation call, BYOK-aware."""
    s = _resolve_settings(session)
    provider = s.get("provider") or req_provider or DEFAULT_PROVIDER
    model = s.get("model") or req_model
    api_key = s.get("key") or None
    if REQUIRE_USER_KEY and not api_key:
        raise HTTPException(400, "Add your LLM key in settings to generate.")
    return provider, model, api_key


def _client_ip(request: Request) -> str:
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _gen_guard(session: Session) -> None:
    limit = int(os.getenv("EASYCAD_GEN_RATE_LIMIT", "30"))
    if not limiter.allow(f"gen:{session.id}", limit, 60):
        raise HTTPException(429, "Rate limit exceeded — slow down a moment.")


# ── Request models ────────────────────────────────────────────────────────────


class ChatRequest(BaseModel):
    prompt: str
    current_code: str | None = None
    provider: str = DEFAULT_PROVIDER
    model: str | None = None
    auto_refine: bool = True
    refined_prompt: str | None = None


class RefineRequest(BaseModel):
    prompt: str
    current_code: str | None = None
    provider: str = DEFAULT_PROVIDER
    model: str | None = None


class VariationsRequest(BaseModel):
    prompt: str
    current_code: str | None = None
    provider: str = DEFAULT_PROVIDER
    model: str | None = None
    auto_refine: bool = True
    count: int = 3


class CommitRequest(BaseModel):
    code: str
    original_prompt: str | None = None
    refined_prompt: str | None = None


class ExecuteRequest(BaseModel):
    code: str


class LoginRequest(BaseModel):
    email: str


class SettingsRequest(BaseModel):
    provider: str | None = None
    model: str | None = None
    key: str | None = None


# ── CAD session helpers ───────────────────────────────────────────────────────


def _create_initial(store) -> None:
    res = execute(INITIAL_CODE)
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


def _session_payload(session: Session) -> dict:
    store = session.store
    current = store.current()
    _ensure_step_stl(current)
    return {
        "current_id": store.current_id,
        "current": current.to_public() if current else None,
        "steps": [s.to_public(include_stl=False) for s in store.all()],
        "providers": {name: cfg["default_model"] for name, cfg in PROVIDERS.items()},
        "default_provider": DEFAULT_PROVIDER,
        "auth": _auth_summary(session),
        "settings": _settings_summary(session),
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
def auth_me(session: Session = Depends(current_session)) -> dict:
    return {**_auth_summary(session), "settings": _settings_summary(session)}


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
def get_settings(session: Session = Depends(current_session)) -> dict:
    return _settings_summary(session)


@app.put("/api/settings")
def put_settings(req: SettingsRequest, session: Session = Depends(current_session)) -> dict:
    patch = {k: v for k, v in req.model_dump().items() if v is not None}
    _apply_settings(session, patch)
    return _settings_summary(session)


# ── CAD session endpoints ─────────────────────────────────────────────────────


@app.get("/api/session")
def get_session(session: Session = Depends(current_session)) -> dict:
    _ensure_initial(session.store)
    return _session_payload(session)


@app.post("/api/session/reset")
def reset_session(session: Session = Depends(current_session)) -> dict:
    session.store.reset()
    _create_initial(session.store)
    return _session_payload(session)


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
def revert_step(step_id: int, session: Session = Depends(current_session)) -> dict:
    if session.store.revert(step_id) is None:
        raise HTTPException(404, f"Step {step_id} not found")
    return _session_payload(session)


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
def api_execute_manual(req: ExecuteRequest, session: Session = Depends(current_session)) -> dict:
    _gen_guard(session)
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
    return {"step": step.to_public(), "session": _session_payload(session)}


@app.post("/api/refine")
def api_refine(req: RefineRequest, session: Session = Depends(current_session)) -> dict:
    _ensure_initial(session.store)
    provider, model, api_key = _resolve_llm(session, req.provider, req.model)
    try:
        t = triage(req.prompt, _base_code(session.store, req.current_code), provider, model, api_key)
    except LLMError as exc:
        raise HTTPException(502, f"Triage error: {exc}") from exc
    return {
        "verdict": t.verdict,
        "refined_prompt": t.refined_prompt,
        "questions": t.questions,
        "reason": t.reason,
        "original_prompt": req.prompt,
    }


def _generate_and_step(
    session: Session,
    base_code: str,
    gen_prompt: str,
    original_prompt: str,
    refined_prompt: str | None,
    provider: str,
    model: str | None,
    api_key: str | None,
) -> dict:
    try:
        code = generate_code(base_code, gen_prompt, provider, model, api_key=api_key)
    except LLMError as exc:
        raise HTTPException(502, f"LLM error: {exc}") from exc

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
        "session": _session_payload(session),
    }


def _no_step(session: Session, action: str, original_prompt: str, **extra) -> dict:
    payload = {
        "action": action,
        "original_prompt": original_prompt,
        "refined_prompt": None,
        "reason": None,
        "questions": [],
        "step": None,
        "session": _session_payload(session),
    }
    payload.update(extra)
    return payload


@app.post("/api/chat")
def api_chat(req: ChatRequest, session: Session = Depends(current_session)) -> dict:
    _gen_guard(session)
    _ensure_initial(session.store)
    base_code = _base_code(session.store, req.current_code)
    provider, model, api_key = _resolve_llm(session, req.provider, req.model)

    if not req.auto_refine:
        gen_prompt = req.refined_prompt or req.prompt
        return _generate_and_step(
            session, base_code, gen_prompt, req.prompt, req.refined_prompt, provider, model, api_key
        )

    try:
        t = triage(req.prompt, base_code, provider, model, api_key)
    except LLMError as exc:
        raise HTTPException(502, f"Triage error: {exc}") from exc

    if t.verdict == "clarify":
        return _no_step(session, "clarify", req.prompt, questions=t.questions)
    if t.verdict == "invalid":
        return _no_step(session, "invalid", req.prompt, reason=t.reason)
    if t.verdict == "refine":
        return _no_step(session, "confirm_refine", req.prompt, refined_prompt=t.refined_prompt)

    return _generate_and_step(session, base_code, req.prompt, req.prompt, None, provider, model, api_key)


@app.post("/api/variations")
def api_variations(req: VariationsRequest, session: Session = Depends(current_session)) -> dict:
    _gen_guard(session)
    _ensure_initial(session.store)
    base_code = _base_code(session.store, req.current_code)
    provider, model, api_key = _resolve_llm(session, req.provider, req.model)

    gen_prompt = req.prompt
    refined_prompt: str | None = None
    if req.auto_refine:
        try:
            t = triage(req.prompt, base_code, provider, model, api_key)
        except LLMError as exc:
            raise HTTPException(502, f"Triage error: {exc}") from exc
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

    return {
        "action": "generated",
        "questions": [],
        "reason": None,
        "original_prompt": req.prompt,
        "refined_prompt": refined_prompt,
        "candidates": candidates,
    }


@app.post("/api/commit")
def api_commit(req: CommitRequest, session: Session = Depends(current_session)) -> dict:
    _gen_guard(session)
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
    return {"step": step.to_public(), "session": _session_payload(session)}


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
def import_project(project: dict, session: Session = Depends(current_session)) -> dict:
    try:
        session.store.load_project(project)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, f"Invalid project file: {exc}") from exc
    if not session.store.all():
        raise HTTPException(400, "Project file has no steps")
    return _session_payload(session)


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


# Serve the built frontend (if present). The SPA catch-all is registered last so
# /api/* routes win.
if STATIC_DIR.exists():
    app.mount("/assets", StaticFiles(directory=STATIC_DIR / "assets"), name="assets")

    @app.get("/{_path:path}")
    def spa(_path: str) -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html", headers={"Cache-Control": "no-cache"})
