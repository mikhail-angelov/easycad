"""Isolated CadQuery execution worker (SPEC12 + SPEC18).

A tiny FastAPI service that runs untrusted, LLM-generated CadQuery code inside a
hardened container. It holds no LLM key and no user data, and — in the compose
deployment — has no network egress. It is invoked by the app container over the
private network via `POST /execute`.

Two execution paths, same wire contract and same per-request isolation:

* Fresh (default): each request spawns a fresh `python -m cq_worker` child that
  imports CadQuery — see `limits.run`.
* Zygote (opt-in, `EASYCAD_WORKER_ZYGOTE=1`, SPEC18): a single side-car process
  imports CadQuery once and forks a one-shot child per request, so the ~1.5s
  import leaves the hot path. Same one-shot-process isolation — see `zygote.py`.

Either way: AST guard → resource-limited one-shot child → tmpfs scratch wiped
after. A concurrency semaphore keeps one heavy request from starving the worker.
"""

import asyncio
import fcntl
import os
import subprocess
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

import code_guard
import limits
import zygote

_CONCURRENCY = int(os.getenv("EASYCAD_WORKER_CONCURRENCY", "2"))
_sem = asyncio.Semaphore(_CONCURRENCY)

# Request-wait metric (SPEC18): time a request spends waiting for the concurrency
# semaphore before it is admitted. Recorded on the single-threaded event loop.
_MAX_WAITS = 256
_wait_ms: list[float] = []


def _record_wait(ms: float) -> None:
    _wait_ms.append(round(ms, 1))
    if len(_wait_ms) > _MAX_WAITS:
        del _wait_ms[0]


def _wait_percentiles():
    d = sorted(_wait_ms)
    if not d:
        return None, None
    return d[len(d) // 2], d[max(0, int(len(d) * 0.95) - 1)]

# Reject oversized bodies before parsing/execution (review C1).
MAX_BODY_BYTES = int(os.getenv("EASYCAD_WORKER_MAX_BODY_BYTES", str(500_000)))
MAX_CODE = 200_000

# ── SPEC18 zygote wiring ─────────────────────────────────────────────────────
_ZYGOTE_ENABLED = os.getenv("EASYCAD_WORKER_ZYGOTE", "").lower() in ("1", "true", "yes")
_ZYGOTE_SOCK = os.getenv("EASYCAD_WORKER_ZYGOTE_SOCK", "/tmp/easycad-zygote.sock")
_ZYGOTE_WARM_TIMEOUT = int(os.getenv("EASYCAD_WORKER_ZYGOTE_WARM_TIMEOUT", "60"))
_HERE = Path(__file__).resolve().parent
_ZYGOTE_LOCK_PATH = os.getenv("EASYCAD_WORKER_ZYGOTE_LOCK", "/tmp/easycad-zygote.lock")
_zygote_proc: subprocess.Popen | None = None
_zygote_client: zygote.ZygoteClient | None = None
_zygote_lock_fd: int | None = None


def _acquire_single_instance_lock(path: str | None = None) -> None:
    """Enforce SPEC18's single-zygote-per-container invariant: exactly one worker
    process may own the zygote. A second process (e.g. `uvicorn --workers 2`)
    fails fast with a clear error instead of silently starting a second zygote
    and multiplying import memory / clobbering the socket. The lock is held for
    the process lifetime via the retained fd."""
    global _zygote_lock_fd
    fd = os.open(path or _ZYGOTE_LOCK_PATH, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        os.close(fd)
        raise RuntimeError(
            "Zygote mode requires a single worker process, but another process "
            "already holds the zygote lock. Do not run uvicorn with --workers>1 "
            "(SPEC18: one zygote per container)."
        ) from exc
    _zygote_lock_fd = fd


def _start_zygote() -> None:
    """Launch the single-threaded zygote side-car and wait until it has imported
    CadQuery (confirmed by a ping). Best-effort: on failure the endpoints still
    return a populated error rather than crash the worker."""
    global _zygote_proc, _zygote_client
    _acquire_single_instance_lock()
    env = dict(os.environ)
    for k in zygote._SINGLE_THREAD_ENV:
        env[k] = "1"
    env["OMP_DYNAMIC"] = "FALSE"
    _zygote_proc = subprocess.Popen(
        [sys.executable, "-m", "zygote", "serve", _ZYGOTE_SOCK],
        cwd=str(_HERE), env=env,
    )
    _zygote_client = zygote.ZygoteClient(_ZYGOTE_SOCK)


@asynccontextmanager
async def _lifespan(app: FastAPI):
    if _ZYGOTE_ENABLED:
        _start_zygote()
        # Wait for import to finish (ping succeeds), bounded.
        for _ in range(_ZYGOTE_WARM_TIMEOUT * 5):
            if _zygote_proc and _zygote_proc.poll() is not None:
                break  # zygote died during warmup
            if await asyncio.to_thread(_zygote_client.ping):
                break
            await asyncio.sleep(0.2)
    try:
        yield
    finally:
        global _zygote_lock_fd
        if _zygote_proc is not None:
            _zygote_proc.terminate()
            try:
                _zygote_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                _zygote_proc.kill()
        if _zygote_lock_fd is not None:
            os.close(_zygote_lock_fd)  # release the single-instance lock
            _zygote_lock_fd = None


app = FastAPI(title="EasyCAD CadQuery Worker", lifespan=_lifespan)


@app.middleware("http")
async def _body_size_limit(request: Request, call_next):
    cl = request.headers.get("content-length")
    if cl is not None:
        try:
            if int(cl) > MAX_BODY_BYTES:
                return JSONResponse({"detail": "Request body too large."}, status_code=413)
        except ValueError:
            return JSONResponse({"detail": "Invalid Content-Length."}, status_code=400)
    return await call_next(request)


class ExecRequest(BaseModel):
    code: str = Field(max_length=MAX_CODE)


class ExportRequest(BaseModel):
    code: str = Field(max_length=MAX_CODE)
    format: str = Field(max_length=8)


@app.get("/healthz")
def healthz() -> dict:
    """Liveness — the HTTP process is up. Unchanged, so Compose `depends_on`
    ordering is unaffected. Warmed-capacity is a separate signal (`/readyz`)."""
    return {"ok": True}


@app.get("/readyz")
def readyz():
    """Readiness — usable capacity exists. In fresh mode always ready; in zygote
    mode ready only once the side-car has imported CadQuery (ping succeeds)."""
    if not _ZYGOTE_ENABLED:
        return {"ready": True, "mode": "fresh"}
    ready = _zygote_client is not None and _zygote_client.ping()
    payload = {"ready": ready, "mode": "zygote"}
    return payload if ready else JSONResponse(payload, status_code=503)


@app.get("/statz")
def statz():
    """SPEC18 operator metrics: import time, job/fail/timeout counts, fork+exec
    latency percentiles, request-wait percentiles, in-flight count, resident
    memory."""
    p50, p95 = _wait_percentiles()
    wait = {"request_wait_p50_ms": p50, "request_wait_p95_ms": p95}
    if not _ZYGOTE_ENABLED:
        return {"mode": "fresh", **wait}
    if _zygote_client is None:
        return JSONResponse({"ok": False, "mode": "zygote", **wait}, status_code=503)
    stats = _zygote_client.stats()
    merged = {**stats, **wait}
    return merged if stats.get("ok") else JSONResponse(merged, status_code=503)


@app.post("/execute")
async def execute(req: ExecRequest) -> dict:
    ok, reason = code_guard.check(req.code)
    if not ok:
        return {
            "success": False,
            "stl_base64": None,
            "geometry_info": None,
            "error": f"Code rejected by guard: {reason}",
        }
    t0 = time.monotonic()
    async with _sem:
        _record_wait((time.monotonic() - t0) * 1000)
        # Both paths block (subprocess / socket round-trip); keep the loop free.
        if _ZYGOTE_ENABLED:
            return await asyncio.to_thread(_zygote_client.run, req.code)
        return await asyncio.to_thread(limits.run, req.code)


@app.post("/export")
async def export(req: ExportRequest) -> dict:
    """On-demand export of `result` to a download format (stl/step)."""
    ok, reason = code_guard.check(req.code)
    if not ok:
        return {"success": False, "data_base64": None, "error": f"Code rejected by guard: {reason}"}
    t0 = time.monotonic()
    async with _sem:
        _record_wait((time.monotonic() - t0) * 1000)
        if _ZYGOTE_ENABLED:
            return await asyncio.to_thread(_zygote_client.export, req.code, req.format)
        return await asyncio.to_thread(limits.export, req.code, req.format)
