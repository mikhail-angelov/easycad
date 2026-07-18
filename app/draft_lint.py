"""Deterministic, provider-free checks over draft geometry."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from .models import DraftSpecification, SpecificationFeature
from .specification import resolve_dimension_values


TOLERANCE_MM = 0.5


@dataclass(frozen=True)
class Extents:
    minimum: tuple[float, float, float]
    maximum: tuple[float, float, float]


@dataclass(frozen=True)
class LintSuggestion:
    feature_id: str
    field_path: str
    value: float


@dataclass(frozen=True)
class LintIssue:
    rule: str
    severity: Literal["error", "warning"]
    feature_ids: list[str]
    message: str
    qualifier: str | None = None
    suggestion: LintSuggestion | None = None

    @property
    def issue_id(self) -> str:
        subject = "+".join(sorted(self.feature_ids))
        return "@".join(part for part in (self.rule, subject, self.qualifier) if part)

    def as_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "rule": self.rule,
            "issue_id": self.issue_id,
            "severity": self.severity,
            "feature_ids": self.feature_ids,
            "message": self.message,
        }
        if self.suggestion:
            payload["suggestion"] = self.suggestion.__dict__
        return payload


@dataclass(frozen=True)
class LintResult:
    issues: list[LintIssue] = field(default_factory=list)
    unevaluated_feature_ids: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, object]:
        return {"issues": [item.as_dict() for item in self.issues], "unevaluated_feature_ids": self.unevaluated_feature_ids}


def _number(value: object, values: dict[str, float]) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        return values.get(value)
    return None


def _origin(feature: SpecificationFeature, values: dict[str, float]) -> tuple[float, float, float] | None:
    raw = feature.placement.origin or [0, 0, 0]
    resolved = [_number(value, values) for value in raw]
    if any(value is None for value in resolved):
        return None
    return tuple(float(value) for value in resolved)  # type: ignore[arg-type,return-value]


def _oriented_extent(origin: tuple[float, float, float], plane: str, u: tuple[float, float], v: tuple[float, float], depth: float) -> Extents:
    # Workplane local x/y axes: XY -> X/Y, XZ -> X/Z, YZ -> Y/Z.
    if plane == "XZ":
        minimum = (origin[0] + u[0], origin[1], origin[2] + v[0])
        maximum = (origin[0] + u[1], origin[1] + depth, origin[2] + v[1])
    elif plane == "YZ":
        minimum = (origin[0], origin[1] + u[0], origin[2] + v[0])
        maximum = (origin[0] + depth, origin[1] + u[1], origin[2] + v[1])
    else:
        minimum = (origin[0] + u[0], origin[1] + v[0], origin[2])
        maximum = (origin[0] + u[1], origin[1] + v[1], origin[2] + depth)
    return Extents(tuple(min(a, b) for a, b in zip(minimum, maximum)), tuple(max(a, b) for a, b in zip(minimum, maximum)))


def resolved_feature_extent(feature: SpecificationFeature, values: dict[str, float]) -> Extents | None:
    origin = _origin(feature, values)
    if origin is None:
        return None
    plane = feature.placement.plane or "XY"
    parameters = feature.parameters
    if feature.type == "box":
        length, width, height = (_number(parameters.get(name), values) for name in ("length", "width", "height"))
        if None in (length, width, height) or plane != "XY":
            return None
        return _oriented_extent(origin, plane, (0, float(length)), (0, float(width)), float(height))
    if feature.type == "cylinder":
        radius, height = (_number(parameters.get(name), values) for name in ("radius", "height"))
        if radius is None or height is None:
            return None
        return _oriented_extent(origin, plane, (-radius, radius), (-radius, radius), height)
    if feature.type in {"hole", "through_hole"}:
        diameter, depth = (_number(parameters.get(name), values) for name in ("diameter", "depth"))
        if diameter is None or depth is None:
            return None
        radius = diameter / 2
        return _oriented_extent(origin, plane, (-radius, radius), (-radius, radius), depth)
    if feature.type in {"pocket", "slot"}:
        length, width, depth = (_number(parameters.get(name), values) for name in ("length", "width", "depth"))
        if None in (length, width, depth):
            return None
        # CadQuery rect/slot profiles are centered in their workplane.
        return _oriented_extent(origin, plane, (-float(length) / 2, float(length) / 2), (-float(width) / 2, float(width) / 2), float(depth))
    return None


def _intersection(a: Extents, b: Extents) -> Extents | None:
    minimum = tuple(max(a.minimum[i], b.minimum[i]) for i in range(3))
    maximum = tuple(min(a.maximum[i], b.maximum[i]) for i in range(3))
    if any(maximum[i] < minimum[i] - TOLERANCE_MM for i in range(3)):
        return None
    return Extents(minimum, maximum)


def _footprint_overlap(cut: Extents, target: Extents, plane: str) -> float:
    axes = (0, 2) if plane == "XZ" else (1, 2) if plane == "YZ" else (0, 1)
    cut_area = 1.0
    overlap_area = 1.0
    for axis in axes:
        cut_size = max(0.0, cut.maximum[axis] - cut.minimum[axis])
        overlap = max(0.0, min(cut.maximum[axis], target.maximum[axis]) - max(cut.minimum[axis], target.minimum[axis]))
        cut_area *= cut_size
        overlap_area *= overlap
    return overlap_area / cut_area if cut_area else 0.0


def _cut_penetrates_target(cut: Extents, target: Extents, plane: str) -> bool:
    axis = 1 if plane == "XZ" else 0 if plane == "YZ" else 2
    overlap = min(cut.maximum[axis], target.maximum[axis]) - max(cut.minimum[axis], target.minimum[axis])
    return overlap > TOLERANCE_MM


def lint_draft(draft: DraftSpecification) -> LintResult:
    values, _ = resolve_dimension_values(draft)
    extents: dict[str, Extents] = {}
    unevaluated: list[str] = []
    for feature in draft.features:
        if feature.status == "unsupported":
            continue
        extent = resolved_feature_extent(feature, values)
        if extent is None and feature.type in {"box", "cylinder", "hole", "through_hole", "pocket", "slot"}:
            unevaluated.append(feature.id)
        elif extent is not None:
            extents[feature.id] = extent

    issues: list[LintIssue] = []
    for feature in draft.features:
        if feature.status == "unsupported":
            continue
        extent = extents.get(feature.id)
        if extent is None:
            continue
        if feature.type == "slot":
            length = _number(feature.parameters.get("length"), values)
            width = _number(feature.parameters.get("width"), values)
            if length is not None and width is not None and length <= width + TOLERANCE_MM:
                issues.append(LintIssue(
                    "slot_degenerate", "error", [feature.id],
                    f"{feature.id} has no straight slot section: length must exceed width",
                ))
        origin = _origin(feature, values)
        for index, (axis, coordinate) in enumerate(zip("xyz", origin or (0, 0, 0))):
            if coordinate < -TOLERANCE_MM:
                issues.append(LintIssue("negative_origin", "error", [feature.id], f"{feature.id} has a negative {axis.upper()} origin", axis, LintSuggestion(feature.id, f"placement.origin[{index}]", 0)))
        if feature.target and feature.target in extents:
            target = extents[feature.target]
            if feature.operation == "cut":
                if _intersection(extent, target) is None or not _cut_penetrates_target(extent, target, feature.placement.plane or "XY"):
                    issues.append(LintIssue("cut_misses_target", "error", [feature.id, feature.target], f"{feature.id} does not intersect target {feature.target}"))
                if _footprint_overlap(extent, target, feature.placement.plane or "XY") < 0.25:
                    issues.append(LintIssue("cut_mostly_outside_target", "warning", [feature.id, feature.target], f"Most of {feature.id} lies outside target {feature.target}"))
            elif feature.operation == "add" and _intersection(extent, target) is None:
                issues.append(LintIssue("additive_disconnected", "error", [feature.id, feature.target], f"{feature.id} is disconnected from target {feature.target}"))
            if feature.type == "through_hole":
                plane = feature.placement.plane or "XY"
                axis = 1 if plane == "XZ" else 0 if plane == "YZ" else 2
                if extent.maximum[axis] - extent.minimum[axis] + TOLERANCE_MM < target.maximum[axis] - target.minimum[axis]:
                    issues.append(LintIssue("through_hole_short", "warning", [feature.id, feature.target], f"{feature.id} does not span target {feature.target}"))

    additive = [extents[item.id] for item in draft.features if item.status != "unsupported" and item.operation == "add" and item.id in extents]
    if additive:
        union_min = tuple(min(item.minimum[i] for item in additive) for i in range(3))
        union_max = tuple(max(item.maximum[i] for item in additive) for i in range(3))
        union_sizes = tuple(union_max[i] - union_min[i] for i in range(3))
        fallback = {"x": ("overall_length", "length"), "y": ("overall_width", "width", "depth", "overall_depth"), "z": ("overall_height", "height")}
        for index, axis in enumerate("xyz"):
            dimensions = [item for item in draft.dimensions if item.role == f"overall_{axis}"]
            if not dimensions:
                by_id = {item.id: item for item in draft.dimensions}
                dimensions = [by_id[name] for name in fallback[axis] if name in by_id][:1]
            if dimensions and dimensions[0].id in values and abs(union_sizes[index] - values[dimensions[0].id]) > TOLERANCE_MM:
                issues.append(LintIssue("overall_extent_mismatch", "warning", [item.id for item in draft.features if item.status != "unsupported" and item.operation == "add" and item.id in extents], f"Additive {axis.upper()} extent does not match {dimensions[0].id}", axis))

    return LintResult(issues, sorted(set(unevaluated)))
