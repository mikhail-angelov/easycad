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
    CADParameter,
    CADProject,
    CADSource,
    DrawingAnalysis,
    FeatureCoverageReport,
    FeatureGraph,
    FeatureOperation,
    FeatureSummary,
    DraftSpecification,
    GenerationResult,
    SourceInfo,
    VisualComparison,
)
from .feature_compiler import (
    CompilerError,
    OPERATION_CONTRACTS,
    canonical_operation_type,
    compile_project_feature_graph,
    compiler_operation_types,
    draft_operation_contract_descriptions,
    draft_specification_operation_types,
)
from .draft_geometry_rules import draft_geometry_rules
from .expressions import ExpressionError, evaluate_expression
from .source_images import get_source_image, store_source_image
from .validator import validate_project
from .draft_builder import DraftBuilder


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


def normalize_draft_specification_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Translate observed provider field variants into the narrow DraftSpecification contract."""
    wrapped_parameters = payload.get("parameters")
    wrapped_specification = payload.get("draft_specification")
    if isinstance(wrapped_parameters, dict) and "features" in wrapped_parameters:
        normalized = dict(wrapped_parameters)
    elif isinstance(wrapped_specification, dict) and "features" in wrapped_specification:
        normalized = dict(wrapped_specification)
    else:
        normalized = dict(payload)
    if str(normalized.get("units", "")).lower() in {"millimeter", "millimeters", "millimetre", "millimetres"}:
        normalized["units"] = "mm"

    for dimension in normalized.get("dimensions", []):
        if not isinstance(dimension, dict):
            continue
        if isinstance(dimension.get("evidence"), str):
            dimension["evidence"] = [dimension["evidence"]]
        if dimension.get("source") not in {"drawing", "derived", "inferred", "assumed", "manual", None}:
            dimension["source"] = "drawing"

    for feature in normalized.get("features", []):
        if isinstance(feature, dict):
            if isinstance(feature.get("evidence"), str):
                feature["evidence"] = [feature["evidence"]]
            if feature.get("placement") is None:
                feature["placement"] = {}

    assumptions = []
    for assumption in normalized.get("assumptions", []):
        if not isinstance(assumption, dict):
            continue
        item = dict(assumption)
        description = str(item.get("description", ""))
        item.setdefault("value", description)
        item.setdefault("rationale", description)
        item.setdefault("affected_ids", item.get("affects", []))
        assumptions.append(item)
    normalized["assumptions"] = assumptions

    questions = []
    for question in normalized.get("questions", []):
        if not isinstance(question, dict):
            continue
        item = dict(question)
        related = item.get("related_features") or item.get("related_dimensions") or item.get("required_for") or []
        if not related and item.get("related_feature"):
            related = [item["related_feature"]]
        if isinstance(related, str):
            related = [related]
        item.setdefault("field_id", related[0] if related else item.get("id", "question"))
        item.setdefault("prompt", item.get("question", item.get("description", "Please provide the missing detail.")))
        questions.append(item)
    normalized["questions"] = questions

    annotations = []
    for annotation in normalized.get("annotations", []):
        if not isinstance(annotation, dict):
            continue
        item = dict(annotation)
        links = item.get("links_to", item.get("field_ids", item.get("field_id", [])))
        if isinstance(links, str):
            links = [links]
        if not isinstance(links, list):
            links = []
        field_ids = [link for link in links if isinstance(link, str) and link]
        item["field_ids"] = field_ids
        item["field_id"] = field_ids[0] if field_ids else str(item.get("field_id") or item.get("id", "annotation"))
        item.setdefault("label", item.get("text", item["field_id"]))
        annotations.append(item)
    normalized["annotations"] = annotations
    return normalized


async def generate_draft_specification_from_image(
    data: bytes, filename: str, mime_type: str, instructions: str = ""
) -> DraftSpecification:
    image_info = validate_image_upload(data, filename, mime_type)
    openrouter_key = os.environ.get("OPEN_ROUTER_KEY")
    deepseek_key = os.environ.get("DEEP_SEEK_KEY")
    if not openrouter_key:
        raise GenerationError("vision_analysis", "OPEN_ROUTER_KEY is not configured")
    if not deepseek_key:
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
    model = normalize_model_id(os.environ.get("DEEP_SEEK_MODEL", os.environ.get("DEEPSEEK_MODEL", "deepseek-chat")))
    url = os.environ.get("DEEP_SEEK_BASE_URL", "https://api.deepseek.com/chat/completions")
    prompt = (
        "Build a DraftSpecification by calling the supplied tools until finish_draft. Do not return CAD code, Feature Graph, or JSON prose. "
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
        "max_tokens": 5000,
        "tools": draft_builder_tools(),
        "tool_choice": "required",
    }
    return await _run_draft_builder(url, api_key, payload, analysis)


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


async def _run_draft_builder(url: str, api_key: str, payload: Dict[str, Any], analysis: Dict[str, Any]) -> DraftSpecification:
    builder = DraftBuilder(analysis)
    messages = list(payload["messages"])
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://easycad.local",
        "X-Title": "EasyCAD",
    }
    last_results: List[Dict[str, Any]] = []
    for _ in range(48):
        try:
            async with httpx.AsyncClient(timeout=120) as client:
                response = await client.post(url, headers=headers, json={**payload, "messages": messages})
        except httpx.HTTPError as exc:
            detail = {"exception_type": type(exc).__name__, "exception_message": str(exc)}
            logger.exception("LLM request failed stage=draft_specification detail=%s", detail)
            raise GenerationError("draft_specification", "Provider request failed", detail) from exc
        if response.status_code >= 400:
            detail = {"status_code": response.status_code, "response": response.text[:4000]}
            logger.error("LLM request failed stage=draft_specification detail=%s", detail)
            raise GenerationError("draft_specification", f"Provider returned HTTP {response.status_code}", detail)
        try:
            response_payload = response.json()
            message = response_payload["choices"][0]["message"]
        except (KeyError, IndexError, ValueError, TypeError) as exc:
            detail = {"response": response.text[:4000], "exception_type": type(exc).__name__}
            logger.exception("LLM response was malformed stage=draft_specification detail=%s", detail)
            raise GenerationError("draft_specification", "Provider returned a malformed tool response", detail) from exc
        calls = message.get("tool_calls", [])
        _log_model_response("draft_specification", {**payload, "messages": messages}, 1, response.status_code, message.get("content", ""), response_payload)
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
                if not builder.draft.title:
                    result = {"ok": False, "field": "title", "message": "set_draft_metadata must be called before finish_draft"}
                elif not builder.draft.features:
                    result = {"ok": False, "field": "features", "message": "draft must contain at least one feature before finish_draft"}
                else:
                    # Questions and assumed values are intentional at this point:
                    # the subsequent UI review, not the planner, resolves them.
                    return builder.finish()
            elif name == "set_draft_metadata": result = builder.set_metadata(**args)
            elif name == "add_dimension": result = builder.add_dimension(args)
            elif name == "add_assumption": result = builder.add_assumption(args)
            elif name == "add_question": result = builder.add_question(args)
            elif name == "add_annotation": result = builder.add_annotation(args)
            elif name.startswith("add_"): result = builder.add_feature(args)
            else: result = {"ok": False, "message": "unknown tool"}
            last_results.append({"tool": name, **result})
            last_results = last_results[-8:]
            messages.append({"role": "tool", "tool_call_id": call["id"], "content": json.dumps(result)})
    raise GenerationError("draft_specification", "Planner exceeded tool-call limit", {"last_tool_results": last_results})


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


def project_from_plan(
    plan: Dict[str, Any],
    analysis: Dict[str, Any],
    image_info: Dict[str, Any],
    image_data: bytes,
) -> CADProject:
    parameters = _normalize_parameters(plan.get("parameters", []))
    if not parameters:
        raise GenerationError("cad_generation", "CAD plan did not include parameters")

    graph_payload = plan.get("feature_graph")
    _lift_inline_feature_expressions(graph_payload, parameters)
    feature_summary = _normalize_features(plan.get("feature_summary", plan.get("features", [])))
    analysis_features = _normalize_analysis_features(analysis.get("features"))
    feature_graph = _normalize_feature_graph(graph_payload, analysis_features)
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
        if exc.operation_id == "feature_graph":
            project.generation.status = "needs_review"
            project.generation.error = {
                "stage": "feature_compiler",
                "message": str(exc),
                "detail": {"operation_id": exc.operation_id},
            }
            return project
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
        if declared_type.lower() in {"expression", "derived", "formula"} and not payload.get("expression"):
            if isinstance(payload.get("value"), str):
                payload["expression"] = payload.pop("value")
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


def _lift_inline_feature_expressions(raw: Any, parameters: Dict[str, CADParameter]) -> None:
    operations = raw.get("operations") if isinstance(raw, dict) else raw
    if not isinstance(operations, list):
        return
    known = {key: 2.0 for key in parameters}
    expression_ids: Dict[str, str] = {}

    def lift(value: Any) -> Any:
        if not isinstance(value, str) or _parameter_id(value) == value:
            return value
        try:
            evaluate_expression(value, known)
        except (ExpressionError, SyntaxError, ZeroDivisionError):
            return value
        if value not in expression_ids:
            parameter_id = f"derived_expr_{len(expression_ids) + 1}"
            while parameter_id in parameters:
                parameter_id = f"derived_expr_{len(expression_ids) + 1}_{len(parameters)}"
            expression_ids[value] = parameter_id
            parameters[parameter_id] = CADParameter(
                label="Derived geometry value",
                type="expression",
                expression=value,
                unit="mm",
                source="derived",
                editable=False,
            )
            known[parameter_id] = 2.0
        return expression_ids[value]

    for operation in operations:
        if not isinstance(operation, dict):
            continue
        if isinstance(operation.get("parameters"), dict):
            operation["parameters"] = {key: lift(value) for key, value in operation["parameters"].items()}
        placement = operation.get("placement")
        if isinstance(placement, dict) and isinstance(placement.get("origin"), list):
            placement["origin"] = [lift(value) for value in placement["origin"]]
        profile = operation.get("profile")
        if isinstance(profile, dict):
            if isinstance(profile.get("dimensions"), dict):
                profile["dimensions"] = {key: lift(value) for key, value in profile["dimensions"].items()}
            if isinstance(profile.get("points"), list):
                profile["points"] = [
                    [lift(value) for value in point] if isinstance(point, list) else point
                    for point in profile["points"]
                ]


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
        payload = _adapt_provider_operation(payload)
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
        if not payload["source_feature_ids"]:
            inferred_source_id = _infer_source_feature_id(operation_id, analysis_features)
            if inferred_source_id:
                payload["source_feature_ids"].append(inferred_source_id)
        payload["source_feature_ids"] = list(dict.fromkeys(payload["source_feature_ids"]))
        source_types = {
            str(feature.get("type") or "").strip().lower()
            for feature in analysis_features
            if str(feature.get("id") or "") in payload["source_feature_ids"]
        }
        if "groove" in source_types and payload.get("type") in {"slot", "slot2d"}:
            payload["status"] = "approximated"
            payload["capability_status"] = "experimental"
            payload["assumption"] = "A semicircular groove is approximated by a slot; its section is not verified."
        mapped_feature_ids.update(payload["source_feature_ids"])
        operations.append(payload)

    unsupported_ids = {
        operation["id"] for operation in operations if operation.get("status") in {"unsupported", "unresolved"}
    }
    for operation in operations:
        if operation.get("target") in unsupported_ids and operation.get("status") == "implemented":
            operation["status"] = "unsupported"
            operation["capability_status"] = "unsupported"
            operation["assumption"] = "Target operation is unsupported by the trusted compiler."
            operation.pop("implementation", None)
            unsupported_ids.add(operation["id"])

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


def _adapt_provider_operation(payload: Dict[str, Any]) -> Dict[str, Any]:
    feature_type = canonical_operation_type(str(payload.get("type") or "feature"))
    payload["type"] = feature_type
    invalid_profile_shape = _normalize_provider_shape_fields(payload)
    implementation = payload.get("implementation")
    trusted_types = compiler_operation_types()
    if isinstance(implementation, str) and (
        feature_type not in trusted_types or payload.get("parameters") or payload.get("profile")
    ) and not invalid_profile_shape:
        payload["operation"] = _normalize_feature_operation(str(payload.get("operation") or "add"))
        return payload
    if implementation is None and (payload.get("parameters") or payload.get("profile")):
        # A structured operation needs no model-owned implementation payload. The trusted compiler assigns the stage ID.
        implementation = {}
    if isinstance(implementation, dict):
        parameters = dict(payload.get("parameters") or {})
        for key, value in implementation.items():
            if key not in {"placement", "profile", "profile_parameters", "profile_params"}:
                parameters.setdefault(key, value)
        payload["parameters"] = parameters
        placement = implementation.get("placement")
        if isinstance(placement, dict) and isinstance(placement.get("origin"), list):
            payload["placement"] = {"origin": placement["origin"]}
        profile = implementation.get("profile")
        if isinstance(profile, dict):
            profile_type = str(profile.get("type") or "").lower()
            if profile_type in {"rectangle", "rect"}:
                payload["profile"] = {
                    "type": "rectangle",
                    "dimensions": {"width": profile.get("length"), "height": profile.get("width")},
                }
            elif profile_type == "circle":
                payload["profile"] = {
                    "type": "circle",
                    "dimensions": {"diameter": profile.get("diameter")},
                }
        elif profile in {"rectangle", "rect"}:
            profile_parameters = implementation.get("profile_params", implementation)
            payload["profile"] = {
                "type": "rectangle",
                "dimensions": {
                    "width": profile_parameters.get("length"),
                    "height": profile_parameters.get("width"),
                },
            }
        elif profile == "circle":
            payload["profile"] = {
                "type": "circle",
                "dimensions": {"diameter": implementation.get("diameter")},
            }
        payload["implementation"] = payload["id"]

    operation = "add"
    if feature_type in {"hole", "through_hole", "counterbore", "countersink", "slot", "pocket"}:
        operation = "cut"
    elif feature_type in {"fillet", "chamfer", "shell", "mirror"}:
        operation = "modify"
    elif "pattern" in feature_type:
        operation = "pattern"
    payload["operation"] = operation

    values = list((payload.get("parameters") or {}).values())
    has_non_scalar_parameter = any(isinstance(value, (list, dict)) for value in values)
    profile_dimensions = (payload.get("profile") or {}).get("dimensions", {})
    values.extend(profile_dimensions.values())
    placement = payload.get("placement") or {}
    values.extend(placement.get("origin", []) if isinstance(placement, dict) else [])
    has_expression = any(isinstance(value, str) and not _parameter_id(value) == value for value in values)
    missing_profile = feature_type in {"extrude", "revolve", "gusset"} and not payload.get("profile")
    missing_pattern = "pattern" in feature_type and not isinstance(payload.get("pattern"), dict)
    required_parameters = {
        "box": {"length", "width", "height"},
        "cylinder": {"radius", "height"},
        "extrude": {"distance"},
        "hole": {"diameter", "depth"},
        "through_hole": {"diameter", "depth"},
        "slot": {"length", "width", "depth"},
        "pocket": {"length", "width", "depth"},
        "fillet": {"radius"},
        "chamfer": {"distance"},
        "shell": {"thickness"},
        "text": {"content", "size", "distance"},
    }.get(feature_type, set())
    missing_parameters = required_parameters - set(payload.get("parameters") or {})
    invalid_profile = bool(payload.get("profile")) and any(value is None for value in profile_dimensions.values())
    if (
        feature_type not in trusted_types
        or not isinstance(implementation, dict)
        or has_expression
        or missing_profile
        or missing_pattern
        or missing_parameters
        or invalid_profile
        or invalid_profile_shape
        or has_non_scalar_parameter
    ):
        if has_non_scalar_parameter:
            payload["parameters"] = {
                key: value
                for key, value in (payload.get("parameters") or {}).items()
                if not isinstance(value, (list, dict))
            }
        payload["status"] = "unsupported"
        payload["capability_status"] = "unsupported"
        payload["assumption"] = "Provider operation cannot be mapped safely to the trusted compiler schema."
        payload.pop("implementation", None)
    elif payload.get("status") == "implemented":
        payload["implementation"] = payload["id"]
    return payload


def _normalize_provider_shape_fields(payload: Dict[str, Any]) -> bool:
    placement = payload.get("placement")
    if isinstance(placement, dict):
        normalized_placement = {
            key: value
            for key, value in placement.items()
            if key in {"reference", "plane", "origin", "axis", "direction", "rotation_deg", "offsets"}
        }
        if not isinstance(normalized_placement.get("direction"), str):
            normalized_placement.pop("direction", None)
        if not isinstance(normalized_placement.get("axis"), str):
            normalized_placement.pop("axis", None)
        payload["placement"] = normalized_placement or None

    profile = payload.get("profile")
    if profile is None:
        return False
    if not isinstance(profile, dict):
        payload["profile"] = None
        return True
    profile_type = str(profile.get("type") or "").lower()
    dimensions = profile.get("dimensions") if isinstance(profile.get("dimensions"), dict) else {}
    if profile_type in {"rectangle", "rect"}:
        dimensions = {
            "width": dimensions.get("width", profile.get("length")),
            "height": dimensions.get("height", profile.get("width")),
        }
        payload["profile"] = {"type": "rectangle", "dimensions": dimensions}
    elif profile_type == "circle":
        payload["profile"] = {
            "type": "circle",
            "dimensions": {"diameter": dimensions.get("diameter", profile.get("diameter"))},
        }
    return False


def _infer_source_feature_id(operation_id: str, analysis_features: List[Dict[str, Any]]) -> str | None:
    """Recover only unambiguous provider IDs such as ``op_base_plate`` -> ``base_plate``."""
    canonical_id = re.sub(r"^(?:op|operation|feature)_+", "", operation_id)
    matches = [
        str(feature.get("id"))
        for feature in analysis_features
        if canonical_id == str(feature.get("id") or "")
    ]
    return matches[0] if len(matches) == 1 else None


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
        "LLM response stage=%s model=%s attempt=%s status=%s %s",
        stage,
        request_payload.get("model"),
        attempt,
        status_code,
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
