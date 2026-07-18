"""Small server-owned builder for planner tool calls."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError

from .feature_compiler import feature_contract_issues
from .models import DrawingAnalysis, DraftSpecification, SpecificationDimension, SpecificationFeature


@dataclass
class DraftBuilder:
    analysis: dict[str, Any]
    draft: DraftSpecification = field(default_factory=DraftSpecification)
    metadata_set: bool = False

    def __post_init__(self) -> None:
        from .ai_generation import normalize_drawing_analysis
        self.analysis = normalize_drawing_analysis(self.analysis)
        self.draft.analysis = DrawingAnalysis.model_validate(self.analysis)

    @classmethod
    def seed(cls, previous: DraftSpecification) -> "DraftBuilder":
        return cls(previous.analysis.model_dump(mode="json"), previous.model_copy(deep=True), True)

    def set_metadata(self, payload: dict[str, Any]) -> dict[str, Any]:
        if isinstance(payload.get("units"), str) and payload["units"].strip().lower() in {"millimeter", "millimeters"}:
            payload["units"] = "mm"
        if self.metadata_set or set(payload) != {"title", "units"}:
            return {"ok": False, "message": "set_draft_metadata requires title and mm units once"}
        if not isinstance(payload["title"], str) or not payload["title"].strip() or payload["units"] != "mm":
            return {"ok": False, "message": "title must be non-empty and units must be mm"}
        self.draft.title, self.draft.units, self.metadata_set = payload["title"], "mm", True
        return {"ok": True}

    def add_dimension(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            item = SpecificationDimension.model_validate(payload)
        except ValidationError as exc:
            return {"ok": False, "error": exc.errors()[0]}
        if any(feature.id == item.id for feature in self.draft.features):
            return {"ok": False, "message": f"duplicate id '{item.id}'"}
        return _store(self.draft.dimensions, item, "dimension_id")

    def add_feature(self, payload: dict[str, Any]) -> dict[str, Any]:
        _normalize_placement(payload)
        if str(payload.get("target", "")).strip().lower() in {"", "none", "null"}:
            payload["target"] = None
        try:
            item = SpecificationFeature.model_validate(payload)
        except ValidationError as exc:
            return {"ok": False, "error": exc.errors()[0]}
        if any(dimension.id == item.id for dimension in self.draft.dimensions):
            return {"ok": False, "message": f"duplicate id '{item.id}'"}
        if issues := feature_contract_issues(item):
            return {"ok": False, "message": issues[0]}
        known_dimensions = {dimension.id for dimension in self.draft.dimensions}
        unknown = sorted(ref for ref in _references(item) if ref not in known_dimensions)
        if unknown:
            return {"ok": False, "message": f"{item.id} references undeclared dimensions: {', '.join(unknown)}"}
        if item.target and not any(feature.id == item.target for feature in self.draft.features):
            return {"ok": False, "message": f"{item.id} targets missing feature '{item.target}'"}
        return _store(self.draft.features, item, "feature_id")

    def finish(self) -> DraftSpecification:
        if not self.metadata_set:
            raise ValueError("set_draft_metadata must be called before finish_draft")
        return self.draft


def _store(items: list, item: Any, key: str) -> dict[str, Any]:
    for index, existing in enumerate(items):
        if existing.id == item.id:
            items[index] = item
            return {"ok": True, key: item.id, "replaced": True}
    items.append(item)
    return {"ok": True, key: item.id}


def _normalize_placement(payload: dict[str, Any]) -> None:
    value = payload.get("placement")
    if not isinstance(value, str):
        return
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError:
        decoded = None
    if isinstance(decoded, dict):
        payload["placement"] = decoded
        return
    match = re.fullmatch(r"\s*(XY|XZ|YZ)\s*(?:\(\s*(.*?)\s*\))?\s*", value, re.IGNORECASE)
    if not match:
        match = re.fullmatch(
            r"\s*(?:(XY|XZ|YZ)\s*[;,]?)?\s*origin\s*[:=]\s*\[\s*(.*?)\s*\]\s*",
            value,
            re.IGNORECASE,
        )
        if not match:
            return
    origin_text = match.group(2)
    origin = [_placement_value(part) for part in origin_text.split(",")] if origin_text else None
    placement = {"origin": origin} if origin and len(origin) == 3 else {}
    if match.group(1):
        placement["plane"] = match.group(1).upper()
    payload["placement"] = placement


def _placement_value(value: str) -> int | float | str:
    value = value.strip()
    if re.fullmatch(r"[+-]?\d+", value):
        return int(value)
    if re.fullmatch(r"[+-]?(?:\d+\.\d*|\.\d+)", value):
        return float(value)
    return value


def _references(feature: SpecificationFeature) -> list[str]:
    values = [*feature.parameters.values(), *(feature.placement.origin or [])]
    if feature.profile:
        values.extend(feature.profile.dimensions.values())
        values.extend(value for point in feature.profile.points for value in point)
    if feature.pattern:
        values.extend(value for value in (feature.pattern.count, feature.pattern.pitch, feature.pattern.angle_deg, feature.pattern.start_margin, feature.pattern.end_margin) if value is not None)
    return [value for value in values if isinstance(value, str)]
