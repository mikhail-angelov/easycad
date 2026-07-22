"""SPEC11 — CadQuery Chat API.

A text-only, single-screen incremental 3D-model builder. The client sends
short modification prompts; the server generates CadQuery code (LLM), executes
it in an isolated worker, and returns the STL + geometry info as a new step.
"""

import base64
import json
import os
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .cadquery_exec import execute
from .llm import DEFAULT_PROVIDER, INITIAL_CODE, LLMError, PROVIDERS, generate_code
from .refiner import triage
from .store import SessionStore

ROOT = Path(__file__).resolve().parent.parent
STATIC_DIR = ROOT / "static"

# Where the current session is autosaved (override for tests / custom location).
AUTOSAVE = Path(os.getenv("EASYCAD_SESSION_FILE", str(Path.home() / ".easycad" / "session.json")))

load_dotenv(ROOT / ".env")

app = FastAPI(title="EasyCAD — CadQuery Chat")
store = SessionStore()


def _persist() -> None:
    """Best-effort autosave of the session; never break a request on failure."""
    try:
        AUTOSAVE.parent.mkdir(parents=True, exist_ok=True)
        AUTOSAVE.write_text(
            json.dumps(store.to_project(), ensure_ascii=False), encoding="utf-8"
        )
    except Exception:
        pass


# ── Request models ────────────────────────────────────────────────────────────


class ChatRequest(BaseModel):
    prompt: str  # what the user typed (the original request)
    current_code: str | None = None
    provider: str = DEFAULT_PROVIDER
    model: str | None = None
    auto_refine: bool = True
    # When confirming a proposed refinement, the confirmed instruction to build
    # from; `prompt` still holds the original. Only used with auto_refine=False.
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


# ── Helpers ───────────────────────────────────────────────────────────────────


def _create_initial() -> None:
    """Create the starting box as step 0 and autosave."""
    res = execute(INITIAL_CODE)
    store.add(
        kind="initial",
        code=res.code_with_geometry or INITIAL_CODE,
        stl_base64=res.stl_base64,
        geometry_info=res.geometry_info,
        success=res.success,
        error=res.error,
    )
    _persist()


def _ensure_initial() -> None:
    """Resume the autosaved session, else create step 0 (the starting box)."""
    if store.all():
        return

    if AUTOSAVE.exists():
        try:
            store.load_project(json.loads(AUTOSAVE.read_text(encoding="utf-8")))
        except Exception:
            store.reset()
        if store.all():
            return

    _create_initial()


def _ensure_step_stl(step) -> None:
    """Lazily regenerate a step's STL from its code (STL isn't persisted).

    No-op if the STL is already in memory, or the step failed / has no code.
    """
    if step is None or step.stl_base64 or not step.success or not step.code:
        return
    res = execute(step.code)
    if res.success:
        step.stl_base64 = res.stl_base64
        if res.geometry_info:
            step.geometry_info = res.geometry_info


def _session_payload() -> dict:
    current = store.current()
    _ensure_step_stl(current)
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
    """Start a brand-new project (does not reload the autosave)."""
    store.reset()
    _create_initial()
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
    _ensure_step_stl(step)
    return step.to_public()


@app.post("/api/steps/{step_id}/revert")
def revert_step(step_id: int) -> dict:
    if store.revert(step_id) is None:
        raise HTTPException(404, f"Step {step_id} not found")
    _persist()
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
    _persist()
    return {"step": step.to_public(), "session": _session_payload()}


def _base_code(current_code: str | None) -> str:
    if current_code is not None:
        return current_code
    current = store.current()
    return current.code if current else INITIAL_CODE


@app.post("/api/refine")
def api_refine(req: RefineRequest) -> dict:
    """Stage 1 only — triage a prompt (verdict + optional refined/questions)."""
    _ensure_initial()
    try:
        t = triage(req.prompt, _base_code(req.current_code), req.provider, req.model)
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
    base_code: str,
    gen_prompt: str,
    original_prompt: str,
    refined_prompt: str | None,
    provider: str,
    model: str | None,
) -> dict:
    """Generate code from `gen_prompt`, execute it, and record a chat step."""
    try:
        code = generate_code(base_code, gen_prompt, provider, model)
    except LLMError as exc:
        raise HTTPException(502, f"LLM error: {exc}") from exc

    res = execute(code)
    step = store.add(
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
    _persist()
    return {
        "action": "generated",
        "original_prompt": original_prompt,
        "refined_prompt": refined_prompt,
        "reason": None,
        "questions": [],
        "step": step.to_public(),
        "session": _session_payload(),
    }


def _no_step(action: str, original_prompt: str, **extra) -> dict:
    payload = {
        "action": action,
        "original_prompt": original_prompt,
        "refined_prompt": None,
        "reason": None,
        "questions": [],
        "step": None,
        "session": _session_payload(),
    }
    payload.update(extra)
    return payload


@app.post("/api/chat")
def api_chat(req: ChatRequest) -> dict:
    """Triage the request, then either generate, ask to confirm a refinement,
    ask clarifying questions, or report a contradiction.

    Response `action` is one of: "generated" (step created), "confirm_refine"
    (refined_prompt proposed, awaiting user confirmation), "clarify" (questions),
    or "invalid" (reason). Nothing is generated except on "generated".
    """
    _ensure_initial()
    base_code = _base_code(req.current_code)

    # Direct path: refine off, or confirming a proposed refinement.
    if not req.auto_refine:
        gen_prompt = req.refined_prompt or req.prompt
        return _generate_and_step(
            base_code, gen_prompt, req.prompt, req.refined_prompt, req.provider, req.model
        )

    try:
        t = triage(req.prompt, base_code, req.provider, req.model)
    except LLMError as exc:
        raise HTTPException(502, f"Triage error: {exc}") from exc

    if t.verdict == "clarify":
        return _no_step("clarify", req.prompt, questions=t.questions)
    if t.verdict == "invalid":
        return _no_step("invalid", req.prompt, reason=t.reason)
    if t.verdict == "refine":
        return _no_step("confirm_refine", req.prompt, refined_prompt=t.refined_prompt)

    # ready → generate directly from the untouched original prompt.
    return _generate_and_step(base_code, req.prompt, req.prompt, None, req.provider, req.model)


@app.post("/api/variations")
def api_variations(req: VariationsRequest) -> dict:
    """Triage once, then generate `count` distinct candidates to pick from.

    Unlike /api/chat, a "refine" verdict is applied automatically here (this is
    an explicit "give me options" action). "clarify"/"invalid" short-circuit.
    Nothing is committed — the client commits the chosen candidate.
    """
    _ensure_initial()
    base_code = _base_code(req.current_code)

    gen_prompt = req.prompt
    refined_prompt: str | None = None
    if req.auto_refine:
        try:
            t = triage(req.prompt, base_code, req.provider, req.model)
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
            code = generate_code(base_code, gen_prompt, req.provider, req.model, temperature=0.7)
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
def api_commit(req: CommitRequest) -> dict:
    """Commit a chosen candidate (or edited code) as a new step."""
    res = execute(req.code)
    step = store.add(
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
    _persist()
    return {"step": step.to_public(), "session": _session_payload()}


@app.get("/api/project/export")
def export_project() -> Response:
    """Download the whole project (all steps) as a single JSON file."""
    _ensure_initial()
    body = json.dumps(store.to_project(), indent=2, ensure_ascii=False)
    return Response(
        content=body,
        media_type="application/json; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="easycad-project.json"'},
    )


@app.post("/api/project/import")
def import_project(project: dict) -> dict:
    """Load a project file, replacing the current session."""
    try:
        store.load_project(project)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, f"Invalid project file: {exc}") from exc
    if not store.all():
        raise HTTPException(400, "Project file has no steps")
    _persist()
    return _session_payload()


@app.get("/api/export/{step_id}")
def export_step(step_id: int) -> Response:
    step = store.get(step_id)
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


# Serve the built frontend (if present). Hashed assets are cacheable forever;
# index.html is served with no-cache so a fresh build always loads. The SPA
# catch-all is registered last, so the /api/* routes above still win.
if STATIC_DIR.exists():
    app.mount("/assets", StaticFiles(directory=STATIC_DIR / "assets"), name="assets")

    @app.get("/{_path:path}")
    def spa(_path: str) -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html", headers={"Cache-Control": "no-cache"})
