from __future__ import annotations

import base64
import json
import logging
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import uuid4

import httpx
from fastapi import HTTPException
from PIL import Image, UnidentifiedImageError
from pydantic import ValidationError as PydanticValidationError

from .models import (
    CADParameter,
    CADProject,
    CADSource,
    DrawingAnalysis,
    FeatureCoverageReport,
    FeatureGraph,
    FeatureOperation,
    FeatureSummary,
    GenerationHistoryEntry,
    GenerationResult,
    SourceInfo,
    VisualComparison,
)
from .feature_compiler import CompilerError, compile_project_feature_graph
from .source_images import get_source_image, store_source_image
from .validator import validate_project


MAX_UPLOAD_BYTES = 20 * 1024 * 1024
MAX_IMAGE_DIMENSION = 12000
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
LLM_LOG_DIR = Path(os.environ.get("EASYCAD_LLM_LOG_DIR", "logs"))
logger = logging.getLogger("easycad.llm")
MODEL_ALIASES = {
    "gemini_3_flash": "google/gemini-3-flash-preview",
    "gemini-3-flash": "google/gemini-3-flash-preview",
}
class GenerationError(RuntimeError):
    def __init__(self, stage: str, message: str, detail: Optional[dict] = None):
        super().__init__(message)
        self.stage = stage
        self.detail = detail or {}


def validate_image_upload(data: bytes, filename: str = "", mime_type: str = "") -> Dict[str, Any]:
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(400, "Image too large")
    if mime_type and mime_type not in {"image/png", "image/jpeg", "image/webp"}:
        raise HTTPException(400, "Unsupported image type")

    try:
        import io

        with Image.open(io.BytesIO(data)) as image:
            image.verify()
            width, height = image.size
            detected_format = (image.format or "").lower()
    except (UnidentifiedImageError, OSError):
        raise HTTPException(400, "Invalid image")

    if width > MAX_IMAGE_DIMENSION or height > MAX_IMAGE_DIMENSION:
        raise HTTPException(400, "Image dimensions are too large")

    detected_mime = {
        "jpeg": "image/jpeg",
        "png": "image/png",
        "webp": "image/webp",
    }.get(detected_format, mime_type)
    return {
        "filename": filename,
        "mime_type": detected_mime or mime_type,
        "width": width,
        "height": height,
    }


async def generate_project_from_image(
    data: bytes,
    filename: str,
    mime_type: str,
    instructions: str = "",
    validate_result: bool = True,
) -> CADProject:
    image_info = validate_image_upload(data, filename, mime_type)
    openrouter_key = os.environ.get("OPEN_ROUTER_KEY")
    deepseek_key = os.environ.get("DEEP_SEEK_KEY")
    if not openrouter_key:
        raise GenerationError("vision_analysis", "OPEN_ROUTER_KEY is not configured")
    if not deepseek_key:
        raise GenerationError("cad_generation", "DEEP_SEEK_KEY is not configured")

    analysis_payload = await analyze_drawing(data, image_info["mime_type"], instructions, openrouter_key)
    plan_payload = await plan_cad_project(analysis_payload, instructions, deepseek_key)
    project = project_from_plan(plan_payload, analysis_payload, image_info, data)
    if validate_result:
        validate_project(project)
    return project


async def repair_project(
    project: CADProject,
    user_feedback: str = "",
    current_view: Optional[str] = None,
    validate_result: bool = True,
) -> CADProject:
    deepseek_key = os.environ.get("DEEP_SEEK_KEY")
    if not deepseek_key:
        raise GenerationError("cad_generation", "DEEP_SEEK_KEY is not configured")

    payload = await plan_repair(project, user_feedback, current_view, deepseek_key)
    repaired = apply_repair_plan(project, payload)
    if validate_result:
        validate_project(repaired)
    return repaired


async def compare_project_renders(project: CADProject, api_key: str) -> VisualComparison:
    source_data = get_source_image(project.source.image_ref or "")
    if source_data is None and project.source.image_data:
        try:
            source_data = base64.b64decode(project.source.image_data.split(",", 1)[1])
        except (IndexError, ValueError) as exc:
            raise GenerationError("visual_comparison", "Saved source image data is invalid") from exc
    if source_data is None:
        raise GenerationError("visual_comparison", "Source drawing is no longer available in memory")
    if len(project.generation.render_artifacts) != 4:
        raise GenerationError("visual_comparison", "Four generated render views are required")

    feature_ids = [operation.id for operation in project.feature_graph.operations]
    prompt = (
        "Compare the source mechanical drawing with the generated CAD renders. Return only one JSON object with "
        "match_score from 0 to 1 and issues as an array. Each issue must contain issue_type (missing, extra, misplaced, "
        "dimension_mismatch, or other), severity (low, medium, high), description, feature_id, view, and confidence. "
        "Use only these feature IDs when applicable: "
        + json.dumps(feature_ids)
        + ". Do not propose code and do not invent hidden geometry."
    )
    content = [
        {"type": "text", "text": prompt},
        {
            "type": "image_url",
            "image_url": {
                "url": f"data:{project.source.mime_type};base64,{base64.b64encode(source_data).decode('ascii')}"
            },
        },
    ]
    for view in ("front", "top", "right", "isometric"):
        artifact = project.generation.render_artifacts[view]
        content.append({"type": "text", "text": f"Generated {view} view:"})
        content.append({"type": "image_url", "image_url": {"url": artifact.image_data}})
    payload = {
        "model": normalize_model_id(os.environ.get("OPEN_ROUTER_MODEL", "google/gemini-3-flash-preview")),
        "messages": [{"role": "user", "content": content}],
        "temperature": 0.1,
        "max_tokens": 2500,
        "response_format": {"type": "json_object"},
    }
    raw = await _chat_json(OPENROUTER_URL, api_key, payload, "visual_comparison")
    try:
        return VisualComparison.model_validate(
            {"status": "advisory", "match_score": raw.get("match_score"), "issues": raw.get("issues", [])}
        )
    except PydanticValidationError as exc:
        raise GenerationError("visual_comparison", f"Invalid visual comparison: {exc.errors()[0]['msg']}") from exc


async def analyze_drawing(data: bytes, mime_type: str, instructions: str, api_key: str) -> Dict[str, Any]:
    model = normalize_model_id(os.environ.get("OPEN_ROUTER_MODEL", "google/gemini-3-flash-preview"))
    data_url = f"data:{mime_type};base64,{base64.b64encode(data).decode('ascii')}"
    prompt = (
        "Analyze this mechanical technical drawing and return only one JSON object with keys: "
        "title, units, views, dimensions, features, uncertainties, overall_shape, construction_strategy. "
        "Do not generate CAD code. Use millimeters unless the drawing clearly says otherwise. "
        "Mark unreadable or inferred values in uncertainties. "
        "features must be a complete array of visible bodies, ribs, gussets, holes, perforations, slots, pockets, "
        "grooves, shells, fillets, chamfers, text, and repeated patterns. Each feature must contain a stable snake_case id, "
        "name, type, operation_hint (add, cut, intersect, modify, or pattern), target when it modifies another feature, "
        "depends_on as an array of feature ids, confidence from 0 to 1, and evidence with source view ids and dimension ids. "
        "When visible, include profile, placement with reference/plane/origin/axis/direction, and pattern with "
        "type/count/pitch/angle_deg/axis/start_margin/end_margin. Never merge a repeated perforation into its host rib. "
        "If placement, count, depth, or spacing is unclear, preserve the feature and describe the missing value in uncertainties. "
    )
    if instructions.strip():
        prompt += f"\nUser instructions: {instructions.strip()}"

    payload = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ],
            }
        ],
        "temperature": 0.1,
        "max_tokens": 3000,
        "response_format": {"type": "json_object"},
    }
    result = await _chat_json(OPENROUTER_URL, api_key, payload, "vision_analysis")
    return normalize_drawing_analysis(result)


async def plan_cad_project(analysis: Dict[str, Any], instructions: str, api_key: str) -> Dict[str, Any]:
    model = normalize_model_id(os.environ.get("DEEP_SEEK_MODEL", os.environ.get("DEEPSEEK_MODEL", "deepseek-chat")))
    url = os.environ.get("DEEP_SEEK_BASE_URL", "https://api.deepseek.com/chat/completions")
    prompt = (
        "Generate a structured parametric CAD plan from this drawing analysis. "
        "Return only one JSON object with keys: title, confidence, parameters, feature_graph, feature_summary, assumptions. "
        "parameters must be an array of objects with id, label, type, value or expression, unit, min, max, step, source, confidence, editable. "
        "Supported parameter types are number, expression, text, and choice. Choice parameters may include options. "
        "feature_summary must contain id, name, type, description. "
        "feature_graph must contain operations mapping every drawing_analysis feature id. Each operation must contain id, type, "
        "operation, source_feature_ids, target when applicable, confidence, status, and implementation. Use status implemented "
        "when a trusted compiler operation represents the feature. Use approximated, "
        "unresolved, or unsupported with an assumption when exact implementation is not possible. Never silently omit a feature. "
        "Do not return Python or CadQuery source. Every engineering dimension must be represented by a parameter reference. "
        "Prefer simple extrusions, revolutions, holes, pockets, chamfers, fillets, and simple top-face text features. "
        "If the drawing shows engraved, embossed, stamped, or printed lettering, transcribe it exactly, including Cyrillic, and add parameters "
        "text_content (type text), text_mode (type choice with options none, engrave, emboss), and text_size (type number, mm). "
        "Model clear text markings with CadQuery .text(...) on a stable face when the placement is obvious. "
        "For local CadQuery use combine='cut' for engraved/recessed text and combine='a' for embossed/raised text; do not use a cut= keyword. "
        "On a top face, recessed text must use negative distance into the solid, for example .text(label, size, -depth, combine='cut'). "
        "If the face or placement is ambiguous, add an assumption. "
        "Do not model real screw threads, helical geometry, or decorative thread ridges; represent threaded sections as plain cylinders at major diameter. "
        "Avoid fragile operations that often fail export, including helixes, freeform sweeps, and boolean cuts with tangent or zero-thickness contact. "
        "If a dimension is unclear, create an assumed parameter and add an assumption. "
        "Do not include Markdown fences or prose outside JSON."
    )
    if instructions.strip():
        prompt += f"\nUser instructions: {instructions.strip()}"

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": prompt},
            {"role": "user", "content": json.dumps({"drawing_analysis": analysis}, ensure_ascii=False)},
        ],
        "temperature": 0.1,
        "max_tokens": 5000,
        "response_format": {"type": "json_object"},
    }
    result = await _chat_json(url, api_key, payload, "cad_generation")
    if _has_cad_plan_shape(result):
        return result
    retry_payload = dict(payload)
    retry_payload["messages"] = list(payload["messages"]) + [
        {
            "role": "user",
            "content": (
                "The previous JSON was not a complete CAD plan. Return one JSON object with "
                "top-level keys exactly including parameters, feature_graph, feature_summary, and assumptions. "
                "parameters must be an array, not a single parameter object."
            ),
        }
    ]
    result = await _chat_json(url, api_key, retry_payload, "cad_generation")
    if not _has_cad_plan_shape(result):
        raise GenerationError("cad_generation", "CAD plan did not include required parameters and Feature Graph")
    return result


async def plan_repair(
    project: CADProject,
    user_feedback: str,
    current_view: Optional[str],
    api_key: str,
) -> Dict[str, Any]:
    model = os.environ.get("DEEP_SEEK_MODEL", os.environ.get("DEEPSEEK_MODEL", "deepseek-chat"))
    model = normalize_model_id(model)
    url = os.environ.get("DEEP_SEEK_BASE_URL", "https://api.deepseek.com/chat/completions")
    prompt = (
        "Repair this structured CAD project. Return only one JSON object with keys: assumptions, feature_summary, "
        "operation_updates, repaired_feature_ids. Do not return Python or CadQuery source. "
        "Make the smallest correction needed. Preserve parameter IDs and do not remove features silently. "
        "When error_feature_ids are present, repair only those Feature Graph operations and required dependencies. "
        "Preserve every unrelated operation ID and definition exactly. Report repaired_feature_ids in the response. "
        "If export or boolean operations failed, update only the responsible structured operation. "
        "Avoid tangent/zero-thickness booleans and fragile sweeps. "
        "If the error reports a bounding-box mismatch, fix coordinate placement so overall_length, overall_width, and overall_height match the model extents. "
        "If the error reports excessive L-bracket volume, build the part as a base block plus an upright block, not as one full-height block with a cutout. "
        "For blocky orthographic parts, prefer box(..., centered=False) and explicit translations over mixed centered/default boxes. "
        "If user feedback conflicts with the drawing analysis, prefer a conservative assumption and record it."
    )
    repair_input = {
        "drawing_analysis": project.analysis.model_dump(),
        "parameters": {key: value.model_dump() for key, value in project.parameters.items()},
        "feature_summary": [item.model_dump() for item in project.feature_summary],
        "feature_graph": project.feature_graph.model_dump(),
        "feature_coverage": project.feature_coverage.model_dump(),
        "assumptions": project.assumptions,
        "error": project.generation.error,
        "error_feature_ids": _repair_error_feature_ids(project),
        "user_feedback": user_feedback,
        "current_view": current_view,
    }
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": prompt},
            {"role": "user", "content": json.dumps(repair_input, ensure_ascii=False)},
        ],
        "temperature": 0.1,
        "max_tokens": 5000,
        "response_format": {"type": "json_object"},
    }
    return await _chat_json(url, api_key, payload, "cad_generation")


def _repair_error_feature_ids(project: CADProject) -> List[str]:
    error = project.generation.error or {}
    detail = error.get("detail") if isinstance(error.get("detail"), dict) else {}
    candidates = [error.get("operation_id"), detail.get("operation_id")]
    candidates.extend(detail.get("feature_ids", []) if isinstance(detail.get("feature_ids"), list) else [])
    return list(
        dict.fromkeys(
            feature_id
            for value in candidates
            if (feature_id := _parameter_id(str(value or "")))
        )
    )


def apply_repair_plan(project: CADProject, plan: Dict[str, Any]) -> CADProject:
    operation_updates = plan.get("operation_updates", [])
    if not isinstance(operation_updates, list):
        raise GenerationError("cad_generation", "Repair operation_updates must be an array")
    if not operation_updates:
        raise GenerationError("cad_generation", "Repair did not include Feature Graph operation updates")

    repaired = project.model_copy(deep=True)
    repaired.generation_history.append(
        GenerationHistoryEntry(
            attempt=repaired.cad.generation_attempt,
            status=repaired.generation.status,
            cad_source=repaired.cad.source,
            error=repaired.generation.error,
            feature_graph=repaired.feature_graph.model_dump(),
            feature_coverage=repaired.feature_coverage.model_dump(),
            repair_feature_ids=[
                feature_id
                for value in plan.get("repaired_feature_ids", [])
                if (feature_id := _parameter_id(str(value)))
            ],
            render_artifacts={
                key: value.model_dump() for key, value in repaired.generation.render_artifacts.items()
            },
            visual_comparison=repaired.generation.visual_comparison.model_dump(),
        )
    )
    repaired.cad.generation_attempt += 1
    _apply_feature_operation_updates(repaired, operation_updates, plan.get("repaired_feature_ids", []))
    try:
        repaired = compile_project_feature_graph(repaired)
    except CompilerError as exc:
        raise GenerationError("cad_generation", f"Updated Feature Graph cannot compile: {exc}") from exc
    repaired.feature_coverage = _build_feature_coverage(repaired.analysis.features, repaired.feature_graph)
    if "assumptions" in plan:
        repaired.assumptions = [str(item) for item in plan.get("assumptions") or [] if str(item).strip()]
    if "feature_summary" in plan or "features" in plan:
        repaired.feature_summary = _normalize_features(plan.get("feature_summary", plan.get("features", [])))
    repaired.generation = GenerationResult(status="needs_review", warnings=[])
    repaired.updated_at = datetime.utcnow().isoformat() + "Z"
    return repaired


def _apply_feature_operation_updates(
    project: CADProject,
    updates: List[Any],
    repaired_feature_ids: Any,
) -> None:
    declared_ids = {
        _parameter_id(str(value))
        for value in repaired_feature_ids
        if _parameter_id(str(value))
    } if isinstance(repaired_feature_ids, list) else set()
    operations = {operation.id: operation for operation in project.feature_graph.operations}
    for raw_update in updates:
        if not isinstance(raw_update, dict):
            raise GenerationError("cad_generation", "Feature operation update must be an object")
        operation_id = _parameter_id(str(raw_update.get("id") or ""))
        if not operation_id or operation_id not in operations:
            raise GenerationError("cad_generation", f"Repair references unknown feature operation '{operation_id}'")
        if declared_ids and operation_id not in declared_ids:
            raise GenerationError(
                "cad_generation",
                f"Operation update '{operation_id}' is not declared in repaired_feature_ids",
            )
        merged = operations[operation_id].model_dump()
        merged.update(raw_update)
        merged["id"] = operation_id
        try:
            operations[operation_id] = FeatureOperation.model_validate(merged)
        except PydanticValidationError as exc:
            raise GenerationError(
                "cad_generation",
                f"Invalid repair for feature operation '{operation_id}': {exc.errors()[0]['msg']}",
            ) from exc
    project.feature_graph = FeatureGraph(operations=[operations[item.id] for item in project.feature_graph.operations])


def project_from_plan(
    plan: Dict[str, Any],
    analysis: Dict[str, Any],
    image_info: Dict[str, Any],
    image_data: bytes,
) -> CADProject:
    parameters = _normalize_parameters(plan.get("parameters", []))
    if not parameters:
        raise GenerationError("cad_generation", "CAD plan did not include parameters")

    feature_summary = _normalize_features(plan.get("feature_summary", plan.get("features", [])))
    analysis_features = _normalize_analysis_features(analysis.get("features"))
    feature_graph = _normalize_feature_graph(plan.get("feature_graph"), analysis_features)
    feature_coverage = _build_feature_coverage(analysis_features, feature_graph)
    assumptions = [str(item) for item in plan.get("assumptions", []) if str(item).strip()]
    source_info = SourceInfo(
        filename=image_info.get("filename", ""),
        mime_type=image_info.get("mime_type", ""),
        width=image_info.get("width"),
        height=image_info.get("height"),
    )
    if image_data:
        source_info.image_ref, source_info.image_sha256 = store_source_image(image_data)
    if os.environ.get("EASYCAD_INCLUDE_IMAGE_DATA") == "1":
        source_info.image_data = (
            f"data:{source_info.mime_type};base64,{base64.b64encode(image_data).decode('ascii')}"
        )

    now = datetime.utcnow().isoformat() + "Z"
    project = CADProject(
        id=str(uuid4()),
        title=str(plan.get("title") or analysis.get("title") or "Generated CAD project"),
        units=str(plan.get("units") or analysis.get("units") or "mm"),
        source=source_info,
        analysis=DrawingAnalysis(
            views=_normalize_dict_items(analysis.get("views"), "view"),
            dimensions=_normalize_dict_items(analysis.get("dimensions"), "dimension"),
            features=analysis_features,
            uncertainties=_normalize_dict_items(analysis.get("uncertainties"), "uncertainty"),
        ),
        parameters=parameters,
        feature_graph=feature_graph,
        feature_coverage=feature_coverage,
        feature_summary=feature_summary,
        assumptions=assumptions,
        cad=CADSource(source="", source_kind="compiled"),
        generation=GenerationResult(status="needs_review", warnings=[]),
        created_at=now,
        updated_at=now,
    )
    try:
        return compile_project_feature_graph(project)
    except CompilerError as exc:
        raise GenerationError("cad_generation", f"Feature Graph cannot compile: {exc}", {"operation_id": exc.operation_id}) from exc


def _normalize_parameters(raw: Any) -> Dict[str, CADParameter]:
    if isinstance(raw, dict):
        items = [{"id": key, **value} for key, value in raw.items() if isinstance(value, dict)]
    elif isinstance(raw, list):
        items = [item for item in raw if isinstance(item, dict)]
    else:
        items = []

    parameters: Dict[str, CADParameter] = {}
    for item in items:
        key = _parameter_id(str(item.get("id") or item.get("name") or ""))
        if not key:
            continue
        payload = dict(item)
        payload.pop("id", None)
        payload.pop("name", None)
        payload.setdefault("label", key.replace("_", " ").title())
        payload.setdefault("unit", "mm")
        payload.setdefault("step", 0.1)
        if payload.get("step") is None:
            payload["step"] = 0.1
        payload["source"] = _normalize_parameter_source(str(payload.get("source") or "manual"))
        declared_type = str(payload.get("type") or "").strip()
        payload.setdefault("editable", payload.get("type") != "expression")
        if payload.get("expression"):
            payload["type"] = "expression"
        elif key == "text_content":
            payload["type"] = "text"
            payload.setdefault("unit", "")
        elif key == "text_mode":
            payload["type"] = "choice"
            payload.setdefault("unit", "")
            payload.setdefault("options", ["none", "engrave", "emboss"])
            if str(payload.get("value") or "").lower() in {"engraved", "recessed", "cut"}:
                payload["value"] = "engrave"
            elif str(payload.get("value") or "").lower() in {"embossed", "raised"}:
                payload["value"] = "emboss"
            elif not payload.get("value"):
                payload["value"] = "none"
        elif not declared_type and isinstance(payload.get("value"), str):
            payload["type"] = "text"
            payload.setdefault("unit", "")
        else:
            payload["type"] = _normalize_parameter_type(str(payload.get("type") or "number"))
        try:
            parameters[key] = CADParameter.model_validate(payload)
        except PydanticValidationError as exc:
            raise GenerationError("cad_generation", f"Invalid parameter '{key}': {exc.errors()[0]['msg']}") from exc
    return parameters


def _normalize_parameter_type(value: str) -> str:
    value = value.strip().lower()
    if value in {"number", "float", "integer", "int", "double", "decimal"}:
        return "number"
    if value in {"expression", "derived", "formula"}:
        return "expression"
    if value in {"text", "string", "str"}:
        return "text"
    if value in {"choice", "select", "enum"}:
        return "choice"
    return value


def _normalize_parameter_source(value: str) -> str:
    value = value.strip().lower()
    aliases = {
        "visible_annotation": "drawing",
        "annotation": "drawing",
        "dimension": "drawing",
        "calculated": "derived",
        "estimated": "inferred",
        "estimate": "inferred",
        "default": "assumed",
    }
    value = aliases.get(value, value)
    if value in {"drawing", "derived", "inferred", "assumed", "manual"}:
        return value
    return "manual"


def _has_cad_plan_shape(payload: Dict[str, Any]) -> bool:
    return (
        isinstance(payload.get("parameters"), (list, dict))
        and isinstance(payload.get("feature_graph"), (list, dict))
    )


def _normalize_features(raw: Any) -> List[FeatureSummary]:
    if not isinstance(raw, list):
        return []
    features: List[FeatureSummary] = []
    for idx, item in enumerate(raw, start=1):
        if not isinstance(item, dict):
            continue
        feature_id = _parameter_id(str(item.get("id") or f"feature_{idx}")) or f"feature_{idx}"
        description = str(item.get("description") or item.get("name") or item.get("type") or "Generated feature")
        features.append(
            FeatureSummary(
                id=feature_id,
                name=str(item.get("name") or description),
                type=str(item.get("type") or "feature"),
                description=description,
            )
        )
    return features


def _normalize_dict_items(raw: Any, item_type: str) -> List[Dict[str, Any]]:
    if isinstance(raw, dict):
        converted = []
        for key, value in raw.items():
            item_id = _parameter_id(str(key)) or f"{item_type}_{len(converted) + 1}"
            if isinstance(value, dict):
                converted.append({"id": item_id, **value})
            elif item_type == "dimension":
                converted.append({"id": item_id, "value": value})
            else:
                converted.append({"id": item_id, "description": str(value)})
        return converted
    if not isinstance(raw, list):
        return []
    items: List[Dict[str, Any]] = []
    for idx, item in enumerate(raw, start=1):
        if isinstance(item, dict):
            items.append(item)
        elif isinstance(item, str):
            items.append({"id": f"{item_type}_{idx}", "description": item})
    return items


def normalize_drawing_analysis(raw: Dict[str, Any]) -> Dict[str, Any]:
    normalized = dict(raw)
    normalized["views"] = _normalize_dict_items(raw.get("views"), "view")
    normalized["dimensions"] = _normalize_dict_items(raw.get("dimensions"), "dimension")
    normalized["features"] = _normalize_analysis_features(raw.get("features"))
    normalized["uncertainties"] = _normalize_dict_items(raw.get("uncertainties"), "uncertainty")
    return normalized


def _normalize_feature_graph(raw: Any, analysis_features: List[Dict[str, Any]]) -> FeatureGraph:
    if isinstance(raw, dict):
        raw_operations = raw.get("operations", [])
    elif isinstance(raw, list):
        raw_operations = raw
    else:
        raw_operations = []

    operations = []
    mapped_feature_ids = set()
    for idx, item in enumerate(raw_operations, start=1):
        if not isinstance(item, dict):
            continue
        payload = dict(item)
        operation_id = _parameter_id(str(payload.get("id") or f"operation_{idx}")) or f"operation_{idx}"
        payload["id"] = operation_id
        payload["type"] = str(payload.get("type") or payload.pop("kind", None) or "feature")
        payload["operation"] = _normalize_feature_operation(str(payload.get("operation") or "add"))
        if payload.get("target"):
            payload["target"] = _parameter_id(str(payload["target"]))
        dependencies = payload.get("depends_on", [])
        if isinstance(dependencies, str):
            dependencies = [dependencies]
        payload["depends_on"] = [
            dependency_id
            for dependency in dependencies
            if (dependency_id := _parameter_id(str(dependency)))
        ]
        source_ids = payload.get("source_feature_ids", payload.pop("source_features", []))
        if isinstance(source_ids, str):
            source_ids = [source_ids]
        payload["source_feature_ids"] = [
            source_id for source in source_ids if (source_id := _parameter_id(str(source)))
        ]
        if operation_id in {str(feature.get("id")) for feature in analysis_features}:
            payload["source_feature_ids"].append(operation_id)
        payload["source_feature_ids"] = list(dict.fromkeys(payload["source_feature_ids"]))
        mapped_feature_ids.update(payload["source_feature_ids"])
        operations.append(payload)

    for feature in analysis_features:
        feature_id = str(feature.get("id") or "")
        if not feature_id or feature_id in mapped_feature_ids:
            continue
        operation = _normalize_feature_operation(str(feature.get("operation_hint") or "add"))
        target = _parameter_id(str(feature.get("target") or "")) or None
        dependencies = feature.get("depends_on", [])
        if isinstance(dependencies, str):
            dependencies = [dependencies]
        operations.append(
            {
                "id": feature_id,
                "name": str(feature.get("name") or feature_id.replace("_", " ").title()),
                "type": str(feature.get("type") or "feature"),
                "operation": operation,
                "target": target,
                "depends_on": [
                    dependency_id
                    for dependency in dependencies
                    if (dependency_id := _parameter_id(str(dependency)))
                ],
                "source_feature_ids": [feature_id],
                "evidence": _normalize_feature_evidence(feature.get("evidence")),
                "confidence": _normalize_confidence(feature.get("confidence")),
                "status": "unresolved",
                "capability_status": "unsupported",
                "assumption": "No explicit Feature Graph operation was supplied by the CAD planner.",
            }
        )

    try:
        return FeatureGraph.model_validate({"operations": operations})
    except PydanticValidationError as exc:
        raise GenerationError("cad_generation", f"Invalid Feature Graph: {exc.errors()[0]['msg']}") from exc


def _build_feature_coverage(
    analysis_features: List[Dict[str, Any]], feature_graph: FeatureGraph
) -> FeatureCoverageReport:
    operations_by_feature: Dict[str, List[Any]] = {}
    for operation in feature_graph.operations:
        for feature_id in operation.source_feature_ids:
            operations_by_feature.setdefault(feature_id, []).append(operation)

    entries = []
    all_accounted_for = True
    has_unresolved = False
    status_priority = {
        "unsupported": 5,
        "unresolved": 4,
        "approximated": 3,
        "planned": 2,
        "implemented": 1,
    }
    for feature in analysis_features:
        feature_id = str(feature.get("id") or "")
        if not feature_id:
            continue
        operations = operations_by_feature.get(feature_id, [])
        if operations:
            status = max((operation.status for operation in operations), key=status_priority.get)
            explanation = next(
                (operation.assumption for operation in operations if operation.assumption),
                None,
            )
        else:
            status = "unresolved"
            explanation = "No Feature Graph operation covers this drawing feature."
            all_accounted_for = False
        if status in {"planned", "unresolved", "unsupported"}:
            has_unresolved = True
        entries.append(
            {
                "feature_id": feature_id,
                "operation_ids": [operation.id for operation in operations],
                "status": status,
                "confidence": _normalize_confidence(feature.get("confidence")),
                "explanation": explanation,
            }
        )
    return FeatureCoverageReport.model_validate(
        {
            "entries": entries,
            "all_accounted_for": all_accounted_for,
            "has_unresolved": has_unresolved,
        }
    )


def _normalize_feature_operation(value: str) -> str:
    aliases = {
        "additive": "add",
        "union": "add",
        "subtract": "cut",
        "subtractive": "cut",
        "hole": "cut",
        "boolean_cut": "cut",
        "fillet": "modify",
        "chamfer": "modify",
        "shell": "modify",
    }
    value = aliases.get(value.strip().lower(), value.strip().lower())
    return value if value in {"add", "cut", "intersect", "modify", "pattern"} else "add"


def _normalize_feature_evidence(raw: Any) -> Dict[str, Any]:
    if not isinstance(raw, dict):
        return {}
    return {
        "views": [str(item) for item in raw.get("views", []) if str(item).strip()],
        "dimension_ids": [str(item) for item in raw.get("dimension_ids", []) if str(item).strip()],
        "source": str(raw.get("source") or "inferred"),
        "note": str(raw["note"]) if raw.get("note") else None,
    }


def _normalize_confidence(value: Any) -> float:
    try:
        return min(1.0, max(0.0, float(value)))
    except (TypeError, ValueError):
        return 0.5


def _normalize_analysis_features(raw: Any) -> List[Dict[str, Any]]:
    if not isinstance(raw, list):
        return []

    source_items = []
    assigned_ids: List[str] = []
    aliases: Dict[str, str] = {}
    counts: Dict[str, int] = {}
    for idx, item in enumerate(raw, start=1):
        if isinstance(item, str):
            payload = {"description": item}
        elif isinstance(item, dict):
            payload = dict(item)
        else:
            continue

        candidates = (
            payload.get("id"),
            payload.get("name"),
            payload.get("label"),
            payload.get("type"),
            payload.get("description"),
        )
        base_id = next(
            (_parameter_id(str(value)) for value in candidates if value and _parameter_id(str(value))),
            "",
        )
        base_id = base_id or f"feature_{idx}"
        counts[base_id] = counts.get(base_id, 0) + 1
        feature_id = base_id if counts[base_id] == 1 else f"{base_id}_{counts[base_id]}"
        assigned_ids.append(feature_id)
        source_items.append(payload)

        for value in candidates:
            alias = _parameter_id(str(value or ""))
            if alias and alias not in aliases:
                aliases[alias] = feature_id

    normalized = []
    for payload, feature_id in zip(source_items, assigned_ids):
        payload["id"] = feature_id
        raw_target = (
            payload.pop("host_feature", None)
            or payload.pop("host", None)
            or payload.pop("parent", None)
            or payload.get("target")
        )
        if raw_target:
            target_id = _parameter_id(str(raw_target))
            payload["target"] = aliases.get(target_id, target_id)

        raw_dependencies = payload.get("depends_on", payload.pop("dependencies", []))
        if isinstance(raw_dependencies, str):
            raw_dependencies = [raw_dependencies]
        if isinstance(raw_dependencies, list):
            payload["depends_on"] = [
                aliases.get(reference_id, reference_id)
                for reference in raw_dependencies
                if (reference_id := _parameter_id(str(reference)))
            ]
        normalized.append(payload)
    return normalized


def _parameter_id(value: str) -> str:
    value = re.sub(r"[^a-zA-Z0-9_]+", "_", value.strip().lower()).strip("_")
    if not value or not re.match(r"^[a-zA-Z]", value):
        return ""
    return value


def _log_model_response(
    stage: str,
    request_payload: Dict[str, Any],
    attempt: int,
    status_code: int,
    content: Any,
    response_payload: Any,
) -> None:
    record = {
        "created_at": datetime.utcnow().isoformat() + "Z",
        "stage": stage,
        "attempt": attempt,
        "status_code": status_code,
        "model": request_payload.get("model"),
        "content": content,
        "response": response_payload,
    }
    try:
        LLM_LOG_DIR.mkdir(parents=True, exist_ok=True)
        with (LLM_LOG_DIR / "llm_responses.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    except OSError as exc:
        logger.warning("Failed to write LLM response log: %s", exc)

    logger.warning(
        "LLM response stage=%s model=%s attempt=%s status=%s content=%s",
        stage,
        request_payload.get("model"),
        attempt,
        status_code,
        content,
    )


async def _chat_json(url: str, api_key: str, payload: Dict[str, Any], stage: str) -> Dict[str, Any]:
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "http://localhost:8852",
        "X-Title": "EasyCAD",
    }
    last_content = ""
    for attempt in range(2):
        request_payload = dict(payload)
        if attempt:
            request_payload["messages"] = list(payload.get("messages", [])) + [
                {
                    "role": "user",
                    "content": "Return only one valid JSON object. No Markdown, no prose, no arrays.",
                }
            ]
        try:
            async with httpx.AsyncClient(timeout=120) as client:
                response = await client.post(url, headers=headers, json=request_payload)
        except httpx.HTTPError as exc:
            raise GenerationError(stage, f"Provider request failed: {exc}") from exc

        if response.status_code >= 400:
            message = _provider_error_message(response.text) or f"Provider returned HTTP {response.status_code}"
            _log_model_response(
                stage,
                request_payload,
                attempt + 1,
                response.status_code,
                response.text,
                {"error_body": response.text},
            )
            raise GenerationError(stage, message, {"status_code": response.status_code, "body": response.text[:1000]})

        try:
            response_payload = response.json()
            content = response_payload["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
            _log_model_response(
                stage,
                request_payload,
                attempt + 1,
                response.status_code,
                response.text,
                {"schema_error": True},
            )
            raise GenerationError(stage, "Provider response did not match chat completion schema") from exc
        _log_model_response(stage, request_payload, attempt + 1, response.status_code, content, response_payload)
        try:
            return _parse_json_object(content, stage)
        except GenerationError:
            last_content = str(content)
    raise GenerationError(stage, "Provider did not return a JSON object", {"content": last_content[:1000]})


def _parse_json_object(content: Any, stage: str) -> Dict[str, Any]:
    if isinstance(content, dict):
        return content
    if not isinstance(content, str):
        raise GenerationError(stage, "Provider returned non-JSON content")
    content = content.strip()
    if content.startswith("```"):
        content = re.sub(r"^```(?:json|python)?\s*", "", content)
        content = re.sub(r"\s*```$", "", content).strip()
    try:
        parsed = json.loads(content)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass

    decoder = json.JSONDecoder()
    for index, char in enumerate(content):
        if char != "{":
            continue
        try:
            parsed, _ = decoder.raw_decode(content[index:])
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            continue
    raise GenerationError(stage, "Provider did not return a JSON object", {"content": content[:1000]})


def normalize_model_id(model: str) -> str:
    return MODEL_ALIASES.get(model.strip(), model.strip())


def _provider_error_message(body: str) -> str:
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return ""
    error = payload.get("error")
    if isinstance(error, dict):
        return str(error.get("message") or "")
    return ""
