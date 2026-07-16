"""Server-owned transaction for narrow LLM draft-building tools."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError

from .feature_compiler import feature_contract_issues
from .models import (
    DrawingAnalysis, DraftSpecification, SpecificationAnnotation, SpecificationAssumption,
    SpecificationDimension, SpecificationFeature, SpecificationQuestion,
)
from .specification import review_reference_issues


@dataclass
class DraftBuilder:
    analysis: dict[str, Any]
    draft: DraftSpecification = field(default_factory=DraftSpecification)
    metadata_set: bool = False

    def set_metadata(self, payload: dict[str, Any]) -> dict[str, Any]:
        if self.metadata_set:
            return {"ok": False, "field": "metadata", "message": "set_draft_metadata may only be called once"}
        if set(payload) != {"title", "units"}:
            return {"ok": False, "field": "metadata", "message": "set_draft_metadata requires only title and units"}
        title, units = payload["title"], payload["units"]
        if not isinstance(title, str) or not title.strip():
            return {"ok": False, "field": "title", "message": "title must be a non-empty string"}
        if units != "mm":
            return {"ok": False, "field": "units", "message": "units must be mm"}
        self.draft.title, self.draft.units = title, units
        self.metadata_set = True
        return {"ok": True}

    def add_dimension(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            dimension = SpecificationDimension.model_validate(payload)
        except ValidationError as exc:
            return {"ok": False, "error": exc.errors()[0]}
        if any(item.id == dimension.id for item in self.draft.features):
            return {"ok": False, "field": "id", "message": f"duplicate dimension id '{dimension.id}' is already a feature id"}
        replaced = _replace_or_append(self.draft.dimensions, dimension)
        return {"ok": True, "dimension_id": dimension.id, **({"replaced": True} if replaced else {})}

    def add_feature(self, payload: dict[str, Any]) -> dict[str, Any]:
        _normalize_compact_placement(payload)
        try:
            feature = SpecificationFeature.model_validate(payload)
        except ValidationError as exc:
            return {"ok": False, "error": exc.errors()[0]}
        if any(item.id == feature.id for item in self.draft.dimensions):
            return {"ok": False, "field": "id", "message": f"duplicate feature id '{feature.id}' is already a dimension id"}
        issues = feature_contract_issues(feature)  # same contract as Build
        if issues:
            return {"ok": False, "field": feature.id, "message": issues[0]}
        known_dimensions = {item.id for item in self.draft.dimensions}
        unknown_references = sorted(
            reference for reference in _feature_dimension_references(feature) if reference not in known_dimensions
        )
        if unknown_references:
            return {
                "ok": False,
                "field": feature.id,
                "message": (
                    f"{feature.id} references undeclared dimensions: {', '.join(unknown_references)}. "
                    "Coordinates and parameters must be numbers or declared dimension IDs; add a derived dimension "
                    "first instead of using an inline expression."
                ),
            }
        if feature.target and not any(item.id == feature.target for item in self.draft.features):
            return {"ok": False, "field": feature.id, "message": f"{feature.id} targets missing or later feature '{feature.target}'"}
        if feature.type in {"fillet", "chamfer"} and feature.placement.reference in {
            item.id for item in self.draft.features
        }:
            return {
                "ok": False,
                "field": feature.id,
                "message": (
                    f"{feature.id} placement.reference is an edge selector, not a feature ID. "
                    "Keep the feature ID only in target; omit placement.reference or use a valid CadQuery selector such as '>Z'."
                ),
            }
        replaced = _replace_or_append(self.draft.features, feature)
        return {"ok": True, "feature_id": feature.id, **({"replaced": True} if replaced else {})}

    def add_assumption(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._append("assumptions", SpecificationAssumption, payload)

    def add_question(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._append("questions", SpecificationQuestion, payload, self._question_reference_issue)

    def add_annotation(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._append("annotations", SpecificationAnnotation, payload, self._annotation_reference_issue)

    def _append(self, field_name: str, model: Any, payload: dict[str, Any], reference_issue: Any = None) -> dict[str, Any]:
        try:
            item = model.model_validate(payload)
        except ValidationError as exc:
            return {"ok": False, "error": exc.errors()[0]}
        if reference_issue is not None:
            issue = reference_issue(item)
            if issue:
                return {"ok": False, "field": "field_id", "message": issue}
        replaced = _replace_or_append(getattr(self.draft, field_name), item)
        return {"ok": True, "id": item.id, **({"replaced": True} if replaced else {})}

    def _question_reference_issue(self, question: SpecificationQuestion) -> str | None:
        known = {item.id for item in self.draft.dimensions + self.draft.features}
        if question.field_id in known:
            return None
        return (
            f"question '{question.id}' field_id '{question.field_id}' is not a declared dimension or feature ID. "
            "First add the item the question asks about — represent an observed but ambiguous shape with a supported "
            "feature type (for example a cylinder cut for a groove) and status needs_input — then re-add this question "
            "with field_id set to that item's ID."
        )

    def _annotation_reference_issue(self, annotation: SpecificationAnnotation) -> str | None:
        known = {item.id for item in self.draft.dimensions + self.draft.features + self.draft.questions}
        missing = sorted(field_id for field_id in {annotation.field_id, *annotation.field_ids} if field_id not in known)
        if not missing:
            return None
        return (
            f"annotation '{annotation.id}' references unknown review items: {', '.join(missing)}. "
            "Annotations may only link to already-added dimension, feature, or question IDs; add those items first."
        )

    def finish(self) -> DraftSpecification:
        if not self.metadata_set:
            raise ValueError("set_draft_metadata must be called before finish_draft")
        self.draft.analysis = DrawingAnalysis.model_validate(self.analysis)
        return self.draft

    def reference_issues(self) -> list[str]:
        return review_reference_issues(self.draft)


_COMPACT_PLACEMENT_PATTERN = re.compile(r"^\s*(XY|XZ|YZ)\s*(?:\(\s*(.*?)\s*\))?\s*$", re.IGNORECASE)
_LOOSE_PLANE_PATTERN = re.compile(r"plane\W*\s*(XY|XZ|YZ)", re.IGNORECASE)
_LOOSE_VECTOR_PATTERN = r"\W*\s*\[([^\]]*)\]"


def _normalize_compact_placement(payload: dict[str, Any]) -> None:
    """Accept known provider string variants of the structured placement/profile/pattern objects."""
    import json as _json

    for key in ("placement", "profile", "pattern"):
        value = payload.get(key)
        if isinstance(value, str):
            try:
                parsed = _json.loads(value)
            except ValueError:
                parsed = None
            if isinstance(parsed, dict):
                payload[key] = parsed
    placement = payload.get("placement")
    if not isinstance(placement, str):
        return
    match = _COMPACT_PLACEMENT_PATTERN.match(placement)
    if match:
        normalized: dict[str, Any] = {"plane": match.group(1).upper()}
        origin = _coordinate_tokens(match.group(2) or "")
        if origin is not None:
            normalized["origin"] = origin
        payload["placement"] = normalized
        return
    # Loose "origin: [x, y, z], plane: XY, axis: [..]" text.
    loose: dict[str, Any] = {}
    plane_match = _LOOSE_PLANE_PATTERN.search(placement)
    if plane_match:
        loose["plane"] = plane_match.group(1).upper()
    vector_match = re.search("origin" + _LOOSE_VECTOR_PATTERN, placement, re.IGNORECASE)
    if vector_match:
        vector = _coordinate_tokens(vector_match.group(1))
        if vector is not None:
            loose["origin"] = vector
    if loose:
        payload["placement"] = loose


def _coordinate_tokens(text: str) -> list[Any] | None:
    text = text.strip()
    if not text:
        return None
    values: list[Any] = []
    for token in text.split(","):
        token = token.strip().strip("'\"")
        try:
            values.append(float(token))
        except ValueError:
            values.append(token)
    return values if len(values) == 3 else None


def _replace_or_append(items: list, item: Any) -> bool:
    """Store `item`, replacing an existing entry with the same ID in place. Returns True on replace."""
    for index, existing in enumerate(items):
        if existing.id == item.id:
            items[index] = item
            return True
    items.append(item)
    return False


def _feature_dimension_references(feature: SpecificationFeature) -> list[str]:
    """Return ID-like string values used by executable feature geometry."""
    references: list[str] = []

    def add(value: object) -> None:
        if isinstance(value, str):
            references.append(value)

    for value in feature.parameters.values():
        add(value)
    if feature.profile:
        for value in feature.profile.dimensions.values():
            add(value)
        for point in feature.profile.points:
            for value in point:
                add(value)
    if feature.pattern:
        for value in (
            feature.pattern.count,
            feature.pattern.pitch,
            feature.pattern.angle_deg,
            feature.pattern.start_margin,
            feature.pattern.end_margin,
        ):
            add(value)
    placement = feature.placement
    if placement and placement.origin:
        for value in placement.origin:
            add(value)
    return references
