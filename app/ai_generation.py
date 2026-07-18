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
from .draft_geometry_rules import draft_geometry_rules
from .draft_builder import DraftBuilder
from .minimal_model import minimal_reliable_draft


MAX_UPLOAD_BYTES = 20 * 1024 * 1024
MAX_IMAGE_DIMENSION = 12000
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
LLM_LOG_DIR = Path(os.environ.get("EASYCAD_LLM_LOG_DIR", "logs"))
MAX_DRAFT_PLANNER_TURNS = 48
MAX_INCREMENTAL_REPLAN_TURNS = 16
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
    incremental = previous_specification is not None and (
        os.environ.get("EASYCAD_REPLAN_MODE", "full") == "incremental" or bool(freeform_instruction)
    )
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
        + draft_geometry_rules()
        + "\n"
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
        "Annotations use normalized x and y coordinates from 0 to 1 and link to a dimension or feature. "
    )
    if previous_specification is not None and not incremental:
        prompt += (
            "Return a complete replacement DraftSpecification, not a patch. The previous specification is reference context only; "
            "use the drawing analysis and user inputs to resolve it again. Return every previous dimension, feature, and assumption: "
            "never delete an existing item or return an empty graph. The one exception is an item that a clarification directly "
            "overrides: correct it, and omit it only when the user's answer says to exclude or remove it. "
            "Keep IDs and proposed geometry unless user input changes them, "
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
        if freeform_instruction:
            prompt += (
                " A freeform_instruction is an explicit request to revise the model broadly: it may replace or remove previously "
                "confirmed dimensions, features, or assumptions when needed to follow that instruction."
            )
    elif incremental:
        prompt += (
            "Edit the seeded DraftSpecification in place. Locked items are server-owned and cannot be changed. "
            "The metadata and previous items are already stored: do not call set_draft_metadata and do not re-add unchanged items. "
            "Call add_* only for unlocked items that need replacement, remove_item for an unlocked obsolete item, "
            "and resolve_question for every answered question. Then call finish_draft."
        )
        if freeform_instruction:
            prompt += (
                " The freeform instruction is a direct user decision: create its requested features with status confirmed, "
                "target the existing rendered body by its stable feature ID, and use source_feature_ids=['freeform_instruction']. "
                "Do not ask questions for a freeform edit. When it supplies a feature depth but omits another symmetric "
                "profile span, use that supplied depth as the missing span: this produces the smallest conventional profile "
                "that can be rendered while preserving every explicit user measurement. Origin conventions are strict: use a "
                "rectangular box cut when you calculate a profile corner, and use pocket or slot only when placement.origin is "
                "the profile centre. Never combine a corner coordinate with a centred-profile primitive. Before finish_draft, "
                "check every freeform position constraint: an edge pair centred on an axis has each feature centre on that "
                "axis midpoint and lies on the two opposite edges of the other in-plane axis."
            )
    if previous_specification is not None:
        prompt += (
            " A clarification with key build_repair is a deterministic build diagnostic supplied to the user: it overrides "
            "any earlier accepted feature geometry named in that clarification. Correct the complete replacement graph accordingly "
            "and do not repeat the failed placement."
        )
    if instructions.strip():
        prompt += f"\nUser instructions: {instructions.strip()}"
    if freeform_instruction:
        prompt += f"\nFreeform model-change instruction: {freeform_instruction}"
    excluded_ids = sorted(set((user_inputs or {}).get("excluded_feature_ids", [])))
    if excluded_ids:
        prompt += f"\nExplicitly excluded feature IDs: {', '.join(excluded_ids)}. Do not return these features."
    superseded_ids = _clarification_superseded_ids(previous_specification, user_inputs or {})
    locked_items = _incremental_locked_items(previous_specification, user_inputs or {}) if incremental else set()
    previous_payload: object = previous_specification.model_dump(mode="json") if previous_specification else None
    if incremental and previous_specification:
        open_items = {
            "dimensions": [item.model_dump(mode="json") for item in previous_specification.dimensions if ("dimension", item.id) not in locked_items],
            "features": [item.model_dump(mode="json") for item in previous_specification.features if ("feature", item.id) not in locked_items],
            "assumptions": [item.model_dump(mode="json") for item in previous_specification.assumptions if ("assumption", item.id) not in locked_items],
            "questions": [item.model_dump(mode="json") for item in previous_specification.questions],
        }
        previous_payload = {
            "open_items": open_items,
            "locked_items": [{"item_type": kind, "id": item_id} for kind, item_id in sorted(locked_items)],
            "rendered_model": {
                "dimensions": [item.model_dump(mode="json") for item in previous_specification.dimensions],
                "features": [item.model_dump(mode="json") for item in previous_specification.features if item.status == "confirmed"],
            },
        }
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
        "tools": draft_builder_tools(incremental=incremental),
        "tool_choice": "required",
    }
    builder_kwargs = ({"previous_specification": previous_specification, "locked_items": locked_items, "incremental": True,
                       "answered_question_ids": {key for key, value in (user_inputs or {}).get("clarifications", {}).items() if str(value).strip()}} if incremental else {
        "required_dimension_ids": set() if freeform_instruction else ({item.id for item in previous_specification.dimensions} - superseded_ids if previous_specification else set()),
        "required_feature_ids": set() if freeform_instruction else ({item.id for item in previous_specification.features} - superseded_ids if previous_specification else set()),
        "required_assumption_ids": set() if freeform_instruction else ({item.id for item in previous_specification.assumptions} - superseded_ids if previous_specification else set()),
        **({} if freeform_instruction else _confirmed_replan_snapshots(previous_specification, user_inputs or {})),
    })
    return await _run_draft_builder(
        url,
        api_key,
        payload,
        analysis,
        planner_run_id=uuid4().hex[:12],
        planner_mode="replan" if previous_specification else "initial",
        **builder_kwargs,
    )


def _clarification_superseded_ids(
    previous_specification: DraftSpecification | None, user_inputs: Dict[str, Any]
) -> set[str]:
    """Previous item IDs a user clarification directly overrides; these may be corrected or removed."""
    if previous_specification is None:
        return set()
    questions = {item.id: item.field_id for item in previous_specification.questions}
    clarified_fields = {
        questions[question_id]
        for question_id, text in user_inputs.get("clarifications", {}).items()
        if text and question_id in questions
    }
    superseded: set[str] = set()
    for item in [*previous_specification.dimensions, *previous_specification.features, *previous_specification.assumptions]:
        if any(field_id == item.id or field_id.startswith(f"{item.id}.") for field_id in clarified_fields):
            superseded.add(item.id)
    # An assumption about a superseded item is superseded with it: the clarification may restructure
    # the geometry the assumption describes.
    for assumption in previous_specification.assumptions:
        if any(affected_id in superseded for affected_id in assumption.affected_ids):
            superseded.add(assumption.id)
    return superseded


def _incremental_locked_items(
    previous_specification: DraftSpecification | None, user_inputs: Dict[str, Any]
) -> set[tuple[str, str]]:
    if previous_specification is None:
        return set()
    superseded = _clarification_superseded_ids(previous_specification, user_inputs)
    accepted_features = set(user_inputs.get("accepted_feature_ids", []))
    accepted_assumptions = set(user_inputs.get("accepted_assumption_ids", []))
    locked: set[tuple[str, str]] = set()
    locked.update(("dimension", item.id) for item in previous_specification.dimensions if item.status == "confirmed" and item.id not in superseded)
    locked.update(("feature", item.id) for item in previous_specification.features if (item.status == "confirmed" or item.id in accepted_features) and item.id not in superseded)
    locked.update(("assumption", item.id) for item in previous_specification.assumptions if (item.status == "confirmed" or item.id in accepted_assumptions) and item.id not in superseded)
    return locked


def _confirmed_replan_snapshots(
    previous_specification: DraftSpecification | None, user_inputs: Dict[str, Any]
) -> Dict[str, Dict[str, Dict[str, Any]]]:
    if previous_specification is None:
        return {
            "preserved_dimensions": {},
            "preserved_features": {},
            "preserved_assumptions": {},
        }
    clarifications = user_inputs.get("clarifications", {})
    build_repair_requested = bool(str(clarifications.get("build_repair") or "").strip())
    superseded_ids = _clarification_superseded_ids(previous_specification, user_inputs)

    def superseded(item_id: str) -> bool:
        return item_id in superseded_ids

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
    # A build_repair diagnostic invalidates accepted feature geometry: the planner must be free to
    # correct any feature's placement or parameters, so features are not byte-exact preserved then.
    features = {} if build_repair_requested else {
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


def draft_builder_tools(*, incremental: bool = False) -> List[Dict[str, Any]]:
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
    if incremental:
        tools.extend([
            {"type": "function", "function": {"name": "remove_item", "parameters": {"type": "object", "additionalProperties": False, "required": ["item_type", "id", "reason"], "properties": {"item_type": {"enum": ["dimension", "feature", "assumption", "question", "annotation"]}, "id": {"type": "string"}, "reason": {"type": "string"}}}}},
            {"type": "function", "function": {"name": "resolve_question", "parameters": {"type": "object", "additionalProperties": False, "required": ["id"], "properties": {"id": {"type": "string"}}}}},
        ])
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
    previous_specification: DraftSpecification | None = None,
    locked_items: set[tuple[str, str]] | None = None,
    incremental: bool = False,
    answered_question_ids: set[str] | None = None,
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
            required_dimension_ids=required_dimension_ids,
            required_feature_ids=required_feature_ids,
            required_assumption_ids=required_assumption_ids,
            preserved_dimensions=preserved_dimensions,
            preserved_features=preserved_features,
            preserved_assumptions=preserved_assumptions,
            planner_run_id=planner_run_id,
            planner_mode=planner_mode,
            previous_specification=previous_specification,
            locked_items=locked_items,
            incremental=incremental,
            answered_question_ids=answered_question_ids,
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
    required_dimension_ids: set[str] | None = None,
    required_feature_ids: set[str] | None = None,
    required_assumption_ids: set[str] | None = None,
    preserved_dimensions: Dict[str, Dict[str, Any]] | None = None,
    preserved_features: Dict[str, Dict[str, Any]] | None = None,
    preserved_assumptions: Dict[str, Dict[str, Any]] | None = None,
    planner_run_id: str | None = None,
    planner_mode: str = "initial",
    previous_specification: DraftSpecification | None = None,
    locked_items: set[tuple[str, str]] | None = None,
    incremental: bool = False,
    answered_question_ids: set[str] | None = None,
    _run_stats: Dict[str, Any] | None = None,
) -> DraftSpecification:
    builder = DraftBuilder.seed(previous_specification, locked_items or set()) if incremental and previous_specification else DraftBuilder(analysis)
    messages = list(payload["messages"])
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://easycad.local",
        "X-Title": "EasyCAD",
    }
    last_results: List[Dict[str, Any]] = []
    required_feature_replacements: Dict[str, frozenset[str]] = {}
    last_finish_failure: tuple[str, str] | None = None
    repeated_finish_failures = 0
    turn_cap = MAX_INCREMENTAL_REPLAN_TURNS if incremental else MAX_DRAFT_PLANNER_TURNS
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
                if incremental:
                    unresolved_answers = sorted((answered_question_ids or set()) & {item.id for item in builder.draft.questions})
                    from .draft_lint import lint_draft
                    lint_errors = [item.message for item in lint_draft(builder.draft).issues if item.severity == "error"]
                    reference_issues = builder.reference_issues()
                    if unresolved_answers or lint_errors or reference_issues:
                        result = {"ok": False, "field": "incremental_replan", "message": "; ".join([
                            *([f"Answered questions still unresolved: {', '.join(unresolved_answers)}"] if unresolved_answers else []),
                            *reference_issues, *lint_errors,
                        ])}
                    else:
                        return builder.finish()
                elif not builder.metadata_set:
                    result = {"ok": False, "field": "title", "message": "set_draft_metadata must be called before finish_draft"}
                elif not builder.draft.features:
                    result = {"ok": False, "field": "features", "message": "draft must contain at least one feature before finish_draft"}
                elif planner_mode == "initial":
                    # The initial pass is intentionally permissive: minimal_reliable_draft
                    # immediately filters this raw draft into a compilable body plus omissions.
                    return builder.finish()
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
                        def _comparable(kind: str, payload: Dict[str, Any] | None) -> Dict[str, Any] | None:
                            # affected_ids is bookkeeping that legitimately changes when a clarification
                            # restructures the referenced geometry; the reviewed content is the rest.
                            if kind == "assumption" and isinstance(payload, dict):
                                return {key: value for key, value in payload.items() if key != "affected_ids"}
                            return payload

                        changed_items = [
                            (kind, item_id, expected_payload)
                            for kind, expected, actual in preserved_items
                            for item_id, expected_payload in expected.items()
                            if _comparable(kind, actual.get(item_id)) != _comparable(kind, expected_payload)
                        ]
                        required_feature_replacements = {
                            item_id: frozenset(expected_payload.get("source_feature_ids", []))
                            for kind, item_id, expected_payload in changed_items
                            if kind == "feature" and expected_payload.get("source_feature_ids")
                        }
                        from .draft_lint import lint_draft
                        lint_result = lint_draft(builder.draft)
                        lint_errors = [issue.message for issue in lint_result.issues if issue.severity == "error"]
                        reference_issues = builder.reference_issues()
                        if changed_items or reference_issues or lint_errors:
                            changed = [f"{kind}:{item_id}" for kind, item_id, _ in changed_items]
                            restore_hint = (
                                (
                                    f"Confirmed items changed without a clarification: {', '.join(changed)}. "
                                    "Restore each by calling its add_ tool again with the same ID (a repeated ID replaces "
                                    "the stored item) and exactly the stored payload. Expected payloads: "
                                    + json.dumps(
                                        {item_id: expected_payload for _, item_id, expected_payload in changed_items[:3]},
                                        ensure_ascii=False,
                                    )
                                )
                                if changed_items
                                else ""
                            )
                            result = {
                                "ok": False,
                                "field": "complete_replan",
                                "message": "; ".join([
                                    *([restore_hint] if restore_hint else []),
                                    *reference_issues,
                                    *lint_errors,
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
            elif name == "remove_item": result = builder.remove_item(args)
            elif name == "resolve_question": result = builder.resolve_question(args)
            elif name.startswith("add_"):
                feature_id = str(args.get("id", ""))
                source_ids = frozenset(str(item) for item in args.get("source_feature_ids", []))
                replacement_ids = sorted(
                    expected_id for expected_id, expected_sources in required_feature_replacements.items()
                    if expected_sources == source_ids and expected_id != feature_id
                )
                if replacement_ids:
                    result = {
                        "ok": False,
                        "field": "id",
                        "message": (
                            f"This source feature must replace the existing feature ID {replacement_ids[0]!r}; "
                            f"do not create alias ID {feature_id!r}. Call {name} again with id={replacement_ids[0]!r}."
                        ),
                    }
                else:
                    result = builder.add_feature(args)
            else: result = {"ok": False, "message": "unknown tool"}
            last_results.append({"tool": name, **result})
            if _run_stats is not None and not result.get("ok", False):
                _run_stats["tool_errors"] += 1
                if name == "finish_draft":
                    reason = str(result.get("field", "unknown"))
                    rejections = _run_stats["finish_rejections"]
                    rejections[reason] = rejections.get(reason, 0) + 1
            elif _run_stats is not None and name == "remove_item":
                _run_stats["removed_items"].append({"item": result.get("removed"), "reason": result.get("reason")})
            last_results = last_results[-8:]
            messages.append({"role": "tool", "tool_call_id": call["id"], "content": json.dumps(result)})
            if name == "finish_draft" and not result.get("ok", False):
                signature = (str(result.get("field", "unknown")), str(result.get("message", "")))
                repeated_finish_failures = repeated_finish_failures + 1 if signature == last_finish_failure else 1
                last_finish_failure = signature
                if repeated_finish_failures >= 3:
                    raise GenerationError(
                        "draft_specification",
                        "Planner made no progress after three identical finish_draft rejections",
                        {"field": signature[0], "message": signature[1], "planner_run_id": planner_run_id},
                        planner_outcome="planner_stopped",
                    )
    _dump_planner_context(planner_run_id, {**payload, "messages": messages})
    raise GenerationError(
        "draft_specification",
        f"Planner exceeded the {turn_cap}-turn limit before completing the draft",
        {
            "last_tool_results": last_results,
            "max_provider_turns": turn_cap,
            "planner_run_id": planner_run_id,
            "planner_mode": planner_mode,
        },
        planner_outcome="turn_limit",
    )


def _dump_planner_context(planner_run_id: str | None, request_payload: Dict[str, Any]) -> None:
    """Persist the complete provider conversation of a failed planner run for offline replay."""
    try:
        LLM_LOG_DIR.mkdir(parents=True, exist_ok=True)
        path = LLM_LOG_DIR / f"planner_context_{planner_run_id or 'unknown'}.json"
        path.write_text(json.dumps(request_payload, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.warning("Planner context dumped to %s", path)
    except OSError as exc:
        logger.warning("Failed to dump planner context: %s", exc)


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
