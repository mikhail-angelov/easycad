from __future__ import annotations

import base64
import json
import time
import logging
import os
import re
import warnings
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

import httpx
from fastapi import HTTPException
from PIL import Image, UnidentifiedImageError

from .models import (
    DraftSpecification,
    SourceInfo,
)
from .feature_compiler import (
    OPERATION_CONTRACTS,
    draft_operation_contract_descriptions,
    draft_specification_operation_types,
)
from .draft_builder import DraftBuilder
from .minimal_model import minimal_reliable_draft


MAX_UPLOAD_BYTES = 20 * 1024 * 1024
MAX_IMAGE_DIMENSION = 12000
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
LLM_LOG_DIR = Path(os.environ.get("EASYCAD_LLM_LOG_DIR", "logs"))
MAX_DRAFT_PLANNER_TURNS = 12
SAFE_RESPONSE_HEADER_NAMES = {"content-type", "content-length", "x-request-id", "cf-ray", "server", "via"}
logger = logging.getLogger("easycad.llm")
MODEL_ALIASES = {
    "gemini_3_flash": "google/gemini-3-flash-preview",
    "gemini-3-flash": "google/gemini-3-flash-preview",
}
class GenerationError(RuntimeError):
    def __init__(self, stage: str, message: str, detail: Optional[dict] = None, *, planner_outcome: Literal["turn_limit", "provider_error", "planner_stopped"] | None = None):
        super().__init__(message)
        self.stage = stage
        self.detail = detail or {}
        self.planner_outcome = planner_outcome


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
    draft = minimal_reliable_draft(draft)
    draft.source = SourceInfo(
        filename=image_info.get("filename", ""),
        mime_type=image_info.get("mime_type", ""),
        width=image_info.get("width"),
        height=image_info.get("height"),
    )
    return draft


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
    freeform_instruction = str((user_inputs or {}).get("freeform_instruction", "")).strip()
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
        "You operate a server-owned builder: call set_draft_metadata once, add dimensions and features, then call finish_draft. "
        "After a tool result with ok=true, that item is stored. Re-calling an add_ tool with an already-stored ID replaces that stored item: "
        "use this only to correct an item that a later tool result reports as wrong; never repeat an unchanged item. After ok=false, correct and retry only that item. "
        "When all intended items have ok=true results, call finish_draft immediately. Never omit finish_draft and never continue after it. "
        "Each dimension requires id, label, value or expression, unit, source, confidence, status, critical, evidence. "
        "Each feature requires id, label, type, operation, target, parameters, placement, status, critical_fields, confidence, evidence, source_feature_ids. "
        "Map every normalized drawing-analysis feature id to one or more specification features through source_feature_ids; never silently drop an observed feature. "
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
        "For text, engraved, recessed, or inset means operation=cut; embossed, raised, protruding, or outward text means operation=add. "
        "Place raised text so its extrusion leaves the named exterior face while still touching the target body. "
        "Build one connected solid: the first feature is the only root; every additive feature after the first MUST set target to the existing root body, and every cut MUST target that same connected body. "
        "Feature placement may contain only reference, plane, origin, axis, direction, rotation_deg, and offsets. "
        "For fillet and chamfer, target is the feature ID; placement.reference is optional CadQuery edge-selector text such as '>Z', "
        "never a feature ID. Omit reference when the selected edges are not known. "
        "Use origin as exactly three numeric values or dimension IDs for translation, never expressions; never use offset, center, position, depth, or centered_on_width. "
        "Write constant origin coordinates as plain numbers: use 0 directly and never declare a dimension for the constant zero. "
        "Use millimetres for every measurement. Build a minimal reliable model, not a complete interpretation: include only geometry with "
        "unambiguous dimensions, position, target, and direction. Never ask questions in this pass. If a detail is incomplete, contradictory, "
        "or unsupported, return it as status unsupported with its source_feature_ids and a short omission reason in its label. Do not invent "
        "a dimension. If the main form is unclear, still return one confirmed box body using the most reliable overall dimensions available, "
        "or a 100 x 100 x 10 mm box when none are readable. "
    )
    if previous_specification is not None:
        prompt += (
            " This is a seeded, already-rendered model. Its metadata, dimensions, and features are already stored. "
            "Do not call set_draft_metadata and do not repeat unchanged items. Apply the user's requested change by adding or "
            "replacing only the affected items, then call finish_draft immediately."
        )
    if instructions.strip():
        prompt += f"\nUser instructions: {instructions.strip()}"
    if freeform_instruction:
        prompt += f"\nFreeform model-change instruction: {freeform_instruction}"
    previous_payload: object = previous_specification.model_dump(mode="json") if previous_specification else None
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": prompt},
            {"role": "user", "content": json.dumps({
                "drawing_analysis": analysis,
                "previous_specification": previous_payload,
                "user_inputs": user_inputs or {},
            }, ensure_ascii=False)},
        ],
        "temperature": 0.1,
        "max_tokens": 20000 if openrouter_planner_model else 5000,
        "tools": draft_builder_tools(seeded=previous_specification is not None),
        "tool_choice": "required",
    }
    return await _run_draft_builder(
        url,
        api_key,
        payload,
        analysis,
        planner_run_id=uuid4().hex[:12],
        planner_mode="initial",
        previous_specification=previous_specification,
    )

def _draft_feature_schema() -> Dict[str, Any]:
    """Discriminated feature schema generated from the compiler operation registry."""
    value_schema = {"anyOf": [{"type": "string"}, {"type": "number"}, {"type": "integer"}, {"type": "boolean"}]}
    common = {
        "id": {"type": "string", "pattern": "^[a-z][a-z0-9_]*$"},
        "label": {"type": "string"},
        "target": {"anyOf": [{"type": "string"}, {"type": "null"}]},
        "placement": {"$ref": "#/$defs/FeaturePlacement"},
        "status": {"enum": ["confirmed", "needs_input", "assumed", "conflicted", "unsupported"]},
        "critical_fields": {"type": "array", "items": {"type": "string"}},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "evidence": {"type": "array", "items": {"type": "string"}},
        "alternatives": {"type": "object", "additionalProperties": {"type": "array", "items": value_schema}},
        "source_feature_ids": {"type": "array", "items": {"type": "string"}},
    }
    variants = []
    for feature_type, contract in OPERATION_CONTRACTS.items():
        properties = {
            **common,
            "type": {"const": feature_type},
            "operation": {"enum": list(contract.allowed_operations)},
            "parameters": _parameter_schema(contract.required_parameters, contract.optional_parameters, value_schema),
        }
        required = ["id", "label", "type", "operation", "target", "parameters", "placement", "status", "critical_fields", "confidence", "evidence", "alternatives", "source_feature_ids"]
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


def draft_builder_tools(*, seeded: bool = False) -> List[Dict[str, Any]]:
    schema = DraftSpecification.model_json_schema()["$defs"]
    feature_variants = _draft_feature_schema()["oneOf"]
    # Each function schema is sent independently.  Keep the shared definitions
    # alongside it so the provider can resolve FeaturePlacement references.
    def with_definitions(parameters: Dict[str, Any]) -> Dict[str, Any]:
        return {**parameters, "$defs": schema}

    tools = [
        {"type": "function", "function": {"name": "add_dimension", "parameters": with_definitions(schema["SpecificationDimension"])}},
        {"type": "function", "function": {"name": "finish_draft", "parameters": {"type": "object", "additionalProperties": False, "properties": {}}}},
    ]
    if not seeded:
        tools.insert(0, {"type": "function", "function": {"name": "set_draft_metadata", "parameters": {"type": "object", "additionalProperties": False, "required": ["title", "units"], "properties": {"title": {"type": "string"}, "units": {"const": "mm"}}}}})
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
    planner_run_id: str | None = None,
    planner_mode: str = "initial",
    previous_specification: DraftSpecification | None = None,
) -> DraftSpecification:
    from datetime import datetime, timezone
    from .draft_lint import lint_draft
    from .run_metrics import append_planner_run

    stats: Dict[str, Any] = {"turns_used": 0, "tool_calls": 0, "tool_errors": 0, "finish_rejections": {}, "removed_items": []}
    started = time.monotonic()
    outcome = "completed"
    draft: DraftSpecification | None = None
    try:
        draft = await _run_draft_builder_impl(
            url, api_key, payload, analysis,
            planner_run_id=planner_run_id,
            planner_mode=planner_mode,
            previous_specification=previous_specification,
            _run_stats=stats,
        )
        return draft
    except GenerationError as exc:
        outcome = exc.planner_outcome or "provider_error"
        raise
    except Exception:
        outcome = "provider_error"
        raise
    finally:
        lint = lint_draft(draft) if draft is not None else None
        append_planner_run({
            "created_at": datetime.now(timezone.utc).isoformat(),
            "planner_run_id": planner_run_id,
            "planner_mode": planner_mode,
            "model": payload.get("model"),
            "outcome": outcome,
            **stats,
            "lint_errors": sum(item.severity == "error" for item in lint.issues) if lint else 0,
            "lint_warnings": sum(item.severity == "warning" for item in lint.issues) if lint else 0,
            "duration_ms": round((time.monotonic() - started) * 1000),
        })


async def _run_draft_builder_impl(
    url: str,
    api_key: str,
    payload: Dict[str, Any],
    analysis: Dict[str, Any],
    *,
    planner_run_id: str | None = None,
    planner_mode: str = "initial",
    previous_specification: DraftSpecification | None = None,
    _run_stats: Dict[str, Any] | None = None,
) -> DraftSpecification:
    builder = DraftBuilder.seed(previous_specification) if previous_specification else DraftBuilder(analysis)
    messages = list(payload["messages"])
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://easycad.local",
        "X-Title": "EasyCAD",
    }
    last_tool_failure: tuple[str, str] | None = None
    repeated_tool_failures = 0
    turn_cap = MAX_DRAFT_PLANNER_TURNS
    for turn in range(1, turn_cap + 1):
        if _run_stats is not None:
            _run_stats["turns_used"] = turn
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
                **_safe_response_diagnostics(response, url),
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
            raise GenerationError("draft_specification", "Planner stopped before finish_draft", planner_outcome="planner_stopped")
        messages.append(message)
        for call in calls:
            if _run_stats is not None:
                _run_stats["tool_calls"] += 1
            name = call["function"]["name"]
            raw_arguments = call["function"].get("arguments", "{}")
            try:
                args = _parse_json_object(raw_arguments, "draft_specification")
            except GenerationError as exc:
                if _run_stats is not None:
                    _run_stats["tool_errors"] += 1
                messages.append({"role": "tool", "tool_call_id": call["id"], "content": json.dumps({"ok": False, "message": str(exc)})})
                continue
            if name == "finish_draft":
                if not builder.metadata_set:
                    result = {"ok": False, "field": "title", "message": "set_draft_metadata must be called before finish_draft"}
                elif not builder.draft.features:
                    result = {"ok": False, "field": "features", "message": "draft must contain at least one feature before finish_draft"}
                else:
                    return builder.finish()
            elif name == "set_draft_metadata": result = builder.set_metadata(args)
            elif name == "add_dimension": result = builder.add_dimension(args)
            elif name.startswith("add_"):
                result = builder.add_feature(args)
            else: result = {"ok": False, "message": "unknown tool"}
            if _run_stats is not None and not result.get("ok", False):
                _run_stats["tool_errors"] += 1
                if name == "finish_draft":
                    reason = str(result.get("field", "unknown"))
                    rejections = _run_stats["finish_rejections"]
                    rejections[reason] = rejections.get(reason, 0) + 1
            messages.append({"role": "tool", "tool_call_id": call["id"], "content": json.dumps(result)})
            if result.get("ok", False):
                last_tool_failure = None
                repeated_tool_failures = 0
            else:
                signature = (name, json.dumps(result, ensure_ascii=False, sort_keys=True))
                repeated_tool_failures = repeated_tool_failures + 1 if signature == last_tool_failure else 1
                last_tool_failure = signature
                if repeated_tool_failures >= 3:
                    logger.warning(
                        "Planner repeated the same rejected tool call; returning the accumulated draft planner_run_id=%s tool=%s",
                        planner_run_id,
                        name,
                    )
                    return builder.draft
    logger.warning(
        "Planner turn limit reached; returning the accumulated draft planner_run_id=%s turns=%s",
        planner_run_id,
        turn_cap,
    )
    return builder.draft

def _safe_response_diagnostics(response: httpx.Response, provider_url: str) -> Dict[str, object]:
    """Record enough malformed-response context to debug a provider without logging secrets."""
    parsed_url = urlsplit(provider_url)
    safe_url = urlunsplit((parsed_url.scheme, parsed_url.netloc.rsplit("@", 1)[-1], parsed_url.path, "", ""))
    headers = {
        name.lower(): value
        for name, value in response.headers.items()
        if name.lower() in SAFE_RESPONSE_HEADER_NAMES
    }
    body = response.content
    return {
        "provider_url": safe_url,
        "response_headers": headers,
        "response_content_length": len(body),
        "response_prefix_hex": body[:64].hex(),
    }


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
        base_id = _parameter_id(str(payload.get("id") or "")) or f"feature_{idx}"
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
        "request_message_count": len(request_payload.get("messages", []) or []),
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
