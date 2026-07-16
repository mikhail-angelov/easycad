from __future__ import annotations

import base64
import json
import logging
import os
import re
import warnings
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import uuid4

import httpx
from fastapi import HTTPException
from PIL import Image, UnidentifiedImageError
from pydantic import ValidationError as PydanticValidationError

from .models import (
    CADProject,
    DraftSpecification,
    SourceInfo,
    VisualComparison,
)
from .feature_compiler import (
    OPERATION_CONTRACTS,
    draft_operation_contract_descriptions,
    draft_specification_operation_types,
)
from .draft_geometry_rules import draft_geometry_rules
from .source_images import get_source_image, store_source_image
from .draft_builder import DraftBuilder


MAX_UPLOAD_BYTES = 20 * 1024 * 1024
MAX_IMAGE_DIMENSION = 12000
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
LLM_LOG_DIR = Path(os.environ.get("EASYCAD_LLM_LOG_DIR", "logs"))
MAX_DRAFT_PLANNER_TURNS = 2
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

        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as image:
                image.verify()
                width, height = image.size
                detected_format = (image.format or "").lower()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise HTTPException(400, "Invalid image")

    if width > MAX_IMAGE_DIMENSION or height > MAX_IMAGE_DIMENSION:
        raise HTTPException(400, "Image dimensions are too large")

    detected_mime = {
        "jpeg": "image/jpeg",
        "png": "image/png",
        "webp": "image/webp",
    }.get(detected_format, mime_type)
    if mime_type and detected_mime and mime_type != detected_mime:
        raise HTTPException(400, "Image content does not match MIME type")
    return {
        "filename": filename,
        "mime_type": detected_mime or mime_type,
        "width": width,
        "height": height,
    }


async def generate_draft_specification_from_image(
    data: bytes, filename: str, mime_type: str, instructions: str = ""
) -> DraftSpecification:
    image_info = validate_image_upload(data, filename, mime_type)
    openrouter_key = os.environ.get("OPEN_ROUTER_KEY")
    deepseek_key = os.environ.get("DEEP_SEEK_KEY")
    if not openrouter_key:
        raise GenerationError("vision_analysis", "OPEN_ROUTER_KEY is not configured")
    if not deepseek_key and not os.environ.get("OPEN_ROUTER_PLANNER_MODEL"):
        raise GenerationError("draft_specification", "DEEP_SEEK_KEY is not configured")
    analysis = await analyze_drawing(data, image_info["mime_type"], instructions, openrouter_key)
    draft = await plan_draft_specification(analysis, instructions, deepseek_key)
    image_ref, image_sha256 = store_source_image(data)
    draft.source = SourceInfo(
        filename=image_info.get("filename", ""),
        mime_type=image_info.get("mime_type", ""),
        width=image_info.get("width"),
        height=image_info.get("height"),
        image_ref=image_ref,
        image_sha256=image_sha256,
    )
    return draft


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


async def plan_draft_specification(
    analysis: Dict[str, Any],
    instructions: str,
    api_key: str,
    *,
    previous_specification: DraftSpecification | None = None,
    user_inputs: Dict[str, Any] | None = None,
) -> DraftSpecification:
    """Convert image observations into an editable pre-CAD specification."""
    openrouter_planner_model = os.environ.get("OPEN_ROUTER_PLANNER_MODEL", "").strip()
    if openrouter_planner_model:
        model = normalize_model_id(openrouter_planner_model)
        url = OPENROUTER_URL
        api_key = os.environ.get("OPEN_ROUTER_KEY", "")
        if not api_key:
            raise GenerationError("draft_specification", "OPEN_ROUTER_KEY is not configured")
    else:
        model = normalize_model_id(os.environ.get("DEEP_SEEK_MODEL", os.environ.get("DEEPSEEK_MODEL", "deepseek-chat")))
        url = os.environ.get("DEEP_SEEK_BASE_URL", "https://api.deepseek.com/chat/completions")
    prompt = (
        "Build a DraftSpecification by calling the supplied tools until finish_draft. Do not return CAD code, Feature Graph, or JSON prose. "
        "Start with set_draft_metadata immediately. Do not spend a response explaining or reasoning about the drawing before making a tool call. "
        "You operate a server-owned builder: call set_draft_metadata exactly once; add each dimension exactly once; add each feature once with its matching add_<type> tool; then add assumptions, questions, annotations, and call finish_draft exactly once. "
        "After a tool result with ok=true, that item is stored: never call that tool for the same ID again. After ok=false, correct and retry only that item. "
        "When all intended items have ok=true results, call finish_draft immediately. Never omit finish_draft and never continue after it. "
        "Each dimension requires id, label, value or expression, unit, source, confidence, status, critical, evidence. "
        "Each feature requires id, label, type, operation, target, parameters, placement, status, critical_fields, confidence, evidence. "
        f"Feature type must be exactly one of these draft-compatible compiler types: {draft_specification_operation_types()}. "
        "The vision-analysis terms body and groove are observations, not valid feature types: choose a supported type that represents them, "
        "such as box or cylinder, or mark the feature unsupported when no trusted type can represent it. "
        "Use only these compiler contracts (the tool schema enforces them):\n"
        + draft_operation_contract_descriptions()
        + "\n"
        "Every string used in parameters, profile dimensions, pattern numeric fields, or origin coordinates must be the ID of a declared dimension; "
        "put literal text such as engraving content in a declared text dimension and reference its ID. "
        "hole/through_hole need diameter, depth; counterbore needs diameter, depth, bore_diameter, bore_depth; "
        "A drawing label that says a hole is through/сквозное is a confirmed instruction to cut through its target thickness; do not ask about it again. "
        "countersink needs diameter, depth, sink_diameter, sink_depth; slot/pocket need length, width, depth; "
        "rib needs length, thickness, height; fillet needs radius; chamfer needs distance; shell needs thickness. "
        + draft_geometry_rules()
        + "\n"
        "Build one connected solid: the first feature is the only root; every additive feature after the first MUST set target to the existing root body, and every cut MUST target that same connected body. "
        "Feature placement may contain only reference, plane, origin, axis, direction, rotation_deg, and offsets. "
        "For fillet and chamfer, target is the feature ID; placement.reference is optional CadQuery edge-selector text such as '>Z', "
        "never a feature ID. Omit reference when the selected edges are not known. "
        "Use origin as exactly three numeric values or dimension IDs for translation, never expressions; never use offset, center, position, depth, or centered_on_width. "
        "Use status confirmed only for unambiguous observed values. Use needs_input for missing critical data, conflicted for contradictions, "
        "and assumed only with an assumption describing the proposal. Every missing size, position, target, cut direction, or depth needed "
        "for a printable feature must become a required question. Never silently invent a dimension. "
        "Annotations use normalized x and y coordinates from 0 to 1 and link to a dimension, feature, or question. "
    )
    if previous_specification is not None:
        prompt += (
            "Return a complete replacement DraftSpecification, not a patch. The previous specification is reference context only; "
            "use the drawing analysis and user inputs to resolve it again. Return every previous dimension, feature, and assumption: "
            "never delete an existing item or return an empty graph. Keep their IDs and proposed geometry unless user input changes them, "
            "so the review UI remains connected. Treat user inputs as the latest clarification. "
            "User input contract: dimension_values are direct user-entered facts and must be returned as confirmed; "
            "accepted_assumption_ids and accepted_feature_ids are explicit user approvals and their matching items must be returned "
            "with status confirmed. An accepted assumption is an authoritative answer for every feature and dimension in its affected_ids; "
            "apply it to the corresponding placement or parameter fields and keep those features confirmed. "
            "A clarification linked to a question overrides an earlier proposal for that question's field_id, including a whole feature and "
            "its placement or parameters. Apply that clarification even when the user also accepted the earlier proposal; the clarification "
            "is the newer, more specific input. "
            "An accepted feature is an authoritative approval of its proposed critical fields: do not downgrade it to needs_input or assumed, "
            "and do not ask a new question about any of its proposed geometry. Do not return a question that is answered by a direct value "
            "or an accepted proposal. "
            "Return every previous question that is still unresolved, preserving its ID and prompt. Remove a previous question only when a direct "
            "user value, clarification, or accepted proposal actually answers it. Only create a new question when the user inputs still leave a necessary modelling fact unresolved."
        )
        prompt += (
            " A clarification with key build_repair is a deterministic build diagnostic supplied to the user: it overrides "
            "any earlier accepted feature geometry named in that clarification. Correct the complete replacement graph accordingly "
            "and do not repeat the failed placement."
        )
    if instructions.strip():
        prompt += f"\nUser instructions: {instructions.strip()}"
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": prompt},
            {"role": "user", "content": json.dumps({
                "drawing_analysis": analysis,
                "previous_specification": previous_specification.model_dump(mode="json") if previous_specification else None,
                "user_inputs": user_inputs or {},
            }, ensure_ascii=False)},
        ],
        "temperature": 0.1,
        "max_tokens": 20000 if openrouter_planner_model else 5000,
        "tools": draft_builder_tools(),
        "tool_choice": "required",
    }
    builder_kwargs = {
        "required_dimension_ids": {item.id for item in previous_specification.dimensions} if previous_specification else set(),
        "required_feature_ids": {item.id for item in previous_specification.features} if previous_specification else set(),
        "required_assumption_ids": {item.id for item in previous_specification.assumptions} if previous_specification else set(),
        **_confirmed_replan_snapshots(previous_specification, user_inputs or {}),
    }
    return await _run_draft_builder(
        url,
        api_key,
        payload,
        analysis,
        planner_run_id=uuid4().hex[:12],
        planner_mode="replan" if previous_specification else "initial",
        **builder_kwargs,
    )


def _confirmed_replan_snapshots(
    previous_specification: DraftSpecification | None, user_inputs: Dict[str, Any]
) -> Dict[str, Dict[str, Dict[str, Any]]]:
    if previous_specification is None:
        return {
            "preserved_dimensions": {},
            "preserved_features": {},
            "preserved_assumptions": {},
        }
    questions = {item.id: item.field_id for item in previous_specification.questions}
    clarified_fields = {
        questions[question_id]
        for question_id, text in user_inputs.get("clarifications", {}).items()
        if text and question_id in questions
    }

    def superseded(item_id: str) -> bool:
        return any(field_id == item_id or field_id.startswith(f"{item_id}.") for field_id in clarified_fields)

    dimensions = {}
    direct_values = user_inputs.get("dimension_values", {})
    for item in previous_specification.dimensions:
        if item.status != "confirmed" or superseded(item.id):
            continue
        payload = item.model_dump(mode="json")
        if item.id in direct_values:
            payload.update({"value": direct_values[item.id], "expression": None, "status": "confirmed", "source": "manual"})
        dimensions[item.id] = payload
    accepted_features = set(user_inputs.get("accepted_feature_ids", []))
    features = {
        item.id: {**item.model_dump(mode="json"), "status": "confirmed" if item.id in accepted_features else item.status}
        for item in previous_specification.features
        if (item.status == "confirmed" or item.id in accepted_features) and not superseded(item.id)
    }
    accepted_assumptions = set(user_inputs.get("accepted_assumption_ids", []))
    assumptions = {
        item.id: {**item.model_dump(mode="json"), "status": "confirmed" if item.id in accepted_assumptions else item.status}
        for item in previous_specification.assumptions
        if (item.status == "confirmed" or item.id in accepted_assumptions) and not superseded(item.id)
    }
    return {
        "preserved_dimensions": dimensions,
        "preserved_features": features,
        "preserved_assumptions": assumptions,
    }


def draft_specification_tool_schema() -> Dict[str, Any]:
    """Return the provider schema for a complete, buildable draft response."""
    schema = DraftSpecification.model_json_schema()
    schema["properties"] = {
        key: schema["properties"][key]
        for key in ("title", "units", "dimensions", "features", "assumptions", "questions", "annotations")
    }
    schema["properties"]["features"]["minItems"] = 1
    schema["$defs"]["SpecificationFeature"] = _draft_feature_schema()
    return schema


def _draft_feature_schema() -> Dict[str, Any]:
    """Discriminated feature schema generated from the compiler operation registry."""
    value_schema = {"anyOf": [{"type": "string"}, {"type": "number"}, {"type": "integer"}, {"type": "boolean"}]}
    common = {
        "id": {"type": "string", "pattern": "^[a-z][a-z0-9_]*$"},
        "label": {"type": "string"},
        "target": {"anyOf": [{"type": "string"}, {"type": "null"}]},
        "placement": {"$ref": "#/$defs/FeaturePlacement"},
        "status": {"enum": ["confirmed", "needs_input", "assumed", "conflicted"]},
        "critical_fields": {"type": "array", "items": {"type": "string"}},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "evidence": {"type": "array", "items": {"type": "string"}},
        "alternatives": {"type": "object", "additionalProperties": {"type": "array", "items": value_schema}},
    }
    variants = []
    for feature_type, contract in OPERATION_CONTRACTS.items():
        properties = {
            **common,
            "type": {"const": feature_type},
            "operation": {"enum": list(contract.allowed_operations)},
            "parameters": _parameter_schema(contract.required_parameters, contract.optional_parameters, value_schema),
        }
        required = ["id", "label", "type", "operation", "target", "parameters", "placement", "status", "critical_fields", "confidence", "evidence", "alternatives"]
        if contract.requires_profile:
            properties["profile"] = {
                **_profile_schema(contract.profile_types, value_schema),
                "description": (
                    f"REQUIRED for {feature_type}. Supply one complete profile; allowed types: "
                    f"{', '.join(contract.profile_types)}. An extrude without this field is invalid."
                ),
            }
            required.append("profile")
        if contract.requires_pattern:
            properties["pattern"] = _pattern_schema(contract.pattern_types, value_schema)
            required.append("pattern")
        variants.append({"type": "object", "additionalProperties": False, "properties": properties, "required": required})
    return {"oneOf": variants}


def _parameter_schema(required: tuple[str, ...], optional: tuple[str, ...], value_schema: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {name: value_schema for name in required + optional},
        "required": list(required),
    }


def _profile_schema(profile_types: tuple[str, ...], value_schema: Dict[str, Any]) -> Dict[str, Any]:
    dimensions = {"rectangle": ("width", "height"), "circle": ("diameter",), "slot": ("length", "width"), "polyline": ()}
    variants = []
    for profile_type in profile_types:
        dimension_names = dimensions[profile_type]
        points = {"type": "array", "items": {"type": "array", "items": value_schema, "minItems": 2, "maxItems": 2}}
        if profile_type == "polyline":
            points["minItems"] = 3
        variants.append(
            {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "type": {"const": profile_type},
                    "dimensions": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {name: value_schema for name in dimension_names},
                        "required": list(dimension_names),
                    },
                    "points": points,
                },
                "required": ["type", "dimensions", "points"],
            }
        )
    return {"oneOf": variants}


def _pattern_schema(pattern_types: tuple[str, ...], value_schema: Dict[str, Any]) -> Dict[str, Any]:
    variants = []
    for pattern_type in pattern_types:
        required = ("count", "pitch", "axis") if pattern_type == "linear" else ("count", "angle_deg", "axis")
        optional = ("start_margin", "end_margin") if pattern_type == "linear" else ("start_margin", "end_margin")
        variants.append(
            {
                "type": "object",
                "additionalProperties": False,
                "properties": {"type": {"const": pattern_type}, **{name: value_schema for name in required + optional}},
                "required": ["type", *required],
            }
        )
    return {"oneOf": variants}


def draft_builder_tools() -> List[Dict[str, Any]]:
    schema = DraftSpecification.model_json_schema()["$defs"]
    feature_variants = _draft_feature_schema()["oneOf"]
    # Each function schema is sent independently.  Keep the shared definitions
    # alongside it so the provider can resolve FeaturePlacement references.
    def with_definitions(parameters: Dict[str, Any]) -> Dict[str, Any]:
        return {**parameters, "$defs": schema}

    tools = [
        {"type": "function", "function": {"name": "set_draft_metadata", "parameters": {"type": "object", "additionalProperties": False, "required": ["title", "units"], "properties": {"title": {"type": "string"}, "units": {"const": "mm"}}}}},
        {"type": "function", "function": {"name": "add_dimension", "parameters": with_definitions(schema["SpecificationDimension"])}},
        {"type": "function", "function": {"name": "add_assumption", "parameters": {"type": "object", "additionalProperties": False, "required": ["id", "value", "rationale", "affected_ids", "status"], "properties": {"id": {"type": "string"}, "value": {"anyOf": [{"type": "number"}, {"type": "string"}]}, "rationale": {"type": "string"}, "affected_ids": {"type": "array", "items": {"type": "string"}}, "status": {"enum": ["assumed", "confirmed"]}}}}},
        {"type": "function", "function": {"name": "add_question", "parameters": with_definitions(schema["SpecificationQuestion"])}},
        {"type": "function", "function": {"name": "add_annotation", "parameters": with_definitions(schema["SpecificationAnnotation"])}},
        {"type": "function", "function": {"name": "finish_draft", "parameters": {"type": "object", "additionalProperties": False, "properties": {}}}},
    ]
    for variant in feature_variants:
        feature_type = variant["properties"]["type"]["const"]
        tools.append({"type": "function", "function": {"name": f"add_{feature_type}", "description": f"Add exactly one {feature_type} feature.", "parameters": with_definitions(variant)}})
    return tools


async def _run_draft_builder(
    url: str,
    api_key: str,
    payload: Dict[str, Any],
    analysis: Dict[str, Any],
    *,
    required_dimension_ids: set[str] | None = None,
    required_feature_ids: set[str] | None = None,
    required_assumption_ids: set[str] | None = None,
    preserved_dimensions: Dict[str, Dict[str, Any]] | None = None,
    preserved_features: Dict[str, Dict[str, Any]] | None = None,
    preserved_assumptions: Dict[str, Dict[str, Any]] | None = None,
    planner_run_id: str | None = None,
    planner_mode: str = "initial",
) -> DraftSpecification:
    builder = DraftBuilder(analysis)
    messages = list(payload["messages"])
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://easycad.local",
        "X-Title": "EasyCAD",
    }
    last_results: List[Dict[str, Any]] = []
    for turn in range(1, MAX_DRAFT_PLANNER_TURNS + 1):
        try:
            async with httpx.AsyncClient(timeout=120) as client:
                response = await client.post(url, headers=headers, json={**payload, "messages": messages})
        except httpx.HTTPError as exc:
            detail = {
                "exception_type": type(exc).__name__,
                "exception_message": str(exc),
                "planner_run_id": planner_run_id,
                "planner_mode": planner_mode,
            }
            logger.exception("LLM request failed stage=draft_specification detail=%s", detail)
            raise GenerationError("draft_specification", "Provider request failed", detail) from exc
        if response.status_code >= 400:
            detail = {
                "status_code": response.status_code,
                "response": response.text[:4000],
                "planner_run_id": planner_run_id,
                "planner_mode": planner_mode,
            }
            logger.error("LLM request failed stage=draft_specification detail=%s", detail)
            raise GenerationError("draft_specification", f"Provider returned HTTP {response.status_code}", detail)
        try:
            response_payload = response.json()
            message = response_payload["choices"][0]["message"]
        except (KeyError, IndexError, ValueError, TypeError) as exc:
            detail = {
                "response": response.text[:4000],
                "exception_type": type(exc).__name__,
                "planner_run_id": planner_run_id,
                "planner_mode": planner_mode,
            }
            logger.exception("LLM response was malformed stage=draft_specification detail=%s", detail)
            raise GenerationError("draft_specification", "Provider returned a malformed tool response", detail) from exc
        calls = message.get("tool_calls", [])
        _log_model_response(
            "draft_specification",
            {**payload, "messages": messages},
            turn,
            response.status_code,
            message.get("content", ""),
            response_payload,
            planner_run_id=planner_run_id,
            planner_mode=planner_mode,
        )
        if not calls:
            raise GenerationError("draft_specification", "Planner stopped before finish_draft")
        messages.append(message)
        for call in calls:
            name = call["function"]["name"]
            raw_arguments = call["function"].get("arguments", "{}")
            try:
                args = _parse_json_object(raw_arguments, "draft_specification")
            except GenerationError as exc:
                messages.append({"role": "tool", "tool_call_id": call["id"], "content": json.dumps({"ok": False, "message": str(exc)})})
                continue
            if name == "finish_draft":
                if not builder.metadata_set:
                    result = {"ok": False, "field": "title", "message": "set_draft_metadata must be called before finish_draft"}
                elif not builder.draft.features:
                    result = {"ok": False, "field": "features", "message": "draft must contain at least one feature before finish_draft"}
                else:
                    missing_dimensions = sorted((required_dimension_ids or set()) - {item.id for item in builder.draft.dimensions})
                    missing_features = sorted((required_feature_ids or set()) - {item.id for item in builder.draft.features})
                    missing_assumptions = sorted((required_assumption_ids or set()) - {item.id for item in builder.draft.assumptions})
                    if missing_dimensions or missing_features or missing_assumptions:
                        result = {
                            "ok": False,
                            "field": "complete_replan",
                            "message": (
                                "Complete replacement draft is missing previously reviewed IDs. Add them before finish_draft: "
                                f"dimensions={missing_dimensions}; features={missing_features}; assumptions={missing_assumptions}."
                            ),
                        }
                    else:
                        preserved_items = (
                            ("dimension", preserved_dimensions or {}, {item.id: item.model_dump(mode="json") for item in builder.draft.dimensions}),
                            ("feature", preserved_features or {}, {item.id: item.model_dump(mode="json") for item in builder.draft.features}),
                            ("assumption", preserved_assumptions or {}, {item.id: item.model_dump(mode="json") for item in builder.draft.assumptions}),
                        )
                        changed = [
                            f"{kind}:{item_id}"
                            for kind, expected, actual in preserved_items
                            for item_id, payload in expected.items()
                            if actual.get(item_id) != payload
                        ]
                        reference_issues = builder.reference_issues()
                        if changed or reference_issues:
                            result = {
                                "ok": False,
                                "field": "complete_replan",
                                "message": "; ".join([
                                    *([f"Confirmed items changed without a clarification: {', '.join(changed)}"] if changed else []),
                                    *reference_issues,
                                ]),
                            }
                        else:
                            # Questions and assumed values are intentional at this point:
                            # the subsequent UI review, not the planner, resolves them.
                            return builder.finish()
            elif name == "set_draft_metadata": result = builder.set_metadata(args)
            elif name == "add_dimension": result = builder.add_dimension(args)
            elif name == "add_assumption": result = builder.add_assumption(args)
            elif name == "add_question": result = builder.add_question(args)
            elif name == "add_annotation": result = builder.add_annotation(args)
            elif name.startswith("add_"): result = builder.add_feature(args)
            else: result = {"ok": False, "message": "unknown tool"}
            last_results.append({"tool": name, **result})
            last_results = last_results[-8:]
            messages.append({"role": "tool", "tool_call_id": call["id"], "content": json.dumps(result)})
    raise GenerationError(
        "draft_specification",
        f"Planner exceeded the {MAX_DRAFT_PLANNER_TURNS}-turn limit before completing the draft",
        {
            "last_tool_results": last_results,
            "max_provider_turns": MAX_DRAFT_PLANNER_TURNS,
            "planner_run_id": planner_run_id,
            "planner_mode": planner_mode,
        },
    )


def submit_draft_specification_tool_schema() -> Dict[str, Any]:
    """Strict function-call arguments for a complete draft, separate from UI defaults."""
    draft_schema = draft_specification_tool_schema()
    _forbid_schema_extras(draft_schema)
    draft_schema["required"] = ["title", "units", "dimensions", "features", "assumptions", "questions", "annotations"]
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["specification"],
        "properties": {"specification": draft_schema},
    }


def _forbid_schema_extras(schema: Any) -> None:
    if isinstance(schema, dict):
        if schema.get("type") == "object" and "properties" in schema:
            schema["additionalProperties"] = False
        for value in schema.values():
            _forbid_schema_extras(value)
    elif isinstance(schema, list):
        for value in schema:
            _forbid_schema_extras(value)


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
    *,
    planner_run_id: str | None = None,
    planner_mode: str | None = None,
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
    if planner_run_id is not None:
        record["planner_run_id"] = planner_run_id
        record["planner_mode"] = planner_mode
    try:
        LLM_LOG_DIR.mkdir(parents=True, exist_ok=True)
        with (LLM_LOG_DIR / "llm_responses.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    except OSError as exc:
        logger.warning("Failed to write LLM response log: %s", exc)

    planner_context = (
        f" planner_run_id={planner_run_id} planner_mode={planner_mode}"
        if planner_run_id is not None
        else ""
    )
    logger.warning(
        "LLM response stage=%s model=%s attempt=%s status=%s%s %s",
        stage,
        request_payload.get("model"),
        attempt,
        status_code,
        planner_context,
        _response_log_summary(content, response_payload),
    )


def _response_log_summary(content: Any, response_payload: Any) -> str:
    """Make tool-call-only responses useful in the console without dumping provider JSON."""
    tool_calls = _response_tool_calls(response_payload)
    if tool_calls:
        previews = [_tool_call_log_preview(call) for call in tool_calls]
        return "tools=[" + "; ".join(previews) + "]"
    return f"content={content}"


def _response_tool_calls(response_payload: Any) -> List[Dict[str, Any]]:
    if not isinstance(response_payload, dict):
        return []
    try:
        calls = response_payload["choices"][0]["message"].get("tool_calls", [])
    except (KeyError, IndexError, TypeError):
        return []
    return [call for call in calls if isinstance(call, dict)] if isinstance(calls, list) else []


def _tool_call_log_preview(call: Dict[str, Any]) -> str:
    function = call.get("function")
    if not isinstance(function, dict):
        return "unknown()"
    name = str(function.get("name") or "unknown")
    raw_arguments = function.get("arguments", "{}")
    try:
        arguments = json.loads(raw_arguments) if isinstance(raw_arguments, str) else raw_arguments
    except json.JSONDecodeError:
        return f"{name}(arguments=<invalid JSON>)"
    if not isinstance(arguments, dict) or not arguments:
        return f"{name}()"
    preferred = ("id", "title", "type", "operation", "target", "value", "expression", "unit")
    keys = [key for key in preferred if key in arguments]
    keys.extend(key for key in arguments if key not in keys)
    pairs = [f"{key}={_compact_log_value(arguments[key])}" for key in keys[:3]]
    return f"{name}({', '.join(pairs)})"


def _compact_log_value(value: Any) -> str:
    if isinstance(value, str):
        compact = value.replace("\n", " ")
        return repr(compact[:80] + "…" if len(compact) > 80 else compact)
    if isinstance(value, list):
        return f"list[{len(value)}]"
    if isinstance(value, dict):
        return f"object[{len(value)}]"
    return repr(value)


async def _chat_json(url: str, api_key: str, payload: Dict[str, Any], stage: str) -> Dict[str, Any]:
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "http://localhost:8852",
        "X-Title": "EasyCAD",
    }
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
            detail = {"exception_type": type(exc).__name__, "exception_message": str(exc)}
            logger.exception("LLM provider transport failure stage=%s detail=%s", stage, detail)
            raise GenerationError(stage, "Provider request failed", detail) from exc

        if response.status_code >= 400:
            _log_model_response(
                stage,
                request_payload,
                attempt + 1,
                response.status_code,
                response.text,
                {"error_body": response.text},
            )
            raise GenerationError(
                stage,
                f"Provider returned HTTP {response.status_code}",
                {"status_code": response.status_code},
            )

        try:
            response_payload = response.json()
            choice = response_payload["choices"][0]
            message = choice["message"]
            provider_error = choice.get("error")
            if provider_error or choice.get("finish_reason") == "error":
                error_type = "provider_error"
                if isinstance(provider_error, dict):
                    metadata = provider_error.get("metadata")
                    candidate = metadata.get("error_type") if isinstance(metadata, dict) else provider_error.get("code")
                    if isinstance(candidate, str) and candidate:
                        error_type = candidate
                _log_model_response(stage, request_payload, attempt + 1, response.status_code, message.get("content", ""), response_payload)
                user_message = "Provider temporarily rate-limited the request. Please try again." if "rate_limit" in error_type else "Provider could not complete the request. Please try again."
                raise GenerationError(stage, user_message, {"provider_error": error_type})
            tool_calls = message.get("tool_calls", [])
            content = tool_calls[0]["function"]["arguments"] if tool_calls else message["content"]
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
            continue
    raise GenerationError(stage, "Provider did not return a JSON object")


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
    except json.JSONDecodeError:
        parsed = None
    if isinstance(parsed, dict):
        return parsed
    raise GenerationError(stage, "Provider did not return a JSON object")


def normalize_model_id(model: str) -> str:
    return MODEL_ALIASES.get(model.strip(), model.strip())
