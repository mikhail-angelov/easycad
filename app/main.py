"""SPEC11 — CadQuery Chat API.

A text-only, single-screen incremental 3D-model builder. The client sends
short modification prompts; the server generates CadQuery code (LLM), executes
it in an isolated worker, and returns the STL + geometry info as a new step.
"""

import base64
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .cadquery_exec import execute
from .llm import DEFAULT_PROVIDER, INITIAL_CODE, LLMError, PROVIDERS, generate_code
from .store import SessionStore

ROOT = Path(__file__).resolve().parent.parent
STATIC_DIR = ROOT / "static"

load_dotenv(ROOT / ".env")

app = FastAPI(title="EasyCAD — CadQuery Chat")
store = SessionStore()


# ── Request models ────────────────────────────────────────────────────────────


class ChatRequest(BaseModel):
    prompt: str
    current_code: str | None = None
    provider: str = DEFAULT_PROVIDER
    model: str | None = None


class ExecuteRequest(BaseModel):
    code: str


# ── Helpers ───────────────────────────────────────────────────────────────────


def _ensure_initial() -> None:
    """Lazily create step 0 (the starting box) on first access."""
    if store.current() is not None:
        return
    res = execute(INITIAL_CODE)
    store.add(
        kind="initial",
        code=res.code_with_geometry or INITIAL_CODE,
        stl_base64=res.stl_base64,
        geometry_info=res.geometry_info,
        success=res.success,
        error=res.error,
    )


def _session_payload() -> dict:
    current = store.current()
    return {
        "current_id": store.current_id,
        "current": current.to_public() if current else None,
        "steps": [s.to_public(include_stl=False) for s in store.all()],
        "providers": {name: cfg["default_model"] for name, cfg in PROVIDERS.items()},
        "default_provider": DEFAULT_PROVIDER,
    }


# ── Endpoints ─────────────────────────────────────────────────────────────────


@app.get("/api/session")
def get_session() -> dict:
    _ensure_initial()
    return _session_payload()


@app.post("/api/session/reset")
def reset_session() -> dict:
    store.reset()
    _ensure_initial()
    return _session_payload()


@app.get("/api/steps")
def list_steps() -> list[dict]:
    _ensure_initial()
    return [s.to_public(include_stl=False) for s in store.all()]


@app.get("/api/steps/{step_id}")
def get_step(step_id: int) -> dict:
    step = store.get(step_id)
    if step is None:
        raise HTTPException(404, f"Step {step_id} not found")
    return step.to_public()


@app.post("/api/steps/{step_id}/revert")
def revert_step(step_id: int) -> dict:
    if store.revert(step_id) is None:
        raise HTTPException(404, f"Step {step_id} not found")
    return _session_payload()


@app.post("/api/execute")
def api_execute(req: ExecuteRequest) -> dict:
    """Stateless execution — run code, return STL + geometry, no step created."""
    res = execute(req.code)
    return {
        "success": res.success,
        "stl_base64": res.stl_base64,
        "geometry_info": res.geometry_info,
        "code_with_geometry": res.code_with_geometry,
        "error": res.error,
    }


@app.post("/api/execute-manual")
def api_execute_manual(req: ExecuteRequest) -> dict:
    """Execute manually edited code and record it as a new step."""
    res = execute(req.code)
    step = store.add(
        kind="manual",
        code=res.code_with_geometry or req.code,
        stl_base64=res.stl_base64,
        geometry_info=res.geometry_info,
        success=res.success,
        error=res.error,
        make_current=res.success,
    )
    return {"step": step.to_public(), "session": _session_payload()}


@app.post("/api/chat")
def api_chat(req: ChatRequest) -> dict:
    """Generate a modification, execute it, and record it as a new step."""
    _ensure_initial()
    base_code = req.current_code
    if base_code is None:
        current = store.current()
        base_code = current.code if current else INITIAL_CODE

    try:
        code = generate_code(base_code, req.prompt, req.provider, req.model)
    except LLMError as exc:
        raise HTTPException(502, f"LLM error: {exc}") from exc

    res = execute(code)
    step = store.add(
        kind="chat",
        original_prompt=req.prompt,
        code=res.code_with_geometry or code,
        stl_base64=res.stl_base64,
        geometry_info=res.geometry_info,
        success=res.success,
        error=res.error,
        make_current=res.success,
    )
    return {"step": step.to_public(), "session": _session_payload()}


@app.get("/api/export/{step_id}")
def export_step(step_id: int) -> Response:
    step = store.get(step_id)
    if step is None or not step.stl_base64:
        raise HTTPException(404, f"No STL available for step {step_id}")
    data = base64.b64decode(step.stl_base64)
    return Response(
        content=data,
        media_type="model/stl",
        headers={"Content-Disposition": f'attachment; filename="model_step_{step_id}.stl"'},
    )


# Serve the built frontend (if present). Hashed assets are cacheable forever;
# index.html is served with no-cache so a fresh build always loads. The SPA
# catch-all is registered last, so the /api/* routes above still win.
if STATIC_DIR.exists():
    app.mount("/assets", StaticFiles(directory=STATIC_DIR / "assets"), name="assets")

    @app.get("/{_path:path}")
    def spa(_path: str) -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html", headers={"Cache-Control": "no-cache"})
