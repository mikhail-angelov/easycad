"""The small public EasyCAD API: image, prompt, and STL."""

import base64
import os
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .ai_generation import (
    GenerationError,
    generate_draft_specification_from_image,
    generate_draft_specification_from_text,
    plan_draft_specification,
)
from .feature_roster import feature_roster
from .minimal_model import fallback_draft, minimal_reliable_draft
from .models import CADProject, DraftSpecification
from .multiview_triangulation import build_grounding_instructions
from .runner import RunnerError, run_project
from .specification import SpecificationValidationError, project_from_specification, resolve_dimension_values


ROOT = Path(__file__).resolve().parent.parent
STATIC_DIR = ROOT / "static"


def load_env() -> None:
    path = ROOT / ".env"
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        if "=" not in line or line.lstrip().startswith("#"):
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


load_env()
app = FastAPI(title="EasyCAD")


class TextRequest(BaseModel):
    instructions: str


class PromptRequest(BaseModel):
    specification: DraftSpecification
    prompt: str
    referenced_feature_ids: list[str] = []


class StlRequest(BaseModel):
    specification: DraftSpecification


def _error(exc: Exception) -> HTTPException:
    if isinstance(exc, GenerationError):
        return HTTPException(422, {"stage": exc.stage, "message": str(exc), "detail": exc.detail})
    if isinstance(exc, RunnerError):
        return HTTPException(422, {"stage": exc.stage, "message": str(exc), "detail": exc.detail})
    if isinstance(exc, SpecificationValidationError):
        return HTTPException(422, {"stage": "specification", "message": str(exc), "detail": {"field_ids": exc.field_ids}})
    return HTTPException(422, {"stage": "model", "message": str(exc)})


def _build_or_fallback(draft: DraftSpecification) -> tuple[CADProject, dict, DraftSpecification]:
    """Build the (already-sanitized) draft; a schema-valid draft can still fail the real
    CadQuery worker (e.g. an infeasible fillet) in a way `minimal_reliable_draft`'s
    schema-level check cannot see. Retry once against the guaranteed-safe fallback rather
    than ever surfacing a build error to the user."""
    try:
        project = project_from_specification(draft)
        result = run_project(project, {}, fmt="stl")
        return project, result, draft
    except (RunnerError, SpecificationValidationError):
        draft = fallback_draft(draft)
        try:
            project = project_from_specification(draft)
            result = run_project(project, {}, fmt="stl")
            return project, result, draft
        except (RunnerError, SpecificationValidationError) as exc:
            raise _error(exc) from exc


def _model_response(draft: DraftSpecification, prefix: str) -> dict[str, object]:
    """Build the reliable draft once and return its STL for immediate viewing."""
    draft = minimal_reliable_draft(draft)
    project, result, draft = _build_or_fallback(draft)
    project.generation.status = "success"
    project.generation.semantic_status = "draft_preview"
    project.generation.execution_time_ms = int(result.get("duration_ms", 0))
    project.generation.bounding_box = result.get("bounding_box")
    project.generation.volume_mm3 = result.get("volume_mm3")
    project.generation.solid_count = result.get("solid_count")
    project.generation.feature_measurements = result.get("feature_measurements", {})
    values, _ = resolve_dimension_values(draft)
    roster = feature_roster(draft, values)
    return {
        "description": _description(draft, prefix),
        "specification": draft.model_dump(mode="json"),
        "model": project.model_dump(mode="json"),
        "model_stl": base64.b64encode(result["artifact_bytes"]).decode("ascii"),
        "features": [entry.__dict__ for entry in roster],
    }


def _description(draft: DraftSpecification, prefix: str) -> str:
    features = [feature.label for feature in draft.features if feature.status == "confirmed"]
    return f"{prefix}: {', '.join(features) or 'minimal body'}."


@app.post("/api/model/image")
async def model_from_image(file: UploadFile = File(...), instructions: str = Form("")):
    try:
        image_bytes = await file.read()
        # Best-effort: '' for an ordinary single-view photo (the common case) or if anything
        # here fails -- this never raises, so it can never turn a normal upload into an error.
        grounding = await build_grounding_instructions(image_bytes, os.environ.get("OPEN_ROUTER_KEY", ""))
        combined_instructions = f"{instructions}\n{grounding}".strip() if grounding else instructions
        draft = await generate_draft_specification_from_image(
            image_bytes, file.filename or "", file.content_type or "", combined_instructions
        )
        return _model_response(draft, "Created model")
    except (GenerationError, RunnerError, SpecificationValidationError) as exc:
        raise _error(exc) from exc


@app.post("/api/model/text")
async def model_from_text(request: TextRequest):
    try:
        draft = await generate_draft_specification_from_text(request.instructions)
        return _model_response(draft, "Created model")
    except (GenerationError, RunnerError, SpecificationValidationError) as exc:
        raise _error(exc) from exc


@app.post("/api/model/refine")
async def refine_model(request: PromptRequest):
    prompt = request.prompt.strip()
    if not prompt:
        raise HTTPException(422, {"stage": "prompt", "message": "Enter a model change."})
    known_feature_ids = {item.id for item in request.specification.features}
    referenced_feature_ids = [item for item in request.referenced_feature_ids if item in known_feature_ids]
    try:
        draft = await plan_draft_specification(
            request.specification.analysis.model_dump(mode="json"),
            "",
            os.environ.get("DEEP_SEEK_KEY", ""),
            previous_specification=request.specification,
            user_inputs={
                "clarifications": {"freeform_instruction": prompt}, "freeform_instruction": prompt,
                "referenced_feature_ids": referenced_feature_ids,
            },
        )
        draft.source = request.specification.source
        return _model_response(draft, f"Updated model for: {prompt}")
    except (GenerationError, RunnerError, SpecificationValidationError) as exc:
        raise _error(exc) from exc


@app.post("/api/model/stl")
def download_stl(request: StlRequest):
    project, result, _draft = _build_or_fallback(minimal_reliable_draft(request.specification))
    return Response(
        result["artifact_bytes"], media_type="model/stl",
        headers={"Content-Disposition": f'attachment; filename="{project.id}.stl"'},
    )


app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
