"""SPEC11 CadQuery Chat API, made multi-tenant per SPEC13.

Text-only incremental 3D-model builder. Each visitor gets an in-memory session
(keyed by the `easycad_session` cookie) with a sliding idle TTL — no working
state touches disk. Passwordless magic-link auth (SPEC13) lets users store their
own LLM key + settings; anonymous users keep settings in their session only.
Users persist CAD work themselves via project export/import.
"""

import asyncio
import base64
import hashlib
import json
import logging
import os
import re
import secrets
import threading
import time
import traceback
import urllib.error
import urllib.request
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv
from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import crashlog, crypto, db, jwt_utils, log_context, metrics
from .cadquery_exec import append_geometry_block, execute, export_model, strip_geometry_block
from .version import VERSION
from .llm import (
    DEFAULT_PROVIDER,
    INITIAL_CODE,
    LLMEmptyResponse,
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
TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"

# Load .env BEFORE the module-level os.getenv() config block below — otherwise
# every tunable read at import (SECURE_COOKIES, TRIAL_*, MAX_REPAIR, …) would miss
# values set only in .env and silently fall back to its default (local-dev bug).
load_dotenv(ROOT / ".env")

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
# PAT→cookie exchange issues a SHORT-lived auth cookie (SPEC22 §6.1): revoking or
# expiring a PAT then stops working within this window, because the agent's cookie
# dies and re-bootstrapping re-checks revoked_at/expires_at. The cookie is stamped
# `pat:true` so _maybe_refresh_auth never promotes it to the 1-year rolling window.
PAT_COOKIE_MAX_AGE = 12 * 3600  # 12 hours

# Free-trial grants on the operator's DeepSeek key (SPEC14). Lifetime, not
# periodic. Set both to 0 to disable the trial entirely (supersedes the old
TRIAL_ANON = int(os.getenv("EASYCAD_TRIAL_ANON", "1"))
TRIAL_USER = int(os.getenv("EASYCAD_TRIAL_USER", "10"))
# Prune anon_trial rows older than this (reuses no external scheduler — swept in
# the same background loop as sessions).
ANON_TRIAL_TTL = float(os.getenv("EASYCAD_ANON_TRIAL_TTL", str(30 * 24 * 3600)))  # 30 days

# ── Launch cost controls (SPEC14 hardening). 0 = disabled/unlimited. ──────────
# Global kill-switch: cap operator-key TRIAL generations per (UTC) day, so a
# traffic spike can't drain the shared DeepSeek key. Over budget → trial callers
# are asked to add their own key.
TRIAL_DAILY_BUDGET = int(os.getenv("EASYCAD_TRIAL_DAILY_BUDGET", "0"))
# Per-IP generation rate (per minute) — closes the cookie-rotation bypass of the
# per-session limit for the LLM endpoints.
GEN_RATE_LIMIT_IP = int(os.getenv("EASYCAD_GEN_RATE_LIMIT_IP", "60"))
# Max concurrent LLM/worker generation requests on this instance.
MAX_INFLIGHT_GEN = int(os.getenv("EASYCAD_MAX_INFLIGHT_GEN", "0"))
# In-turn repair loop: on an execution failure, feed the error back to the model
# and let it fix its own code, up to this many EXTRA attempts (0 = one-shot, the
# pre-repair behaviour — also the bench ablation baseline, bench-SPEC §6.1).
# max(0, …): a negative value would make `range(MAX_REPAIR + 1)` empty, so the
# generate/execute loop never runs and `res` stays None → 500. Clamp to one-shot.
MAX_REPAIR = max(0, int(os.getenv("EASYCAD_MAX_REPAIR", "2")))
# The single operator identity. Admin pages/endpoints require being signed in as
# this email; feedback and ops alerts are sent here. Unset → admin is hidden and
# no ops mail is sent. This is the ONLY admin/ops address the app uses.
ADMIN_EMAIL = (os.getenv("ADMIN_EMAIL") or "").strip().lower()
# Send the daily crash-report email (SPEC21 W2). 0 disables the mail (the JSONL
# sink and retention still run — only the digest email is suppressed).
CRASH_REPORT = os.getenv("EASYCAD_CRASH_REPORT", "1") != "0"

# ── Logging setup ─────────────────────────────────────────────────────────────
# Configure logging EXPLICITLY via dictConfig (not basicConfig, which only owns
# root and is a no-op once uvicorn installs its own propagate=False handlers).
# This owns root + the three uvicorn loggers, routes them all through one handler
# with a context-aware formatter, so every line — including access/error and the
# crash record — carries the request's trace_id/user/version (SPEC21 W1).
LOG_LEVEL = os.getenv("EASYCAD_LOG_LEVEL", "INFO").upper()
log_context.configure_logging(LOG_LEVEL)
log = logging.getLogger("easycad")
log.setLevel(LOG_LEVEL)

# Input bounds (review C1) — reject oversized payloads before parsing/retention.
MAX_BODY_BYTES = int(os.getenv("EASYCAD_MAX_BODY_BYTES", str(2_000_000)))  # 2 MB
MAX_PROMPT = 20_000
MAX_CODE = 200_000
MAX_NAME = 500
MAX_EMAIL = 320
MAX_FEEDBACK = 4_000


def _check_required_env() -> None:
    """Fail-fast on boot when a security-critical env var is missing in production
    (EASYCAD_SECURE_COOKIES=1) — better to refuse to start than to run on a PUBLIC
    default secret. Feature-config gaps (trial key) only warn, so a deliberately-
    minimal deploy still boots."""
    problems = []
    # JWT_SECRET is checked on its OWN — it signs auth tokens, so it must be a real
    # secret even if a separate EASYCAD_SECRETS_KEY handles encryption. Without it,
    # anyone can forge magic-link / session tokens (account takeover).
    if not jwt_utils.secret_is_secure():
        problems.append("JWT_SECRET (signs auth tokens — a public default lets anyone forge sessions)")
    # Encryption key: JWT_SECRET or EASYCAD_SECRETS_KEY. Separate from the above so
    # an explicit EASYCAD_SECRETS_KEY=<default> is still caught.
    if not crypto.secret_is_secure():
        problems.append("JWT_SECRET or EASYCAD_SECRETS_KEY (encrypts BYOK keys at rest)")
    # ADMIN_EMAIL is now the destination for the daily crash report (SPEC21 W2) and
    # the SPEC19 ops mail. Unset in prod → silently no crash mail, a foot-gun — so
    # it joins the fail-fast checks (dev only warns, below).
    if not ADMIN_EMAIL:
        problems.append("ADMIN_EMAIL (destination for the daily crash report + ops mail)")
    if problems:
        if SECURE_COOKIES:
            raise RuntimeError("Refusing to start: missing/insecure " + "; ".join(problems) + ".")
        for p in problems:
            log.warning("Insecure config: %s — OK for local dev only.", p)
    if SECURE_COOKIES and (TRIAL_ANON > 0 or TRIAL_USER > 0) and not os.getenv("DEEP_SEEK_KEY"):
        log.warning("Free trial is enabled but DEEP_SEEK_KEY is unset — trial generations will fail.")


_check_required_env()
crashlog.ensure_dir()  # create the crash-log dir on boot (record also creates lazily)

registry = build_registry()
limiter = RateLimiter()

# Daily operator-budget counter (in-memory, single-instance per SPEC13; resets at
# UTC midnight and on restart — fine for a soft cost kill-switch).
_budget_lock = threading.Lock()
_budget_state = {"day": "", "used": 0}
# Non-blocking concurrency cap for generation endpoints (None = disabled).
_gen_semaphore = threading.BoundedSemaphore(MAX_INFLIGHT_GEN) if MAX_INFLIGHT_GEN > 0 else None


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


app = FastAPI(title="text2part — CadQuery Chat", lifespan=_lifespan)


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


def issue_auth_cookie(resp, user_id, email: str, max_age: int, pat: bool = False) -> None:
    """Single definition of the `auth` session cookie contract (SPEC22).

    Both the magic-link callback and the PAT exchange mint the SAME cookie so
    every downstream route stays unchanged. `max_age` differs by caller
    (COOKIE_MAX_AGE for magic-link, PAT_COOKIE_MAX_AGE for PAT); `pat=True` stamps
    a provenance marker so _maybe_refresh_auth never promotes a 12h PAT cookie to
    the 1-year rolling window."""
    payload = {"user_id": user_id, "email": email}
    if pat:
        payload["pat"] = True
    token = jwt_utils.sign(payload, max_age)
    resp.set_cookie(
        AUTH_COOKIE, token, max_age=max_age,
        httponly=True, samesite="lax", secure=SECURE_COOKIES,
    )


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
    # PAT cookies are a hard 12h cap from issuance (SPEC22 §6.1): never refresh
    # them, else the agent's first busy minute would silently promote a revocable
    # 12h cookie to a 1-year rolling session and destroy the revocation bound.
    if payload.get("pat"):
        return
    # Time-gate first so a normal authenticated request in the refresh window does
    # no extra DB work (current_session already resolved identity for this request).
    if time.time() - float(payload.get("iat", 0)) < AUTH_REFRESH_AFTER:
        return
    # About to roll the cookie forward: a deleted account's cookie must NOT get a
    # fresh 1-year expiry (SPEC22 §2.3). If the user row is gone, skip refresh and
    # clear the stale cookie so the request settles anonymous.
    if not db.get_user(int(payload["user_id"])):
        response.delete_cookie(AUTH_COOKIE)
        return
    issue_auth_cookie(response, payload["user_id"], payload["email"], COOKIE_MAX_AGE)


@app.middleware("http")
async def _session_cookie(request: Request, call_next):
    sid = request.cookies.get(SESSION_COOKIE)
    new = sid is None
    if new:
        sid = secrets.token_urlsafe(24)
    request.state.session_id = sid
    # Fill the log context now that the session id is known (the outer context
    # middleware seeded trace_id/version/user). Anonymous callers get a stable
    # `anon:<session-prefix>` identity; signed-in callers keep their email.
    log_context.update_context(session_id=sid)
    if log_context.current().get("user") in (None, "-"):
        log_context.update_context(user=f"anon:{sid[:8]}")
    # Stash the resolved identity on the Request so the exception handler — which
    # runs after the ContextVar is reset — can still stamp user/session (W1).
    request.state.log_user = log_context.current().get("user", "-")
    request.state.log_session = sid
    response = await call_next(request)
    if new:
        response.set_cookie(
            SESSION_COOKIE, sid, max_age=COOKIE_MAX_AGE,
            httponly=True, samesite="lax", secure=SECURE_COOKIES,
        )
    _maybe_refresh_auth(request, response)
    return response


# Access log (registered last → outermost middleware, so it wraps and times the
# whole request). One line per request with method, path, status and duration.
# Static-asset GETs are dropped to keep the signal high. This is the "positive
# path" visibility the container logs were missing.
_ACCESS_LOG_SKIP_PREFIXES = ("/assets", "/static")


def _crash_event(request: Request, status: int, kind: str, *,
                 service: str = "app", code: str | None = None,
                 exc: BaseException | None = None) -> dict:
    """Build one crash record from the W1 context + request specifics (W2).
    `kind:error` carries the live exception; `kind:operational` carries the
    coded 5xx via op_error. Scrubbing/length-capping happens in crashlog.record."""
    ctx = log_context.current()
    ev = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "trace_id": getattr(request.state, "trace_id", ctx.get("trace_id", "-")),
        "service": service,
        "method": request.method,
        "path": request.url.path,
        "status": status,
        "kind": kind,
        "code": code,
        "user": ctx.get("user", "-"),
        "session_id": ctx.get("session_id", "-"),
        "version": VERSION,
        "exc_class": None,
        "exc_message": None,
        "traceback_tail": None,
    }
    if exc is not None:
        ev["exc_class"] = type(exc).__name__
        ev["exc_message"] = str(exc)
        tb = traceback.format_exception(type(exc), exc, exc.__traceback__)
        ev["traceback_tail"] = "".join(tb)[-crashlog.MAX_TB:]
    return ev


@app.middleware("http")
async def _access_log(request: Request, call_next):
    path = request.url.path
    if request.method == "GET" and path.startswith(_ACCESS_LOG_SKIP_PREFIXES):
        return await call_next(request)
    _t0 = time.monotonic()
    try:
        response = await call_next(request)
    except Exception as exc:
        # Unhandled error escaping the app: emit the concise access line here (no
        # traceback — the exception handler below logs the full traceback once),
        # record the crash ONCE (the single chokepoint — the exception handler does
        # not), and re-raise so that handler turns it into a 500 for the client.
        dur_ms = int((time.monotonic() - _t0) * 1000)
        log.error("%s %s -> 500 (%dms) unhandled: %s", request.method, path, dur_ms, exc)
        if not getattr(request.state, "crash_recorded", False):
            request.state.crash_recorded = True
            crashlog.record(_crash_event(request, 500, "error", exc=exc))
        raise
    dur_ms = int((time.monotonic() - _t0) * 1000)
    level = logging.INFO
    if response.status_code >= 500:
        level = logging.ERROR
    elif response.status_code >= 400:
        level = logging.WARNING
    log.log(level, "%s %s -> %d (%dms)", request.method, path, response.status_code, dur_ms)
    # Operational/explicit 5xx that never raised (worker outage mapped to a coded
    # HTTPException, server_busy, provider_error…): record it as `kind:operational`,
    # labelled from op_error. A normal HTTP-200 failed generation is NOT a crash.
    if response.status_code >= 500 and not getattr(request.state, "crash_recorded", False):
        request.state.crash_recorded = True
        op = getattr(request.state, "op_error", None) or {}
        crashlog.record(_crash_event(
            request, response.status_code, "operational",
            service=op.get("service", "app"), code=op.get("code"),
        ))
    return response


# ── Daily crash report trigger (lazy, at-most-once) ───────────────────────────
# An in-memory guard is only a hot-path short-circuit (avoid the FS on every
# request); the authority is the single atomic marker crashlog.claim_report writes.
_report_lock = threading.Lock()
_report_day = ""


def _maybe_send_daily_report() -> None:
    """On the first request of a new UTC day, claim the day (one atomic marker),
    email yesterday's digest best-effort, and apply retention. At-most-once: a
    mail failure loses that day's report by design (never a duplicate). Wrapped so
    it never breaks a request (the _budget_alert contract)."""
    global _report_day
    today = time.strftime("%Y-%m-%d", time.gmtime())
    with _report_lock:
        if _report_day == today:
            return
    outcome = crashlog.claim_report(today)
    if outcome is None:
        # Transient FS error — do NOT mark the day done; a later request retries.
        return
    with _report_lock:
        _report_day = today  # definitive (claimed or already-sent): stop re-checking
    if outcome is False:
        return  # another worker/restart already sent today's report
    yesterday = time.strftime("%Y-%m-%d", time.gmtime(time.time() - 86400))
    try:
        n, subject, body = crashlog.build_digest(yesterday)
        if CRASH_REPORT and ADMIN_EMAIL:
            try:
                send_mail(ADMIN_EMAIL, subject, body)
            except Exception:  # noqa: BLE001 — mail failure loses this report, by design
                log.warning("Daily crash report email failed for %s", yesterday)
        log.info("Daily crash report %s: %d crashes", yesterday, n)
    finally:
        crashlog.apply_retention()


def _daily_report_tick() -> None:
    try:
        _maybe_send_daily_report()
    except Exception:  # noqa: BLE001 — never break a request
        pass


# Fire-and-forget tasks are tracked so they aren't garbage-collected mid-flight.
_report_tasks: set = set()


def _spawn_daily_report_if_due() -> None:
    """Per-request hook. The common case is a single date-string compare — no work,
    no thread hop. Only when the UTC day has rolled over do we spawn the tick as a
    fire-and-forget task (its FS work + blocking SMTP run off the request path, so
    NO request — not even the day's first — waits for it). Concurrent spawns at the
    day boundary are safe: the atomic marker lets only one send win."""
    if _report_day == time.strftime("%Y-%m-%d", time.gmtime()):
        return
    try:
        task = asyncio.create_task(asyncio.to_thread(_daily_report_tick))
    except RuntimeError:
        return  # no running loop (shouldn't happen in an async middleware)
    _report_tasks.add(task)
    task.add_done_callback(_report_tasks.discard)


# Registered LAST → the OUTERMOST user middleware. Its `finally` runs after the
# access-log middleware's, so the crash record + access line inside are still
# stamped with this request's context before the ContextVar is reset.
@app.middleware("http")
async def _request_context(request: Request, call_next):
    trace_id = secrets.token_hex(8)
    request.state.trace_id = trace_id
    # Resolve the caller identity from the auth cookie (email) here; session_id and
    # the anon fallback are filled in _session_cookie (which runs inside this).
    token = request.cookies.get(AUTH_COOKIE)
    payload = jwt_utils.verify(token) if token else None
    user = payload.get("email") if payload and payload.get("user_id") else None
    ctx_token = log_context.set_context(trace_id=trace_id, version=VERSION, user=user)
    try:
        _spawn_daily_report_if_due()  # cheap gate; off-request-path when the day rolls over
        response = await call_next(request)
    finally:
        # A thread-pool task/thread can be reused, so reset unconditionally — else a
        # later request could inherit this caller's identity.
        log_context.reset_context(ctx_token)
    response.headers["X-Trace-Id"] = trace_id  # error path: the exception handler sets it
    return response


@app.exception_handler(Exception)
async def _unhandled_exception(request: Request, exc: Exception):
    """Last-resort handler so a bug returns a clean 500 JSON body instead of the
    client seeing a dropped/reset connection.

    It runs in ServerErrorMiddleware — OUTSIDE the context middleware, whose
    `finally` has already reset the ContextVar — so it must NOT rely on the live
    context: it reads `request.state.trace_id` (still on the Request), stamps it
    on the log line via `extra=`, echoes it in the body, and sets `X-Trace-Id` on
    its own response (the middleware never got to add it for a request that
    raised). It does NOT record the crash — that already happened once at the
    access-log chokepoint (W2)."""
    trace_id = getattr(request.state, "trace_id", "-")
    log.exception(
        "Unhandled error on %s %s: %s", request.method, request.url.path, exc,
        extra={
            "trace_id": trace_id, "version": VERSION,
            "user": getattr(request.state, "log_user", "-") or "-",
            "session_id": getattr(request.state, "log_session", "-"),
        },
    )
    return JSONResponse(
        {"detail": "Internal server error.", "trace_id": trace_id},
        status_code=500, headers={"X-Trace-Id": trace_id},
    )


def current_session(request: Request) -> Session:
    """Resolve the caller's session and (re)link it to their user, if logged in."""
    session = registry.get_or_create(request.state.session_id)
    token = request.cookies.get(AUTH_COOKIE)
    payload = jwt_utils.verify(token) if token else None
    user_id = int(payload["user_id"]) if payload and payload.get("user_id") else None
    # A still-valid cookie (magic-link OR PAT) for a deleted account must resolve
    # as anonymous, not keep the session "signed in" until the JWT expires
    # (SPEC22 §2.3). One existence check covers both cookie kinds.
    session.user_id = user_id if user_id is not None and db.get_user(user_id) else None
    return session


def locked_session(session: Session = Depends(current_session)):
    """Session dependency that serializes mutating requests per session (H1).

    The lock is held for the whole endpoint (generator dependency), so two
    concurrent requests on one cookie can't interleave a read-then-append.
    """
    with session.lock:
        yield session


def require_admin(session: Session = Depends(current_session)) -> Session:
    """Admin gate: the caller must be signed in as `ADMIN_EMAIL`. Returns 404
    (not 403) when unset or mismatched, so the admin surface never reveals that
    it exists to anyone but the operator."""
    if not ADMIN_EMAIL or not session.user_id:
        raise HTTPException(404, "Not found")
    user = db.get_user(session.user_id)
    if not user or user["email"].strip().lower() != ADMIN_EMAIL:
        raise HTTPException(404, "Not found")
    return session


def _check_capacity(session: Session) -> None:
    if session.store.at_capacity():
        raise HTTPException(429, f"Session step limit reached ({session.store.MAX_STEPS}).")


# ── Settings / auth helpers ───────────────────────────────────────────────────


def _coded_error(status: int, code: str, message: str) -> HTTPException:
    """HTTPException whose body carries a stable machine-readable `code` so the
    frontend maps code → notice instead of matching on prose (SPEC14)."""
    return HTTPException(status_code=status, detail={"code": code, "message": message})


def _provider_error(context: str, exc: Exception) -> HTTPException:
    metrics.incr("provider_errors")
    detail = crashlog.scrub_text(str(exc))
    log.warning("provider_error %s: %s", context, detail)
    return _coded_error(502, "provider_error", f"{context}: {detail}")


def _empty_response_error() -> HTTPException:
    return _coded_error(
        422, "empty_response",
        "The model could not produce an answer. Please rephrase your request.",
    )


async def _await_llm(request: Request, operation: str, value):
    """Await one LLM call, cancelling its HTTP connection if the request ends."""
    llm_task = asyncio.create_task(value)
    try:
        while not llm_task.done():
            await asyncio.wait({llm_task}, timeout=0.1)
            if llm_task.done():
                break
            if await request.is_disconnected():
                llm_task.cancel()
                await asyncio.gather(llm_task, return_exceptions=True)
                metrics.incr("llm_disconnect_cancelled")
                log.info("llm.disconnect_cancelled operation=%s", operation)
                raise HTTPException(status_code=499, detail={"code": "client_disconnected", "message": "Client disconnected."})
        return await llm_task
    except asyncio.CancelledError:
        # ASGI cancels a streaming response coroutine as soon as its client
        # disconnects.  Propagate that cancellation into the non-streaming
        # provider request too; otherwise its HTTP call can outlive the tab.
        llm_task.cancel()
        await asyncio.gather(llm_task, return_exceptions=True)
        metrics.incr("llm_disconnect_cancelled")
        log.info("llm.disconnect_cancelled operation=%s", operation)
        raise


async def _execute_if_connected(request: Request, code: str):
    """Avoid dispatching CadQuery after the user/proxy has already disconnected."""
    if await request.is_disconnected():
        metrics.incr("worker_skipped_disconnect")
        raise HTTPException(status_code=499, detail={"code": "client_disconnected", "message": "Client disconnected."})
    return await asyncio.to_thread(execute, code)


async def _emit_progress(progress, stage: str) -> None:
    if progress is not None:
        await progress(stage)


def _sse_event(name: str, data: dict) -> str:
    return f"event: {name}\ndata: {json.dumps(data, ensure_ascii=False, separators=(',', ':'))}\n\n"


# Operational execution failures (worker timeout / transport) surfaced as coded,
# localized "try again" notices instead of a raw error string in the chat. Ordinary
# CadQuery/model errors carry no `code` and stay in the chat as a failed step (W1).
_EXEC_OPERATIONAL = {
    "execution_timeout": (504, "That model took too long — simplify it or try again."),
    "worker_unavailable": (
        503, "The modelling service is briefly unavailable — try again in a moment."
    ),
    "worker_font_unavailable": (
        503, "The modelling service cannot create text right now — try again in a moment."
    ),
}


def _raise_operational(request: Request, code: str, *, service: str = "worker") -> None:
    """Set the op_error contract, then raise the coded 5xx. The access-log
    chokepoint reads `request.state.op_error` to label the crash record with the
    originating `service` and `code` (W2) — without it a worker outage would be
    mislabelled an app error (the middleware sees only an HTTP status)."""
    metrics.incr(f"exec_{code}")
    request.state.op_error = {"service": service, "code": code}
    status, message = _EXEC_OPERATIONAL[code]
    raise _coded_error(status, code, message)


def _operational_exec_code(res) -> str | None:
    """Return a user-facing operational code for an execution result, if any."""
    if res is None or res.success:
        return None
    if "fontname" in (res.error or "").lower():
        return "worker_font_unavailable"
    return res.code if res.code in _EXEC_OPERATIONAL else None


def _raise_if_operational(res, request: Request) -> None:
    """Raise a coded error for an operational exec failure; no-op otherwise.

    Called right after `execute()` on user-facing paths so a worker timeout or
    transport failure degrades to a localized notice rather than looping repairs
    (each of which costs an LLM call) or landing as a generic red error."""
    code = _operational_exec_code(res)
    if code:
        _raise_operational(request, code)


_alert_lock = threading.Lock()
_alert_day = ""


def _budget_alert() -> None:
    """Warn once per (UTC) day when the daily budget kill-switch trips: a log
    line (alertable via the existing health monitor) + an optional ops email."""
    global _alert_day
    day = time.strftime("%Y-%m-%d", time.gmtime())
    with _alert_lock:
        if _alert_day == day:
            return
        _alert_day = day
    log.warning("Daily trial budget (%s) exhausted — trials paused until UTC midnight.", TRIAL_DAILY_BUDGET)
    if ADMIN_EMAIL:
        try:
            send_mail(
                ADMIN_EMAIL,
                "text2part: daily trial budget exhausted",
                f"The operator-key trial budget ({TRIAL_DAILY_BUDGET}/day) is spent; "
                "trials are paused until UTC midnight. Users are asked to add their own key.",
            )
        except Exception:  # noqa: BLE001 — alerting must never break the request
            log.warning("Budget-alert email failed")


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
         forced to deepseek/deepseek-v4-flash (any request-supplied provider/model is
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
        # The daily budget is charged per operator-key LLM call at the call sites
        # (via _charge_operator_call), not per turn — so variations' N generates
        # can't overshoot the cap. api_key stays None → make_client falls back to
        # the operator env key.
        return TRIAL_PROVIDER, TRIAL_MODEL, None, trial.ident

    if trial.tier == "user":
        metrics.incr("exhausted_user")
        raise _coded_error(
            402, "trial_exhausted_user",
            f"You've used your {TRIAL_USER} free generations — add your LLM key to continue.",
        )
    metrics.incr("exhausted_anon")
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


def _gen_guard(session: Session, request: Request | None = None) -> None:
    limit = int(os.getenv("EASYCAD_GEN_RATE_LIMIT", "30"))
    if not limiter.allow(f"gen:{session.id}", limit, 60):
        raise HTTPException(429, "Rate limit exceeded — slow down a moment.")
    # LLM endpoints also cap per client IP, so rotating the session cookie can't
    # bypass the per-session limit and burn the operator key (SPEC14 hardening).
    if request is not None:
        ip = _client_ip(request)
        if not limiter.allow(f"genip:{ip}", GEN_RATE_LIMIT_IP, 60):
            raise HTTPException(429, "Rate limit exceeded — slow down a moment.")


def _trial_budget_reserve() -> bool:
    """Atomically reserve one unit of today's operator-key budget, or return False
    (without reserving) if the day is already spent. check + increment happen in a
    SINGLE critical section, so concurrent trials cannot overshoot the cap — the
    budget is a strict kill-switch, not a soft, race-able counter."""
    if TRIAL_DAILY_BUDGET <= 0:
        return True
    day = time.strftime("%Y-%m-%d", time.gmtime())
    with _budget_lock:
        if _budget_state["day"] != day:
            _budget_state.update(day=day, used=0)
        if _budget_state["used"] >= TRIAL_DAILY_BUDGET:
            return False
        _budget_state["used"] += 1
        return True


def _charge_trial(trial_ident: "TrialIdent") -> None:
    """Charge a successful trial generation to its per-identity grant (the
    lifetime free-N counter). The operator daily budget is charged separately, per
    LLM call, via `_charge_operator_call`."""
    trial_ident.count()


def _budget_exhausted_error() -> HTTPException:
    metrics.incr("budget_exhausted")
    _budget_alert()
    return _coded_error(
        402, "trial_budget_exhausted",
        "Free generations are paused right now — add your LLM key to keep building.",
    )


def _charge_operator_call(trial_ident: "TrialIdent | None") -> bool:
    """Reserve budget for ONE operator-key LLM call (a triage or a generate).

    Charged per call — not per turn — so variations' up-to-4 generates plus a
    triage cannot ride a single reserved unit past the daily cap. Returns True
    when allowed (BYOK, or under the cap) and False when the cap is spent, so the
    caller can raise (turn start) or stop (mid-batch). Also feeds `trial_spend`."""
    if trial_ident is None:
        return True  # BYOK — their key, their cost, no operator budget
    if _trial_budget_reserve():
        metrics.incr("trial_spend")
        return True
    return False


def gen_slot(request: Request):
    """Cap concurrent LLM/worker generation requests globally (SPEC14 hardening).

    Non-blocking: over capacity → 503 instead of piling work onto one instance.
    Used as a dependency on the LLM endpoints; the slot is held for the request.
    """
    if _gen_semaphore is None:
        yield
        return
    if not _gen_semaphore.acquire(blocking=False):
        # server_busy is an app-origin operational 5xx → label the crash record.
        request.state.op_error = {"service": "app", "code": "server_busy"}
        raise _coded_error(
            503, "server_busy",
            "We're under heavy load — try again in a few seconds.",
        )
    try:
        yield
    finally:
        _gen_semaphore.release()


# ── Request models ────────────────────────────────────────────────────────────


class ChatRequest(BaseModel):
    prompt: str = Field(max_length=MAX_PROMPT)
    current_code: str | None = Field(default=None, max_length=MAX_CODE)
    provider: str = Field(default=DEFAULT_PROVIDER, max_length=MAX_NAME)
    model: str | None = Field(default=None, max_length=MAX_NAME)
    auto_refine: bool = True
    refined_prompt: str | None = Field(default=None, max_length=MAX_PROMPT)
    response_language: Literal["en", "ru"] = "en"


class RefineRequest(BaseModel):
    prompt: str = Field(max_length=MAX_PROMPT)
    current_code: str | None = Field(default=None, max_length=MAX_CODE)
    provider: str = Field(default=DEFAULT_PROVIDER, max_length=MAX_NAME)
    model: str | None = Field(default=None, max_length=MAX_NAME)
    response_language: Literal["en", "ru"] = "en"


class VariationsRequest(BaseModel):
    prompt: str = Field(max_length=MAX_PROMPT)
    current_code: str | None = Field(default=None, max_length=MAX_CODE)
    provider: str = Field(default=DEFAULT_PROVIDER, max_length=MAX_NAME)
    model: str | None = Field(default=None, max_length=MAX_NAME)
    auto_refine: bool = True
    count: int = Field(default=3, ge=1, le=4)
    response_language: Literal["en", "ru"] = "en"


class CommitRequest(BaseModel):
    code: str = Field(max_length=MAX_CODE)
    original_prompt: str | None = Field(default=None, max_length=MAX_PROMPT)
    refined_prompt: str | None = Field(default=None, max_length=MAX_PROMPT)


class ExecuteRequest(BaseModel):
    code: str = Field(max_length=MAX_CODE)


class LoginRequest(BaseModel):
    email: str = Field(max_length=MAX_EMAIL)


class CreateTokenRequest(BaseModel):
    name: str = Field(max_length=MAX_NAME)


class TokenExchangeRequest(BaseModel):
    token: str = Field(max_length=MAX_NAME)


class SettingsRequest(BaseModel):
    provider: str | None = Field(default=None, max_length=MAX_NAME)
    model: str | None = Field(default=None, max_length=MAX_NAME)
    key: str | None = Field(default=None, max_length=MAX_NAME)


class ValidateKeyRequest(BaseModel):
    provider: str = Field(max_length=MAX_NAME)
    key: str = Field(max_length=MAX_NAME)


class FeedbackRequest(BaseModel):
    message: str = Field(min_length=1, max_length=MAX_FEEDBACK)
    rating: int | None = Field(default=None, ge=1, le=5)
    email: str | None = Field(default=None, max_length=MAX_EMAIL)


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
        code=INITIAL_CODE,
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
    # Lazy STL restore also runs the worker, but from read paths (get step, export,
    # session payload after import). Honour the concurrency cap so it can't be a
    # bypass, but best-effort: if the worker is at capacity, skip rather than 503 a
    # read — the client just gets the step without STL and can retry later.
    if _gen_semaphore is not None and not _gen_semaphore.acquire(blocking=False):
        return
    try:
        res = execute(step.code)
    finally:
        if _gen_semaphore is not None:
            _gen_semaphore.release()
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


def _is_initial_model(store, code: str) -> bool:
    current = store.current()
    return (
        current is not None
        and current.kind == "initial"
        and strip_geometry_block(code) == strip_geometry_block(current.code)
    )


def _with_geometry(store, base_code: str) -> str:
    """Give the LLM its last measured geometry without leaking it to clients."""
    current = store.current()
    info = current.geometry_info if current else None
    return append_geometry_block(base_code, info) if info else base_code


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
            "Your text2part sign-in link",
            f"Click to sign in to text2part:\n{link}\n\nThis link expires in 15 minutes.",
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
    resp = RedirectResponse(url="/", status_code=302)
    issue_auth_cookie(resp, payload["user_id"], payload["email"], COOKIE_MAX_AGE)
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


# ── Personal Access Tokens (SPEC22) ───────────────────────────────────────────

PAT_PREFIX = "pat_"
# `pat_<base64url(32 random bytes)>` (SPEC22 §2.2). token_urlsafe(32) is 43
# unpadded urlsafe-base64 chars, so the exact shape is prefix + 43 chars. Matching
# it rejects obviously-wrong forms (`pat_x`, bad length/alphabet) before a DB hit.
PAT_RE = re.compile(r"^pat_[A-Za-z0-9_-]{43}$")


def _new_pat() -> tuple[str, str]:
    """Return (raw_token, sha256_hash). `pat_<base64url(32 random bytes)>` — the
    prefix is greppable/leak-scannable and lets us reject wrong shapes before a DB
    hit; the 32 bytes are the secret. Only the hash is ever stored."""
    raw = PAT_PREFIX + secrets.token_urlsafe(32)
    return raw, _hash_pat(raw)


def _hash_pat(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


@app.post("/api/tokens")
def create_token(req: CreateTokenRequest, session: Session = Depends(current_session)) -> dict:
    if not session.user_id:
        raise HTTPException(401, "Not signed in.")
    # Per-user rate limit only (SPEC22 §2.3). No per-IP cap here — that would
    # reject distinct users behind one NAT; per-IP is only for the exchange (§2.4).
    if not limiter.allow(f"pat_create:{session.user_id}", 10, 3600):
        raise HTTPException(429, "Too many token requests. Try again later.")
    name = req.name.strip() or "token"
    raw, token_hash = _new_pat()
    try:
        rec = db.create_token_if_under_limit(session.user_id, name, token_hash)
    except db.TokenLimitError:
        raise HTTPException(
            429, f"You already have {db.MAX_ACTIVE_TOKENS} active tokens — revoke one first."
        )
    # The raw secret is returned exactly once and never retrievable again.
    return {"id": rec["id"], "name": rec["name"], "token": raw, "created_at": rec["created_at"]}


@app.get("/api/tokens")
def get_tokens(session: Session = Depends(current_session)) -> list[dict]:
    if not session.user_id:
        raise HTTPException(401, "Not signed in.")
    return db.list_tokens(session.user_id)


@app.delete("/api/tokens/{token_id}")
def delete_token(token_id: int, session: Session = Depends(current_session)) -> dict:
    if not session.user_id:
        raise HTTPException(401, "Not signed in.")
    # revoke_token scopes by user_id, so a foreign/missing/already-revoked row is
    # a safe no-op — an agent can't revoke another user's token by guessing ids.
    db.revoke_token(token_id, session.user_id)
    return {"ok": True}


@app.post("/api/auth/token")
def auth_token(req: TokenExchangeRequest, request: Request) -> Response:
    """Exchange a PAT for the standard short-lived `auth` session cookie (SPEC22).
    This is the ONLY PAT-aware auth path; every downstream route resolves identity
    from the resulting cookie unchanged."""
    ip = _client_ip(request)
    if not limiter.allow(f"pat_exchange_ip:{ip}", 20, 3600):
        raise HTTPException(429, "Too many attempts. Try again later.")
    # Exact-shape pre-filter on the RAW value before any DB hit (SPEC22 §2.2). No
    # strip(): the exchange is machine-to-machine (the SPA posts the stored token
    # verbatim), so whitespace is not valid input — a padded token is rejected.
    raw = req.token
    if not PAT_RE.match(raw):
        raise HTTPException(401, "Invalid token.")
    rec = db.get_active_token_by_hash(_hash_pat(raw))
    if not rec:
        raise HTTPException(401, "Invalid token.")
    user = db.get_user(rec["user_id"])
    if not user:
        raise HTTPException(401, "Invalid token.")
    resp = JSONResponse({"ok": True, "email": user["email"]})
    issue_auth_cookie(resp, user["id"], user["email"], PAT_COOKIE_MAX_AGE, pat=True)
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


# ── Ops / observability ───────────────────────────────────────────────────────


_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _looks_like_email(s: str) -> bool:
    """Cheap sanity check for an optional contact address (the frontend input is
    not inside a <form>, so native validation may not run before submit)."""
    return bool(_EMAIL_RE.match(s.strip()))


def _notify_feedback(fid: int, email: str | None, rating: int | None, message: str, ip: str) -> None:
    """Best-effort operator email; runs as a background task so a slow SMTP
    round-trip never delays the user's confirmation. Only ever called when
    ADMIN_EMAIL is configured — never falls back to another mailbox."""
    try:
        who = email or f"anonymous ({ip})"
        stars = f" · {rating}★" if rating else ""
        send_mail(
            ADMIN_EMAIL,
            f"[text2part] feedback #{fid}{stars} from {who}",
            f"{message}\n\n— from {who}",
        )
    except Exception:  # noqa: BLE001
        log.warning("feedback notify email failed")


@app.post("/api/feedback")
def submit_feedback(
    req: FeedbackRequest,
    request: Request,
    background_tasks: BackgroundTasks,
    session: Session = Depends(current_session),
) -> dict:
    """Accept in-app feedback from any visitor (anonymous or signed-in). Stored in
    SQLite; the operator email is sent in the background. Rate-limited by IP."""
    ip = _client_ip(request)
    if not limiter.allow(f"feedback:{ip}", 5, 3600):
        raise HTTPException(429, "Too many messages. Please try again later.")
    message = req.message.strip()
    if not message:
        raise HTTPException(400, "Message is empty.")

    # Prefer the signed-in user's verified email; else the optional one they typed
    # (kept only if it looks like an email — invalid values are discarded).
    email: str | None = None
    if session.user_id:
        user = db.get_user(session.user_id)
        email = user["email"] if user else None
    if not email and req.email and _looks_like_email(req.email):
        email = req.email.strip().lower()[:MAX_EMAIL]

    fid = db.add_feedback(message, email, req.rating)
    metrics.incr("feedback")
    # Notify only when an operator identity is configured — never send to a
    # fallback mailbox in a deployment that intentionally leaves ADMIN_EMAIL unset.
    if ADMIN_EMAIL:
        background_tasks.add_task(_notify_feedback, fid, email, req.rating, message, ip)
    return {"ok": True}


# Numeric worker metrics surfaced on the admin dashboard. Only these keys are
# forwarded (and only if numeric), so a malformed or compromised worker response
# cannot smuggle arbitrary keys/markup into the same-origin admin DOM, which
# renders these values via innerHTML.
_WORKER_STAT_FIELDS = (
    "jobs_total", "crashes_total", "timeouts_total",
    "fork_exec_p50_ms", "fork_exec_p95_ms",
    "request_wait_p50_ms", "request_wait_p95_ms",
    "rss_mb", "import_seconds",
)


def _sanitize_worker_statz(data: dict) -> dict:
    """Whitelist + type-coerce the worker's /statz payload for the admin page.
    Drops unknown keys and non-numeric metric values so nothing untrusted reaches
    the admin DOM as markup."""
    out: dict = {"reachable": True}
    mode = data.get("mode")
    if isinstance(mode, str):
        out["mode"] = mode[:32]  # rendered via textContent, but bound it anyway
    for key in _WORKER_STAT_FIELDS:
        val = data.get(key)
        if isinstance(val, (int, float)) and not isinstance(val, bool):
            out[key] = val
    return out


def _worker_statz() -> dict:
    """Best-effort proxy of the worker's /statz (SPEC18) for the admin dashboard.
    Never raises: an unreachable worker returns {reachable: False} so the page
    degrades to a status line instead of 500-ing."""
    base = os.getenv("EASYCAD_WORKER_URL")
    if not base:
        return {"reachable": False, "reason": "local execution (no worker)"}
    try:
        with urllib.request.urlopen(f"{base.rstrip('/')}/statz", timeout=2) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        # A well-behaved worker returns a JSON object; anything else (list, string,
        # number) is a malformed response — degrade instead of raising a TypeError
        # on attribute access below (which would 500 the whole admin endpoint).
        if not isinstance(data, dict):
            return {"reachable": False, "reason": "malformed worker /statz response"}
        return _sanitize_worker_statz(data)
    except (urllib.error.URLError, TimeoutError, ValueError, OSError) as exc:
        return {"reachable": False, "reason": str(exc)}


@app.get("/api/admin/stats")
def admin_stats(session: Session = Depends(require_admin)) -> dict:
    """Operator snapshot: counters, avg chat-generation latency, today's operator-
    key budget, live session count, recent feedback, and worker health. Requires
    being signed in as `ADMIN_EMAIL` (hidden with 404 otherwise)."""
    counts = metrics.snapshot()
    # Scoped, honestly named: this is the /api/chat generation-turn latency only —
    # NOT triage, variations, manual execute, or lazy STL restore.
    avg_ms = round(counts["gen_ms_total"] / counts["gen_ms_count"]) if counts.get("gen_ms_count") else None
    with _budget_lock:
        budget = {"day": _budget_state["day"], "used": _budget_state["used"], "limit": TRIAL_DAILY_BUDGET}
    return {
        "version": VERSION,
        "counters": counts,
        "avg_chat_gen_ms": avg_ms,
        "budget_today": budget,
        "sessions_live": registry.count(),
        "signups": db.count_users(),
        "feedback": {"count": db.count_feedback(), "recent": db.list_feedback(30)},
        "worker": _worker_statz(),
    }


@app.get("/admin")
def admin_page(session: Session = Depends(require_admin)) -> HTMLResponse:
    """Read-only operator dashboard (usage + worker health + feedback). Gated by
    `require_admin` (signed in as ADMIN_EMAIL); hidden with 404 otherwise.

    The page markup lives in app/templates/admin.html and is read per request so
    an edit is picked up without a restart (same convention as the landing page).
    """
    html = (TEMPLATES_DIR / "admin.html").read_text(encoding="utf-8")
    return HTMLResponse(html, headers={"Cache-Control": "no-store"})


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
def api_execute(
    req: ExecuteRequest, request: Request,
    session: Session = Depends(current_session), _slot: None = Depends(gen_slot),
) -> dict:
    _gen_guard(session)
    res = execute(req.code)
    _raise_if_operational(res, request)
    return {
        "success": res.success,
        "stl_base64": res.stl_base64,
        "geometry_info": res.geometry_info,
        "code": strip_geometry_block(req.code),
        "error": res.error,
    }


@app.post("/api/execute-manual")
def api_execute_manual(
    req: ExecuteRequest, request: Request,
    session: Session = Depends(locked_session), _slot: None = Depends(gen_slot),
) -> dict:
    _gen_guard(session)
    _check_capacity(session)
    res = execute(req.code)
    _raise_if_operational(res, request)
    step = session.store.add(
        kind="manual",
        code=req.code,
        stl_base64=res.stl_base64,
        geometry_info=res.geometry_info,
        success=res.success,
        error=res.error,
        make_current=res.success,
    )
    return {"step": step.to_public(), "session": _session_payload(session, request)}


@app.post("/api/refine")
async def api_refine(
    req: RefineRequest, request: Request,
    session: Session = Depends(current_session), _slot: None = Depends(gen_slot),
) -> dict:
    # Rate-limit like /api/chat: /api/refine also spends the operator key on a
    # trial (a triage LLM call), so without this an anonymous caller could hit it
    # unbounded — triage is uncounted by design, so the gate is the only bound.
    _gen_guard(session, request)
    _ensure_initial(session.store)
    provider, model, api_key, trial_ident = _resolve_llm(session, request, req.provider, req.model)
    # refine is one operator-key triage call on trial → charge the budget for it.
    if not _charge_operator_call(trial_ident):
        raise _budget_exhausted_error()
    try:
        base_code = _base_code(session.store, req.current_code)
        t = await _await_llm(
            request, "triage",
            triage(req.prompt, _with_geometry(session.store, base_code), provider, model, api_key,
                   response_language=req.response_language),
        )
    except LLMEmptyResponse as exc:
        raise _empty_response_error() from exc
    except LLMError as exc:
        raise _provider_error("Triage error", exc) from exc
    # `/api/refine` is STATELESS: it does not touch session.pending_skills.
    # Doing so would race /api/chat (which mutates pending under the session
    # lock) and would wrongly persist skills for clarify/invalid verdicts too.
    # The confirm flow runs through /api/chat's own triage, which stores the
    # pending skills under the lock, bound to the prompt. `skills` is returned
    # for transparency only; the client cannot set it back (no ChatRequest field).
    return {
        "verdict": t.verdict,
        "refined_prompt": t.refined_prompt,
        "questions": t.questions,
        "reason": t.reason,
        "original_prompt": req.prompt,
        "skills": t.skills,
    }


async def _generate_and_step(
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
    skills: list[str] | None = None,
    consume_pending: bool = False,
    progress=None,
) -> dict:
    _t0 = time.time()
    generate_kwargs = {"api_key": api_key, "skills": skills}
    if _is_initial_model(session.store, base_code):
        generate_kwargs["replace_initial"] = True

    # In-turn repair loop: generate → execute; on failure feed the error back and
    # let the model fix its own code, up to MAX_REPAIR extra attempts. The text-
    # mode equivalent of an agentic tool loop — the model only ever sees its own
    # code + the measured error, never a reference (bench-SPEC §2.5).
    feedback: dict | None = None
    code = None
    res = None
    charged_trial = False
    # max(1, …): always at least ONE attempt, so a non-positive MAX_REPAIR (config
    # is clamped at import, but guard the use site too) can't leave `res` None → 500.
    for attempt in range(max(1, MAX_REPAIR + 1)):
        # Each generate is one operator-key LLM call → charge the daily budget per
        # attempt (BYOK is free). Budget exhausted: raise on the first attempt,
        # otherwise stop and keep the best result produced so far.
        if not _charge_operator_call(trial_ident):
            if attempt == 0:
                raise _budget_exhausted_error()
            break
        metrics.incr("gen_attempts")
        try:
            await _emit_progress(progress, "generating")
            code = await _await_llm(
                request, "generate",
                generate_code(_with_geometry(session.store, base_code), gen_prompt, provider, model,
                              feedback=feedback, **generate_kwargs),
            )
        except LLMEmptyResponse as exc:
            metrics.incr("llm_empty_response")
            raise _empty_response_error() from exc
        except LLMError as exc:
            if attempt == 0:
                raise _provider_error("LLM error", exc) from exc
            break  # a repair attempt failed at the provider → keep best-so-far
        # The trial quota (lifetime free-N) is charged ONCE per turn, on the first
        # produced code — repairs ride the operator daily budget, not the grant.
        if trial_ident is not None and not charged_trial:
            _charge_trial(trial_ident)
            charged_trial = True
        await _emit_progress(progress, "executing")
        res = await _execute_if_connected(request, code)
        # A worker timeout / transport failure won't be fixed by a repair attempt
        # (and each repair costs another LLM call) — surface it as a notice now.
        _raise_if_operational(res, request)
        if res.success:
            break
        if attempt < MAX_REPAIR:
            metrics.incr("gen_repair")
            log.info("chat.gen repair attempt=%d error=%s", attempt + 1, (res.error or "")[:120])
            await _emit_progress(progress, "repairing")
        feedback = {"code": code, "error": res.error}

    dur_ms = int((time.time() - _t0) * 1000)
    metrics.incr("gen_ms_total", dur_ms)
    metrics.incr("gen_ms_count")
    ok = bool(res and res.success)
    metrics.incr("gen_ok" if ok else "gen_exec_fail")
    log.info(
        "chat.gen provider=%s model=%s trial=%s exec_%s dur_ms=%d",
        provider, model, trial_ident is not None, "ok" if ok else "fail", dur_ms,
    )
    if not ok:
        log.warning("chat.gen exec error: %s", ((res.error if res else "no result") or "")[:300])
    # Consume the pending refinement only on a FULLY successful confirm turn.
    # Provider/budget errors raise before here and an exec failure leaves it set,
    # so the user can retry the same confirmation and still get the recipe.
    if consume_pending and res.success:
        session.pending_skills = None
    step = session.store.add(
        kind="chat",
        original_prompt=original_prompt,
        refined_prompt=refined_prompt,
        code=code,
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


async def _chat_response(
    req: ChatRequest,
    request: Request,
    session: Session,
    progress=None,
) -> dict:
    _gen_guard(session, request)
    _check_capacity(session)
    _ensure_initial(session.store)
    base_code = _base_code(session.store, req.current_code)
    provider, model, api_key, trial_ident = _resolve_llm(session, request, req.provider, req.model)

    # The starter box is a disposable visual placeholder. The first user request
    # always defines the actual model, so bypass triage and replace it directly.
    if _is_initial_model(session.store, base_code):
        return await _generate_and_step(
            session, request, base_code, req.prompt, req.prompt, None,
            provider, model, api_key, trial_ident, progress=progress,
        )

    if not req.auto_refine:
        # No triage this turn. Skills come ONLY from the server-side pending
        # refinement stored by the triage that returned confirm_refine — never
        # from the request, and only when this turn's prompt matches the one the
        # refinement was for. So neither a client nor an unrelated auto_refine=off
        # turn can pick up someone else's recipe (SPEC15). The pending state is
        # consumed inside _generate_and_step, and only on a fully successful
        # attempt — a failed confirm leaves it so a retry still gets the recipe.
        matched = (
            session.pending_skills is not None
            and session.pending_skills[0] == req.prompt
        )
        skills = session.pending_skills[1] if matched else None
        gen_prompt = req.refined_prompt or req.prompt
        return await _generate_and_step(
            session, request, base_code, gen_prompt, req.prompt, req.refined_prompt,
            provider, model, api_key, trial_ident, skills=skills, consume_pending=matched,
            progress=progress,
        )

    # The triage call is a separate operator-key LLM call → charge it too.
    if not _charge_operator_call(trial_ident):
        raise _budget_exhausted_error()
    try:
        await _emit_progress(progress, "refining")
        t = await _await_llm(
            request, "triage",
            triage(req.prompt, _with_geometry(session.store, base_code), provider, model, api_key,
                   response_language=req.response_language),
        )
    except LLMEmptyResponse as exc:
        raise _empty_response_error() from exc
    except LLMError as exc:
        raise _provider_error("Triage error", exc) from exc

    session.pending_skills = None  # a fresh triage supersedes any stale pending
    if t.verdict == "clarify":
        return _no_step(session, request, "clarify", req.prompt, questions=t.questions)
    if t.verdict == "invalid":
        return _no_step(session, request, "invalid", req.prompt, reason=t.reason)
    if t.verdict == "refine":
        # Hold the skills server-side, bound to this prompt, for the confirm turn.
        session.pending_skills = (req.prompt, t.skills) if t.skills else None
        return _no_step(session, request, "confirm_refine", req.prompt,
                        refined_prompt=t.refined_prompt)

    return await _generate_and_step(
        session, request, base_code, req.prompt, req.prompt, None,
        provider, model, api_key, trial_ident, skills=t.skills, progress=progress,
    )


async def _chat_sse(req: ChatRequest, request: Request, session: Session):
    """Yield chat progress while keeping request cancellation tied to the LLM."""
    updates: asyncio.Queue[str] = asyncio.Queue()

    async def progress(stage: str) -> None:
        await updates.put(stage)

    task = asyncio.create_task(_chat_response(req, request, session, progress))
    yield _sse_event("progress", {"stage": "accepted"})
    try:
        while not task.done():
            next_update = asyncio.create_task(updates.get())
            try:
                done, _pending = await asyncio.wait({task, next_update}, return_when=asyncio.FIRST_COMPLETED)
                if next_update in done:
                    yield _sse_event("progress", {"stage": next_update.result()})
            finally:
                # A disconnect cancels this generator while it is awaiting the
                # queue. Always clean up that waiter as well as the chat task.
                if not next_update.done():
                    next_update.cancel()
                    await asyncio.gather(next_update, return_exceptions=True)

        while not updates.empty():
            yield _sse_event("progress", {"stage": updates.get_nowait()})

        try:
            result = await task
        except HTTPException as exc:
            detail = exc.detail if isinstance(exc.detail, dict) else {}
            yield _sse_event("error", {
                "status": exc.status_code,
                "code": detail.get("code"),
                "message": detail.get("message") or str(exc.detail),
            })
        else:
            yield _sse_event("result", result)
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


@app.post("/api/chat")
async def api_chat(
    req: ChatRequest, request: Request,
    session: Session = Depends(locked_session), _slot: None = Depends(gen_slot),
):
    # Fetch supports POST + SSE framing, unlike EventSource (GET-only). Keep the
    # JSON response for API clients that did not opt into progress updates.
    if "text/event-stream" in request.headers.get("accept", ""):
        return StreamingResponse(
            _chat_sse(req, request, session),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )
    return await _chat_response(req, request, session)


@app.post("/api/variations")
async def api_variations(
    req: VariationsRequest, request: Request,
    session: Session = Depends(current_session), _slot: None = Depends(gen_slot),
) -> dict:
    _gen_guard(session, request)
    _ensure_initial(session.store)
    base_code = _base_code(session.store, req.current_code)
    provider, model, api_key, trial_ident = _resolve_llm(session, request, req.provider, req.model)

    gen_prompt = req.prompt
    refined_prompt: str | None = None
    skills: list[str] | None = None
    if req.auto_refine and not _is_initial_model(session.store, base_code):
        if not _charge_operator_call(trial_ident):  # triage is an operator call
            raise _budget_exhausted_error()
        try:
            t = await _await_llm(
                request, "triage",
                triage(req.prompt, _with_geometry(session.store, base_code), provider, model, api_key,
                       response_language=req.response_language),
            )
        except LLMEmptyResponse as exc:
            raise _empty_response_error() from exc
        except LLMError as exc:
            raise _provider_error("Triage error", exc) from exc
        skills = t.skills
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
        # Charge each candidate as its own operator-key call; if the daily budget
        # runs out mid-batch, stop and return the candidates already produced.
        if not _charge_operator_call(trial_ident):
            break
        metrics.incr("gen_attempts")
        try:
            generate_kwargs = {"temperature": 0.7, "api_key": api_key, "skills": skills}
            if _is_initial_model(session.store, base_code):
                generate_kwargs["replace_initial"] = True
            code = await _await_llm(
                request, "generate",
                generate_code(_with_geometry(session.store, base_code), gen_prompt, provider, model,
                              **generate_kwargs),
            )
        except LLMEmptyResponse as exc:
            metrics.incr("llm_empty_response")
            raise _empty_response_error() from exc
        except LLMError as exc:
            metrics.incr("provider_errors")  # so variations feed the failure rate
            candidates.append(
                {"code": None, "stl_base64": None, "geometry_info": None, "success": False, "error": str(exc)}
            )
            continue
        res = await _execute_if_connected(request, code)
        operational_code = _operational_exec_code(res)
        if operational_code:
            # An operational failure (worker timeout/outage) hits the whole batch,
            # not just this candidate. With nothing usable yet, surface the localized
            # W1 notice exactly like /api/chat; with partial results already in hand,
            # stop the batch and return them rather than discarding good candidates.
            if not any(c["success"] for c in candidates):
                _raise_if_operational(res, request)
            metrics.incr(f"exec_{operational_code}")
            break
        metrics.incr("gen_ok" if res.success else "gen_exec_fail")
        candidates.append({
            "code": strip_geometry_block(code),
            "stl_base64": res.stl_base64,
            "geometry_info": res.geometry_info,
            "success": res.success,
            "error": res.error,
        })

    # Zero candidates can only happen when the budget ran out before the first
    # generate (trial only — BYOK always makes ≥1). Surface the notice instead of
    # a silent empty 200; a partial batch (≥1 candidate) still returns normally.
    if not candidates:
        raise _budget_exhausted_error()

    # One variations turn = one trial unit (like /api/chat), charged once if any
    # candidate's code was actually generated. Prevents unlimited free use of the
    # operator key via the ×N button before the first chat exhausts the grant.
    if trial_ident is not None and any(c["code"] for c in candidates):
        _charge_trial(trial_ident)

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
def api_commit(
    req: CommitRequest, request: Request,
    session: Session = Depends(locked_session), _slot: None = Depends(gen_slot),
) -> dict:
    _gen_guard(session)
    _check_capacity(session)
    res = execute(req.code)
    _raise_if_operational(res, request)
    step = session.store.add(
        kind="chat",
        original_prompt=req.original_prompt,
        refined_prompt=req.refined_prompt,
        code=req.code,
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
        headers={"Content-Disposition": 'attachment; filename="text2part-project.json"'},
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


@app.get("/api/export/{step_id}/source")
def export_step_source(step_id: int, session: Session = Depends(current_session)) -> Response:
    """Download the step's CadQuery script (.py) — just the stored code, no worker."""
    step = session.store.get(step_id)
    if step is None or not step.code:
        raise HTTPException(404, f"No source available for step {step_id}")
    return Response(
        content=step.code,
        media_type="text/x-python; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="model_step_{step_id}.py"'},
    )


@app.get("/api/export/{step_id}/step")
def export_step_step(
    step_id: int, request: Request,
    session: Session = Depends(current_session), _slot: None = Depends(gen_slot),
) -> Response:
    """Download the step as a STEP file — re-runs the stored code in the worker to
    export CAD-native geometry (no LLM; rate-limited + concurrency-capped)."""
    _gen_guard(session)
    step = session.store.get(step_id)
    if step is None or not step.success or not step.code:
        raise HTTPException(404, f"No model available for step {step_id}")
    result = export_model(step.code, "step")
    # Symmetric with execute(): a worker outage/timeout is a coded operational 5xx
    # (reaches the crash chokepoint as service=worker), distinct from a legit
    # no-data result (→ a plain 502 "could not export").
    if result.code in _EXEC_OPERATIONAL:
        _raise_operational(request, result.code)
    data = result.data
    if not data:
        raise HTTPException(502, "Could not export STEP for this model.")
    # Content hash so a client (e.g. the bench harness) can verify the download
    # is complete and unaltered end-to-end — a proxy truncation or stale body is
    # otherwise measured as if it were the current model.
    import hashlib
    digest = hashlib.sha256(data).hexdigest()
    return Response(
        content=data,
        media_type="application/step",
        headers={
            "Content-Disposition": f'attachment; filename="model_step_{step_id}.step"',
            "X-Content-SHA256": digest,
        },
    )


# The landing page at "/" is light static content and may be indexed; the app
# (/app) and API are heavy/interactive and are kept off-limits to crawlers.
_SITE_URL = os.getenv("APP_URL", "https://text2part.bconf.com").rstrip("/")

# ── Web analytics (Yandex.Metrica) ──────────────────────────────────────────
# The counter is injected into both HTML surfaces (landing + SPA shell) only
# when a numeric counter id is configured, so dev/self-hosting stay tracker-free
# by default. The snippet enables Webvisor + click/link maps — the point of the
# ProductHunt launch is watching where real visitors get stuck. It also exposes
# `window.__ymId`, which the SPA's analytics module reads to send funnel goals.
#
# The id is interpolated raw into a <script>, so it must be a plain counter
# number — anything else is a config error (and, if the env were tampered with,
# an HTML/JS injection vector). Reject non-numeric values: no tag, loud log.
_raw_metrica_id = os.getenv("YANDEX_METRICA_ID", "").strip()
# ASCII digits only: `isdecimal()` alone accepts non-ASCII digits (e.g. Arabic
# ١٢٣), which are injection-safe but form a broken `window.__ymId = …` literal.
if _raw_metrica_id and not (_raw_metrica_id.isascii() and _raw_metrica_id.isdecimal()):
    log.warning("YANDEX_METRICA_ID=%r is not a numeric counter id — analytics disabled.", _raw_metrica_id)
    _raw_metrica_id = ""
YANDEX_METRICA_ID = _raw_metrica_id


def _metrica_snippet(counter_id: str) -> str:
    # The vendor-generated snippet with the id threaded from YANDEX_METRICA_ID.
    # `window.__ymId` is our addition — the SPA analytics module reads it to send
    # funnel goals (reachGoal); the rest is Metrica's standard init.
    return (
        "<!-- Yandex.Metrika counter -->\n"
        '<script type="text/javascript">\n'
        "   (function(m,e,t,r,i,k,a){\n"
        "       m[i]=m[i]||function(){(m[i].a=m[i].a||[]).push(arguments)};\n"
        "       m[i].l=1*new Date();\n"
        "       for (var j = 0; j < document.scripts.length; j++) {if (document.scripts[j].src === r) { return; }}\n"
        "       k=e.createElement(t),a=e.getElementsByTagName(t)[0],k.async=1,k.src=r,a.parentNode.insertBefore(k,a)\n"
        f"   }})(window, document,'script','https://mc.yandex.ru/metrika/tag.js?id={counter_id}', 'ym');\n"
        "\n"
        f"   ym({counter_id}, 'init', {{ssr:true, webvisor:true, clickmap:true, ecommerce:\"dataLayer\", referrer: document.referrer, url: location.href, accurateTrackBounce:true, trackLinks:true}});\n"
        f"   window.__ymId = {counter_id};\n"
        "</script>\n"
        f'<noscript><div><img src="https://mc.yandex.ru/watch/{counter_id}" style="position:absolute; left:-9999px;" alt="" /></div></noscript>\n'
        "<!-- /Yandex.Metrika counter -->"
    )


# Both HTML sources carry the `<!--@METRICA@-->` placeholder in <head>; swap it
# for the counter (or strip it when unconfigured).
def _inject_metrica(html: str) -> str:
    snippet = _metrica_snippet(YANDEX_METRICA_ID) if YANDEX_METRICA_ID else ""
    return html.replace("<!--@METRICA@-->", snippet)


# Reads per request so a `make build` while the server is running is picked up
# without a restart.
def _serve_html(path: Path, cache_control: str) -> HTMLResponse:
    html = _inject_metrica(path.read_text(encoding="utf-8"))
    return HTMLResponse(html, headers={"Cache-Control": cache_control})


# The legal pages (terms/privacy) share their CSS and language-toggle JS. Rather
# than duplicate ~90 lines across both files, each page carries `<!--@LEGAL_CSS@-->`
# / `<!--@LEGAL_JS@-->` placeholders and only its own copy + RU strings; the shared
# assets are inlined here from single source files (read per request, same as
# the landing page, so an edit is picked up without a restart).
def _serve_legal(path: Path) -> HTMLResponse:
    css_path = STATIC_DIR / "legal.css"
    js_path = STATIC_DIR / "legal.js"
    # The page needs the shared assets too — a partial/stale build where the HTML
    # exists but legal.{css,js} do not must 404 cleanly, not raise FileNotFoundError
    # (→ 500) mid-request.
    if not (path.exists() and css_path.exists() and js_path.exists()):
        raise HTTPException(404)
    html = path.read_text(encoding="utf-8")
    html = html.replace("<!--@LEGAL_CSS@-->", f"<style>\n{css_path.read_text(encoding='utf-8')}</style>")
    html = html.replace("<!--@LEGAL_JS@-->", f"<script>\n{js_path.read_text(encoding='utf-8')}</script>")
    return HTMLResponse(_inject_metrica(html), headers={"Cache-Control": "public, max-age=3600"})


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
    def landing() -> HTMLResponse:
        if _LANDING.exists():
            return _serve_html(_LANDING, "public, max-age=300")
        return _serve_html(_INDEX, "no-cache")

    # Minimal legal pages (W4). Same static serving as the landing; bilingual
    # (EN+RU) with an in-page toggle. 404 if the build hasn't produced them.
    @app.get("/terms")
    def terms() -> HTMLResponse:
        return _serve_legal(STATIC_DIR / "terms.html")

    @app.get("/privacy")
    def privacy() -> HTMLResponse:
        return _serve_legal(STATIC_DIR / "privacy.html")

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
    def spa(_path: str = "") -> HTMLResponse:
        return _serve_html(_INDEX, "no-cache")
