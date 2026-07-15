from __future__ import annotations

import json
import logging
import os
import base64
import hashlib
import io
from pathlib import Path
from typing import Dict
from uuid import uuid4

from PIL import Image

from fastapi import FastAPI, HTTPException, UploadFile, File, Form
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from .ai_generation import (
    GenerationError,
    compare_project_renders,
    generate_draft_specification_from_image,
    plan_draft_specification,
    validate_image_upload,
)
from .models import (
    CADProject,
    CompareRequest,
    PreviewRequest,
    RenderArtifact,
    SpecificationEditRequest,
    DraftSpecification,
    SpecificationQuestion,
)
from .specification import (
    SpecificationValidationError,
    project_from_specification,
    validate_specification,
)
from .runner import RunnerError, concrete_parameters, run_project
from .validator import ValidationError, validate_project as validate_project_model


ROOT = Path(__file__).resolve().parent.parent
PROJECT_DIR = ROOT / "projects"
STATIC_DIR = ROOT / "static"
FIXTURE_DIR = ROOT / "fixtures"


def load_env() -> None:
    env_path = ROOT / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


load_env()

app = FastAPI(title="EasyCAD")
logger = logging.getLogger("easycad.api")


def _specification_error(exc: SpecificationValidationError) -> HTTPException:
    return HTTPException(422, {"stage": "specification_validation", "message": str(exc), "detail": {"field_ids": exc.field_ids, "messages": exc.messages}})


def load_project_json(text: str) -> CADProject:
    project = CADProject.model_validate_json(text)
    if project.cad.source_kind != "compiled" or not project.feature_graph.operations:
        mark_generation_error(
            project,
            "legacy_project",
            "Legacy source-only projects require Feature Graph migration before preview or export",
        )
    return project


def validate_trusted_project(project: CADProject) -> None:
    if project.cad.source_kind != "compiled" or not project.feature_graph.operations:
        raise RunnerError(
            "legacy_project",
            "Legacy source-only projects require Feature Graph migration before preview or export",
        )


def apply_generation_metadata(project: CADProject, result: Dict[str, object]) -> None:
    metadata = {k: v for k, v in result.items() if k not in {"artifact_bytes", "render_artifacts"}}
    project.generation.status = "success"
    project.generation.execution_time_ms = int(metadata.get("duration_ms", 0))
    project.generation.bounding_box = metadata.get("bounding_box")
    project.generation.volume_mm3 = metadata.get("volume_mm3")
    project.generation.solid_count = metadata.get("solid_count")
    project.generation.feature_measurements = metadata.get("feature_measurements", {})
    render_artifacts = result.get("render_artifacts", {})
    if isinstance(render_artifacts, dict):
        project.generation.render_artifacts = {
            view: _render_artifact_payload(view, data)
            for view, data in render_artifacts.items()
            if isinstance(data, bytes)
        }
    worker_warnings = metadata.get("warnings", [])
    project.generation.warnings = list(dict.fromkeys([*project.generation.warnings, *worker_warnings]))
    project.generation.error = None


def _render_artifact_payload(view: str, data: bytes) -> RenderArtifact:
    with Image.open(io.BytesIO(data)) as image:
        width, height = image.size
    return RenderArtifact(
        view=view,
        mime_type="image/png",
        image_data=f"data:image/png;base64,{base64.b64encode(data).decode('ascii')}",
        sha256=hashlib.sha256(data).hexdigest(),
        width=width,
        height=height,
    )


def mark_generation_error(project: CADProject, stage: str, message: str, detail: Dict[str, object] | None = None) -> None:
    project.generation.status = "needs_review"
    project.generation.error = {"stage": stage, "message": message, "detail": detail or {}}
    if stage == "static_validation":
        project.generation.syntax_status = "failed"
    elif stage in {"worker", "worker_import", "worker_timeout", "cadquery_execution", "export"}:
        project.generation.geometry_status = "failed"
    else:
        project.generation.semantic_status = "failed"


def validate_generation_geometry(project: CADProject, result: Dict[str, object], tolerance: float = 1.0) -> None:
    bbox = result.get("bounding_box")
    if not isinstance(bbox, dict):
        return
    expected = {
        "x": _number_parameter(project, ("overall_length", "length")),
        "y": _number_parameter(project, ("overall_width", "width", "depth", "overall_depth")),
        "z": _number_parameter(project, ("overall_height", "height")),
    }
    mismatches = []
    for axis, value in expected.items():
        if value is None:
            continue
        actual = bbox.get(axis)
        if actual is None:
            continue
        if abs(float(actual) - value) > tolerance:
            mismatches.append(f"{axis}: expected {value:g}, got {float(actual):g}")
    if mismatches:
        raise RunnerError(
            "geometry_validation",
            "Generated model bounding box does not match declared overall dimensions",
            {"mismatches": mismatches, "bounding_box": bbox, "expected": expected},
        )

    max_volume = _expected_l_bracket_envelope_volume(project)
    actual_volume = result.get("volume_mm3")
    if max_volume is not None and actual_volume is not None and float(actual_volume) > max_volume * 1.05:
        raise RunnerError(
            "geometry_validation",
            "Generated model volume is too large for declared L-bracket dimensions",
            {"volume_mm3": actual_volume, "max_expected_volume_mm3": max_volume},
        )


def validate_feature_coverage(project: CADProject) -> None:
    unresolved_ids = [
        entry.feature_id
        for entry in project.feature_coverage.entries
        if entry.confidence >= 0.8 and entry.status in {"planned", "unresolved", "unsupported"}
    ]
    if unresolved_ids:
        raise RunnerError(
            "feature_coverage",
            "High-confidence drawing features are not implemented",
            {"feature_ids": unresolved_ids},
        )


def validate_feature_measurements(project: CADProject, result: Dict[str, object]) -> None:
    measurements = result.get("feature_measurements")
    if not isinstance(measurements, dict):
        return
    errors = []
    final_solid_count = result.get("solid_count")
    if final_solid_count is not None and int(final_solid_count) != 1:
        errors.append(f"result: expected one printable solid, measured {int(final_solid_count)}")
    values = concrete_parameters(project, {})
    for operation in project.feature_graph.operations:
        measurement = measurements.get(operation.id)
        if not isinstance(measurement, dict):
            continue
        feature_type = operation.type.lower()
        solid_count = measurement.get("solid_count")
        if solid_count is not None and operation.operation == "add" and int(solid_count) != 1:
            errors.append(f"{operation.id}: additive feature is disconnected ({int(solid_count)} solids)")
        if operation.minimum_printable_thickness is not None:
            minimum = _resolved_feature_value(operation.minimum_printable_thickness, values)
            thickness_value = operation.parameters.get("thickness")
            if thickness_value is not None:
                actual_thickness = abs(_resolved_feature_value(thickness_value, values))
                if actual_thickness < minimum:
                    errors.append(
                        f"{operation.id}: thickness {actual_thickness:g} is below printable minimum {minimum:g}"
                    )
        delta = measurement.get("volume_delta_mm3")
        if delta is not None:
            delta = float(delta)
            if operation.operation == "add" and delta <= 1e-6:
                errors.append(f"{operation.id}: additive feature did not add material")
            elif operation.operation == "cut" and delta >= -1e-6:
                errors.append(f"{operation.id}: subtractive feature did not remove material")
            elif operation.operation == "modify":
                if feature_type in {"fillet", "chamfer", "shell"} and delta >= -1e-6:
                    errors.append(f"{operation.id}: subtractive modifier did not remove material")
                elif feature_type == "mirror" and delta <= 1e-6:
                    errors.append(f"{operation.id}: mirror did not add reflected material")
            elif operation.operation == "pattern":
                if any(token in feature_type for token in ("hole", "cut", "perforation", "pocket", "slot")):
                    if delta >= -1e-6:
                        errors.append(f"{operation.id}: subtractive pattern did not remove material")
                elif any(token in feature_type for token in ("rib", "additive", "boss")) and delta <= 1e-6:
                    errors.append(f"{operation.id}: additive pattern did not add material")
        if operation.operation == "pattern" and any(
            token in feature_type for token in ("hole", "perforation")
        ):
            expected = measurement.get("expected_instance_count")
            actual = measurement.get("cylindrical_faces_delta")
            if expected is not None and actual is not None and int(actual) != int(expected):
                errors.append(f"{operation.id}: expected {expected} holes, measured {actual}")
            if operation.pattern and operation.pattern.type == "linear":
                expected_pitch = _resolved_feature_value(operation.pattern.pitch, values)
                expected_margin = _resolved_feature_value(operation.pattern.start_margin, values, default=0.0)
                measured_pitch = measurement.get("measured_pitch")
                measured_margin = measurement.get("measured_start_margin")
                if measured_pitch is not None and not _measurement_close(float(measured_pitch), expected_pitch):
                    errors.append(
                        f"{operation.id}: expected pitch {expected_pitch:g}, measured {float(measured_pitch):g}"
                    )
                if measured_margin is not None and not _measurement_close(float(measured_margin), expected_margin):
                    errors.append(
                        f"{operation.id}: expected margin {expected_margin:g}, measured {float(measured_margin):g}"
                    )
            if operation.profile and "diameter" in operation.profile.dimensions:
                expected_diameter = _resolved_feature_value(operation.profile.dimensions["diameter"], values)
                measured_diameter = measurement.get("measured_cylinder_diameter")
                if measured_diameter is not None and not _measurement_close(
                    float(measured_diameter), expected_diameter
                ):
                    errors.append(
                        f"{operation.id}: expected diameter {expected_diameter:g}, measured {float(measured_diameter):g}"
                    )
    if errors:
        feature_ids = [error.split(":", 1)[0] for error in errors]
        raise RunnerError(
            "semantic_validation",
            "Feature measurements do not match the Feature Graph",
            {"feature_ids": feature_ids, "mismatches": errors},
        )


def _resolved_feature_value(value, values: Dict[str, object], default: float | None = None) -> float:
    if value is None:
        if default is None:
            raise RunnerError("semantic_validation", "Required feature measurement value is missing")
        return default
    return float(values[value] if isinstance(value, str) else value)


def _measurement_close(actual: float, expected: float) -> bool:
    return abs(actual - expected) <= max(0.2, abs(expected) * 0.01)


def _number_parameter(project: CADProject, names: tuple[str, ...]) -> float | None:
    for name in names:
        param = project.parameters.get(name)
        if param and param.type == "number" and param.value is not None:
            return float(param.value)
    return None


def _expected_l_bracket_envelope_volume(project: CADProject) -> float | None:
    length = _number_parameter(project, ("overall_length", "length"))
    width = _number_parameter(project, ("overall_width", "width", "depth", "overall_depth"))
    height = _number_parameter(project, ("overall_height", "height"))
    base_height = _number_parameter(project, ("base_height", "base_thickness", "base_h"))
    upright = _number_parameter(project, ("upright_thickness", "upright_length", "vert_w"))
    if None in (length, width, height, base_height, upright):
        return None
    if not (0 < base_height < height and 0 < upright < length):
        return None
    return length * width * base_height + upright * width * (height - base_height)


@app.get("/api/health")
def health() -> Dict[str, object]:
    return {
        "status": "ok",
        "has_openrouter_key": bool(os.environ.get("OPEN_ROUTER_KEY")),
        "openrouter_model": os.environ.get("OPEN_ROUTER_MODEL", ""),
        "has_deepseek_key": bool(os.environ.get("DEEP_SEEK_KEY")),
    }


@app.get("/api/projects/fixtures")
def fixture_projects():
    projects = []
    for path in sorted(PROJECT_DIR.glob("*.json")):
        project = load_project_json(path.read_text(encoding="utf-8"))
        projects.append({"id": project.id, "title": project.title, "path": path.name})
    return projects


@app.get("/api/projects/fixtures/{name}")
def load_fixture_project(name: str):
    path = PROJECT_DIR / name
    if path.suffix != ".json" or not path.exists() or path.parent != PROJECT_DIR:
        raise HTTPException(404, "Fixture project not found")
    return JSONResponse(json.loads(path.read_text(encoding="utf-8")))


@app.post("/api/projects/validate")
def validate_project(project: CADProject):
    errors = []
    try:
        validate_project_model(project)
    except ValidationError as exc:
        errors.append({"stage": "project_validation", "message": str(exc)})
    return {"valid": not errors, "errors": errors, "warnings": []}


@app.post("/api/specifications/analyze")
async def analyze_specification(
    file: UploadFile = File(...),
    instructions: str = Form(""),
    input_mode: str = Form("sketch"),
    has_orthographic_views: bool = Form(False),
    has_isometric_view: bool = Form(False),
    has_units_and_overall_dimensions: bool = Form(False),
    has_feature_positions: bool = Form(False),
    has_feature_dimensions_and_directions: bool = Form(False),
):
    request_id = uuid4().hex[:12]
    logger.info(
        "specification_analyze_started request_id=%s input_mode=%s filename_present=%s",
        request_id,
        input_mode,
        bool(file.filename),
    )
    try:
        input_warning = validate_input_quality_gate(
            input_mode,
            has_orthographic_views=has_orthographic_views,
            has_isometric_view=has_isometric_view,
            has_units_and_overall_dimensions=has_units_and_overall_dimensions,
            has_feature_positions=has_feature_positions,
            has_feature_dimensions_and_directions=has_feature_dimensions_and_directions,
        )
    except HTTPException as exc:
        logger.warning("specification_analyze_rejected request_id=%s detail=%s", request_id, exc.detail)
        raise
    try:
        draft = await generate_draft_specification_from_image(
            await file.read(), file.filename or "", file.content_type or "", instructions
        )
    except GenerationError as exc:
        logger.warning(
            "specification_analyze_failed request_id=%s stage=%s detail_keys=%s",
            request_id,
            exc.stage,
            sorted(exc.detail),
        )
        raise HTTPException(422, {"stage": exc.stage, "message": str(exc), "detail": exc.detail, "request_id": request_id})
    if input_warning:
        draft.questions.append(SpecificationQuestion(id="sketch_review", field_id="input_mode", prompt=input_warning, required=False))
    logger.info("specification_analyze_succeeded request_id=%s", request_id)
    return {"specification": draft.model_dump(mode="json"), "request_id": request_id}


@app.post("/api/specifications/validate")
async def validate_specification_endpoint(req: SpecificationEditRequest):
    clarifications = [(question_id, text.strip()) for question_id, text in req.clarifications.items() if text.strip()]
    user_inputs = {
        "dimension_values": req.dimension_values,
        "accepted_feature_ids": req.accepted_feature_ids,
        "accepted_assumption_ids": req.accepted_assumption_ids,
        "clarifications": dict(clarifications),
    }
    if any(user_inputs.values()):
        try:
            draft = await plan_draft_specification(
                req.specification.analysis.model_dump(mode="json"),
                "",
                os.environ.get("DEEP_SEEK_KEY", ""),
                previous_specification=req.specification,
                user_inputs=user_inputs,
            )
            draft.source = req.specification.source
        except GenerationError as exc:
            return {"valid": False, "specification": req.specification.model_dump(mode="json"), "diagnostics": {"field_ids": [], "messages": [str(exc)]}}
    else:
        draft = req.specification
    try:
        values = validate_specification(draft)
    except SpecificationValidationError as exc:
        return {"valid": False, "specification": draft.model_dump(mode="json"), "diagnostics": {"field_ids": exc.field_ids, "messages": exc.messages}}
    return {"valid": True, "values": values, "specification": draft.model_dump(mode="json")}


@app.post("/api/specifications/build")
def build_specification(specification: DraftSpecification):
    try:
        project = project_from_specification(specification)
    except SpecificationValidationError as exc:
        raise _specification_error(exc)
    except Exception as exc:
        raise HTTPException(422, {"stage": "feature_graph", "message": str(exc)}) from exc
    try:
        result = run_project(project, {}, fmt="stl", render_views=True)
        project.generation.syntax_status = "success"
        project.generation.geometry_status = "success"
        validate_generation_geometry(project, result)
        validate_feature_measurements(project, result)
        validate_feature_coverage(project)
        project.generation.semantic_status = "success"
        apply_generation_metadata(project, result)
    except RunnerError as exc:
        mark_generation_error(project, exc.stage, str(exc), exc.detail)
        return {"status": "needs_review", "project": project.model_dump(mode="json"), "diagnostics": exc.detail}
    return {"status": "success", "project": project.model_dump(mode="json")}


def validate_input_quality_gate(
    input_mode: str,
    *,
    has_orthographic_views: bool,
    has_isometric_view: bool,
    has_units_and_overall_dimensions: bool,
    has_feature_positions: bool,
    has_feature_dimensions_and_directions: bool,
) -> str | None:
    mode = input_mode.strip().lower()
    if mode == "sketch":
        return "Sketch/photo input: verify ambiguous geometry before exporting for print."
    if mode != "engineering":
        raise HTTPException(422, {"stage": "input_quality", "message": "Unknown drawing input mode"})
    checks = {
        "orthographic_views": has_orthographic_views,
        "units_and_overall_dimensions": has_units_and_overall_dimensions,
        "feature_positions": has_feature_positions,
        "feature_dimensions_and_directions": has_feature_dimensions_and_directions,
    }
    missing = [name for name, present in checks.items() if not present]
    if missing:
        raise HTTPException(
            422,
            {
                "stage": "input_quality",
                "message": "Engineering drawing is missing required input confirmations",
                "detail": {"missing": missing},
            },
        )
    return None if has_isometric_view else "No isometric view confirmed; review ambiguous geometry before building."


@app.post("/api/projects/compare")
async def compare_generated_project(req: CompareRequest):
    api_key = os.environ.get("OPEN_ROUTER_KEY")
    if not api_key:
        raise HTTPException(422, {"stage": "visual_comparison", "message": "OPEN_ROUTER_KEY is not configured"})
    source_before = req.project.cad.source
    graph_before = req.project.feature_graph.model_dump()
    parameters_before = {key: value.model_dump() for key, value in req.project.parameters.items()}
    try:
        comparison = await compare_project_renders(req.project, api_key)
    except GenerationError as exc:
        raise HTTPException(422, {"stage": exc.stage, "message": str(exc), "detail": exc.detail})
    req.project.generation.visual_comparison = comparison
    if (
        req.project.cad.source != source_before
        or req.project.feature_graph.model_dump() != graph_before
        or {key: value.model_dump() for key, value in req.project.parameters.items()} != parameters_before
    ):
        raise HTTPException(500, {"stage": "visual_comparison", "message": "Advisory comparison mutated geometry"})
    return {"status": "advisory", "project": json.loads(req.project.model_dump_json())}


@app.post("/api/projects/preview")
def preview(req: PreviewRequest):
    try:
        validate_trusted_project(req.project)
        result = run_project(req.project, req.parameters, fmt="stl")
    except ValidationError as exc:
        raise HTTPException(422, {"stage": "static_validation", "message": str(exc)})
    except RunnerError as exc:
        raise HTTPException(422, {"stage": exc.stage, "message": str(exc), "detail": exc.detail})
    metadata = {k: v for k, v in result.items() if k not in {"artifact_bytes", "render_artifacts"}}
    return Response(
        result["artifact_bytes"],
        media_type="model/stl",
        headers={"X-EasyCAD-Generation": json.dumps(metadata)},
    )


@app.post("/api/projects/export")
def export(req: PreviewRequest, format: str = "step"):
    fmt = format.lower()
    try:
        validate_trusted_project(req.project)
        validate_feature_coverage(req.project)
    except RunnerError as exc:
        raise HTTPException(422, {"stage": exc.stage, "message": str(exc), "detail": exc.detail})
    if fmt == "json":
        filename = f"{req.project.id}.json"
        return Response(
            req.project.model_dump_json(indent=2).encode("utf-8"),
            media_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    try:
        result = run_project(req.project, req.parameters, fmt=fmt)
    except ValidationError as exc:
        raise HTTPException(422, {"stage": "static_validation", "message": str(exc)})
    except RunnerError as exc:
        raise HTTPException(422, {"stage": exc.stage, "message": str(exc), "detail": exc.detail})
    media_type = "model/step" if fmt == "step" else "model/stl"
    ext = "step" if fmt == "step" else "stl"
    filename = f"{req.project.id}.{ext}"
    return Response(
        result["artifact_bytes"],
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.post("/api/uploads/inspect")
async def inspect_upload(file: UploadFile = File(...)):
    data = await file.read()
    image_info = validate_image_upload(data, file.filename or "", file.content_type or "")
    return {"status": "ok", **image_info}


app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("PORT", "8852")))
