"""Isolated CadQuery execution worker (SPEC12).

A tiny FastAPI service that runs untrusted, LLM-generated CadQuery code inside a
hardened container. It holds no LLM key and no user data, and — in the compose
deployment — has no network egress. It is invoked by the app container over the
private network via `POST /execute`.

Per-request isolation (see `limits.run`): AST guard → fresh resource-limited
child process → tmpfs scratch wiped after. A concurrency semaphore keeps one
heavy request from starving the shared worker.
"""

import asyncio
import os

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

import code_guard
import limits

app = FastAPI(title="EasyCAD CadQuery Worker")

_CONCURRENCY = int(os.getenv("EASYCAD_WORKER_CONCURRENCY", "2"))
_sem = asyncio.Semaphore(_CONCURRENCY)

# Reject oversized bodies before parsing/execution (review C1).
MAX_BODY_BYTES = int(os.getenv("EASYCAD_WORKER_MAX_BODY_BYTES", str(500_000)))
MAX_CODE = 200_000


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


@app.get("/healthz")
def healthz() -> dict:
    return {"ok": True}


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
    async with _sem:
        # limits.run blocks (subprocess); keep the event loop free.
        return await asyncio.to_thread(limits.run, req.code)
