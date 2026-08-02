"""SPEC21 W1 — request-context logging.

Covers the context filter (defaults outside a request, real values inside), the
ContextVar reset in the app middleware (no leak across reused tasks), the
dictConfig routing uvicorn's loggers through the filtered handler, the trace-id
middleware + X-Trace-Id header, the user email-vs-anon resolution, and the
worker seeding its ContextVar from the inbound X-Trace-Id on both endpoints.
"""

import logging
import sys
from pathlib import Path

from fastapi.testclient import TestClient

import app.main as m
from app import jwt_utils, log_context
from app.main import app


# ── Filter: defaults outside a request, real values inside ────────────────────

def test_filter_stamps_defaults_outside_request():
    rec = logging.LogRecord("x", logging.INFO, __file__, 1, "hi", None, None)
    log_context.ContextFilter().filter(rec)
    assert rec.trace_id == "-" and rec.user == "-" and rec.version == "-"


def test_filter_stamps_context_values_inside_request():
    token = log_context.set_context(trace_id="abc123", user="a@b.co", version="v9")
    try:
        rec = logging.LogRecord("x", logging.INFO, __file__, 1, "hi", None, None)
        log_context.ContextFilter().filter(rec)
        assert rec.trace_id == "abc123" and rec.user == "a@b.co" and rec.version == "v9"
    finally:
        log_context.reset_context(token)


def test_filter_does_not_overwrite_explicit_extra():
    # The exception handler runs after the ContextVar is reset and stamps trace_id
    # via extra=; the filter must not clobber it with the default.
    rec = logging.LogRecord("x", logging.INFO, __file__, 1, "hi", None, None)
    rec.trace_id = "explicit"
    log_context.ContextFilter().filter(rec)
    assert rec.trace_id == "explicit"


def test_context_reset_prevents_leak():
    token = log_context.set_context(trace_id="req1")
    log_context.reset_context(token)
    # After reset we are back to defaults — a reused task can't inherit req1.
    assert log_context.current().get("trace_id") == "-"


# ── dictConfig routes uvicorn loggers through the filtered handler ─────────────

def test_configure_logging_owns_uvicorn_loggers():
    log_context.configure_logging("INFO")
    for name in ("uvicorn", "uvicorn.access", "uvicorn.error"):
        lg = logging.getLogger(name)
        assert lg.handlers, f"{name} has no handler"
        assert lg.propagate is False
        handler = lg.handlers[0]
        assert any(isinstance(f, log_context.ContextFilter) for f in handler.filters)


# ── Middleware: trace id + X-Trace-Id header ──────────────────────────────────

def test_response_carries_x_trace_id():
    r = TestClient(app).get("/api/settings", headers={"x-real-ip": "1.1.1.1"})
    assert r.status_code == 200
    assert len(r.headers.get("X-Trace-Id", "")) == 16  # secrets.token_hex(8)


def test_formatted_access_line_carries_context():
    # Exercise the actual FORMAT string + filter end to end (not just record attrs).
    import io
    buf = io.StringIO()
    h = logging.StreamHandler(buf)
    h.setFormatter(logging.Formatter(log_context.FORMAT))
    h.addFilter(log_context.ContextFilter())
    lg = logging.getLogger("easycad.fmt_test")
    lg.addHandler(h)
    lg.setLevel(logging.INFO)
    lg.propagate = False
    tok = log_context.set_context(trace_id="abcd1234", user="u@e.co", version="v7")
    try:
        lg.info("GET /x -> 200")
    finally:
        log_context.reset_context(tok)
        lg.removeHandler(h)
    assert "[trace=abcd1234 user=u@e.co v=v7]" in buf.getvalue()


def _capture(logger_name: str):
    """Attach a filtered, formatted capturing handler and return (handler, buffer)."""
    import io
    buf = io.StringIO()
    h = logging.StreamHandler(buf)
    h.setFormatter(logging.Formatter(log_context.FORMAT))
    h.addFilter(log_context.ContextFilter())
    lg = logging.getLogger(logger_name)
    lg.addHandler(h)
    return lg, h, buf


def test_exception_log_line_carries_user_and_session():
    # The exception handler runs after the ContextVar reset, but must still stamp
    # the caller identity (from request.state) on its traceback line (W1).
    @app.get("/_boom_ident_w1")
    def _boom_ident():
        raise RuntimeError("ident-boom")

    from app import db
    user = db.get_or_create_user("crash@user.co")
    token = jwt_utils.sign({"user_id": user["id"], "email": user["email"]}, 3600)
    lg, h, buf = _capture("easycad")
    try:
        client = TestClient(app, raise_server_exceptions=False)
        client.cookies.set(m.AUTH_COOKIE, token)
        client.get("/_boom_ident_w1", headers={"x-real-ip": "1.1.1.9"})
    finally:
        lg.removeHandler(h)
    out = buf.getvalue()
    assert "crash@user.co" in out  # user, not "-"
    assert "user=-" not in out.split("Unhandled error")[-1]


def test_500_body_and_header_echo_trace_id(monkeypatch):
    # Force an unhandled error and assert the exception handler echoes trace_id in
    # both the body and the X-Trace-Id header (the middleware never ran its header).
    @app.get("/_boom_w1")
    def _boom():
        raise RuntimeError("kaboom")

    client = TestClient(app, raise_server_exceptions=False)
    r = client.get("/_boom_w1", headers={"x-real-ip": "1.1.1.2"})
    assert r.status_code == 500
    body = r.json()
    assert body["trace_id"] and body["trace_id"] == r.headers.get("X-Trace-Id")


# ── user: email when signed in, else anon:<session-prefix> ────────────────────

def test_user_is_anon_for_anonymous(monkeypatch):
    seen = {}

    @app.get("/_whoami_w1")
    def _whoami():
        seen.update(log_context.current())
        return {"ok": True}

    TestClient(app).get("/_whoami_w1", headers={"x-real-ip": "1.1.1.3"})
    assert seen.get("user", "").startswith("anon:")


def test_user_is_email_when_signed_in():
    seen = {}

    @app.get("/_whoami2_w1")
    def _whoami2():
        seen.update(log_context.current())
        return {"ok": True}

    from app import db
    user = db.get_or_create_user("signed@in.co")
    token = jwt_utils.sign({"user_id": user["id"], "email": user["email"]}, 3600)
    client = TestClient(app)
    client.cookies.set(m.AUTH_COOKIE, token)
    client.get("/_whoami2_w1", headers={"x-real-ip": "1.1.1.4"})
    assert seen.get("user") == "signed@in.co"


# ── Worker seeds its ContextVar from the inbound X-Trace-Id (both endpoints) ───

def _worker_client():
    # Import the worker app the way the built image does (flat top-level modules).
    repo = Path(__file__).resolve().parent.parent
    for p in (str(repo / "app"), str(repo / "worker")):
        if p not in sys.path:
            sys.path.insert(0, p)
    import importlib
    wmain = importlib.import_module("main")
    return wmain, TestClient(wmain.app)


def test_worker_seeds_trace_from_header_on_execute_and_export(monkeypatch):
    wmain, client = _worker_client()  # sets up sys.path for the flat worker modules
    import log_context as wlog  # worker's top-level module (now importable)
    captured = {}

    def fake_run(code):
        captured["execute"] = wlog._ctx.get().get("trace_id")
        return {"success": True, "stl_base64": "AA==", "geometry_info": "# i"}

    def fake_export(code, fmt):
        captured["export"] = wlog._ctx.get().get("trace_id")
        return {"success": True, "data_base64": "AA=="}

    monkeypatch.setattr(wmain.limits, "run", fake_run)
    monkeypatch.setattr(wmain.limits, "export", fake_export)
    monkeypatch.setattr(wmain, "_ZYGOTE_ENABLED", False)

    client.post("/execute", json={"code": "x"}, headers={"x-trace-id": "trace-exec"})
    client.post("/export", json={"code": "x", "format": "step"}, headers={"x-trace-id": "trace-exp"})
    assert captured.get("execute") == "trace-exec"
    assert captured.get("export") == "trace-exp"

    # No leak across requests: a call WITHOUT the header resets to the default, so a
    # reused task/thread can't inherit "trace-exec".
    client.post("/execute", json={"code": "x"})
    assert captured.get("execute") == "-"
    # And after the request the middleware has reset the ContextVar entirely.
    assert wlog._ctx.get().get("trace_id") == "-"


def test_worker_access_line_is_in_context_and_traced(monkeypatch):
    # On the happy path limits.run logs nothing, so the worker's own access line is
    # the only per-request line — it must be emitted in-context and carry the trace
    # (uvicorn's post-reset duplicate is silenced).
    import io
    wmain, client = _worker_client()
    import log_context as wlog
    monkeypatch.setattr(wmain.limits, "run",
                        lambda code: {"success": True, "stl_base64": "AA==", "geometry_info": "# i"})
    monkeypatch.setattr(wmain, "_ZYGOTE_ENABLED", False)
    buf = io.StringIO()
    h = logging.StreamHandler(buf)
    h.setFormatter(logging.Formatter(wlog.FORMAT))
    h.addFilter(wlog.ContextFilter())
    lg = logging.getLogger("worker")
    lg.addHandler(h)
    lg.setLevel(logging.INFO)
    try:
        client.post("/execute", json={"code": "x"}, headers={"x-trace-id": "trace-access"})
    finally:
        lg.removeHandler(h)
    out = buf.getvalue()
    assert "trace-access" in out and "/execute -> 200" in out
