"""Server-owned transaction for narrow LLM draft-building tools."""

from __future__ import annotations

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
        if any(item.id == dimension.id for item in self.draft.dimensions + self.draft.features):
            return {"ok": False, "field": "id", "message": f"duplicate dimension id '{dimension.id}'"}
        self.draft.dimensions.append(dimension)
        return {"ok": True, "dimension_id": dimension.id}

    def add_feature(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            feature = SpecificationFeature.model_validate(payload)
        except ValidationError as exc:
            return {"ok": False, "error": exc.errors()[0]}
        if any(item.id == feature.id for item in self.draft.features + self.draft.dimensions):
            return {"ok": False, "field": "id", "message": f"duplicate feature id '{feature.id}'"}
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
        self.draft.features.append(feature)
        return {"ok": True, "feature_id": feature.id}

    def add_assumption(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._append("assumptions", SpecificationAssumption, payload)

    def add_question(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._append("questions", SpecificationQuestion, payload)

    def add_annotation(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._append("annotations", SpecificationAnnotation, payload)

    def _append(self, field_name: str, model: Any, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            item = model.model_validate(payload)
        except ValidationError as exc:
            return {"ok": False, "error": exc.errors()[0]}
        items = getattr(self.draft, field_name)
        if any(existing.id == item.id for existing in items):
            return {"ok": False, "field": "id", "message": f"duplicate id '{item.id}'"}
        items.append(item)
        return {"ok": True, "id": item.id}

    def finish(self) -> DraftSpecification:
        if not self.metadata_set:
            raise ValueError("set_draft_metadata must be called before finish_draft")
        self.draft.analysis = DrawingAnalysis.model_validate(self.analysis)
        return self.draft

    def reference_issues(self) -> list[str]:
        return review_reference_issues(self.draft)


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
