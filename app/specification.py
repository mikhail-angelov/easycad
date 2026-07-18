"""Deterministic validation and editing for the pre-CAD user specification."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, List

from pydantic import ValidationError as PydanticValidationError

from .expressions import ExpressionError, evaluate_expression
from .feature_compiler import compile_project_feature_graph, feature_contract_issues
from .models import (
    CADParameter,
    CADProject,
    CADSource,
    DraftSpecification,
    FeatureCoverageEntry,
    FeatureCoverageReport,
    FeatureGraph,
    FeatureOperation,
    FeaturePlacement,
    FeatureSummary,
    SpecificationFeature,
)


@dataclass
class SpecificationValidationError(ValueError):
    field_ids: List[str]
    messages: List[str]

    def __str__(self) -> str:
        return "; ".join(self.messages)


def resolve_dimension_values(specification: DraftSpecification) -> tuple[Dict[str, float], List[str]]:
    """Resolve every safely evaluable numeric dimension without raising."""
    values: Dict[str, float] = {}
    unresolved: set[str] = set()
    pending: Dict[str, str] = {}
    for dimension in specification.dimensions:
        if dimension.critical and dimension.status in {"needs_input", "conflicted"}:
            unresolved.add(dimension.id)
        elif dimension.status == "assumed":
            unresolved.add(dimension.id)
        elif dimension.expression:
            pending[dimension.id] = dimension.expression
        elif isinstance(dimension.value, (int, float)) and not isinstance(dimension.value, bool) and math.isfinite(float(dimension.value)):
            values[dimension.id] = float(dimension.value)
        else:
            unresolved.add(dimension.id)
    while pending:
        progressed = False
        for field_id, expression in list(pending.items()):
            try:
                value = evaluate_expression(expression, values)
            except Exception:
                continue
            if math.isfinite(value):
                values[field_id] = value
                del pending[field_id]
                progressed = True
        if not progressed:
            unresolved.update(pending)
            break
    return values, sorted(unresolved)


def validate_specification(specification: DraftSpecification) -> Dict[str, float]:
    field_ids: List[str] = []
    messages: List[str] = []
    values: Dict[str, float] = {}
    text_value_ids = set()
    signed_text_distance_ids = {
        feature.parameters["distance"]
        for feature in specification.features
        if feature.type == "text" and feature.operation == "cut" and isinstance(feature.parameters.get("distance"), str)
    }
    # Sizes (parameters, profile, pattern) must be positive; placement-origin coordinates may be zero.
    positive_value_ids = {
        parameter_id
        for feature in specification.features
        for parameter_id in _feature_size_references(feature)
        if not (
            feature.type == "text"
            and feature.operation == "cut"
            and parameter_id == feature.parameters.get("distance")
        )
    }
    pending = {}
    for dimension in specification.dimensions:
        if dimension.critical and dimension.status in {"needs_input", "conflicted"}:
            field_ids.append(dimension.id)
            messages.append(f"{dimension.id} requires input")
            continue
        if dimension.status == "assumed":
            field_ids.append(dimension.id)
            messages.append(f"{dimension.id} assumption must be accepted")
            continue
        if dimension.expression:
            pending[dimension.id] = dimension.expression
            continue
        if dimension.value is None:
            field_ids.append(dimension.id)
            messages.append(f"{dimension.id} has no value")
            continue
        if isinstance(dimension.value, str):
            text_value_ids.add(dimension.id)
            continue
        value = float(dimension.value)
        allows_signed_value = dimension.id in signed_text_distance_ids - positive_value_ids
        allows_zero_value = dimension.id not in positive_value_ids
        if not math.isfinite(value) or (value == 0 and not allows_zero_value) or (value < 0 and not allows_signed_value):
            field_ids.append(dimension.id)
            messages.append(f"{dimension.id} must be positive")
            continue
        if dimension.min is not None and value < dimension.min:
            field_ids.append(dimension.id)
            messages.append(f"{dimension.id} is below minimum")
        if dimension.max is not None and value > dimension.max:
            field_ids.append(dimension.id)
            messages.append(f"{dimension.id} is above maximum")
        values[dimension.id] = value
    while pending:
        progressed = False
        for field_id, expression in list(pending.items()):
            try:
                value = evaluate_expression(expression, values)
            except ExpressionError:
                continue
            except Exception:
                field_ids.append(field_id)
                messages.append(f"{field_id} expression is invalid")
                del pending[field_id]
                progressed = True
                continue
            if not math.isfinite(value) or value < 0 or (value == 0 and field_id in positive_value_ids):
                field_ids.append(field_id)
                messages.append(f"{field_id} expression must resolve to a positive value")
            else:
                values[field_id] = value
            del pending[field_id]
            progressed = True
        if not progressed:
            field_ids.extend(sorted(pending))
            messages.append("Could not resolve derived dimensions")
            break
    known_features = set()
    for feature in specification.features:
        if feature.status == "unsupported":
            known_features.add(feature.id)
            continue
        if feature.status in {"needs_input", "conflicted", "assumed"}:
            field_ids.append(feature.id)
            messages.append(f"{feature.id} requires input")
        for issue in feature_contract_issues(feature):
            field_ids.append(feature.id)
            messages.append(f"{feature.id} {issue}")
        try:
            placement = FeaturePlacement.model_validate(feature.placement).model_dump(exclude_none=True)
        except PydanticValidationError:
            field_ids.append(feature.id)
            messages.append(f"{feature.id} placement is invalid")
            placement = {}
        if feature.operation in {"cut", "intersect", "modify", "pattern"} and not feature.target:
            field_ids.append(feature.id)
            messages.append(f"{feature.id} requires a target")
        if feature.operation == "add" and known_features and not feature.target:
            field_ids.append(feature.id)
            messages.append(f"{feature.id} must target the existing body")
        if feature.target and feature.target not in known_features:
            field_ids.append(feature.id)
            messages.append(f"{feature.id} targets an unknown or later feature")
        for field in feature.critical_fields:
            if field.startswith("parameters.") and field.removeprefix("parameters.") in feature.parameters:
                continue
            if field == "profile.points" and feature.profile is not None and feature.profile.points:
                continue
            if field.startswith("profile.dimensions.") and feature.profile is not None and field.removeprefix("profile.dimensions.") in feature.profile.dimensions:
                continue
            if field in {"placement", "position"} and placement:
                continue
            if field == "profile" and feature.profile is not None:
                continue
            if field == "pattern" and feature.pattern is not None:
                continue
            if field == "target" and feature.target:
                continue
            if field not in feature.parameters and field not in placement:
                field_ids.append(feature.id)
                messages.append(f"{feature.id} is missing {field}")
        for parameter_id in _feature_parameter_references(feature):
            if parameter_id in values:
                continue
            if feature.type == "text" and parameter_id == feature.parameters.get("content") and parameter_id in text_value_ids:
                continue
            if parameter_id in text_value_ids:
                field_ids.append(feature.id)
                messages.append(f"{feature.id} uses text dimension '{parameter_id}' where a numeric value is required")
                continue
            if parameter_id not in values:
                field_ids.append(feature.id)
                messages.append(f"{feature.id} references unknown or unresolved dimension '{parameter_id}'")
        known_features.add(feature.id)
    if not known_features:
        field_ids.append("features")
        messages.append("specification must include at least one supported feature")
    if messages:
        raise SpecificationValidationError(list(dict.fromkeys(field_ids)), list(dict.fromkeys(messages)))
    return values


def _feature_size_references(feature: SpecificationFeature) -> List[str]:
    """Dimension IDs used as sizes: feature parameters, profile geometry, and pattern fields."""
    references: List[str] = []

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
    return references


def _feature_parameter_references(feature: SpecificationFeature) -> List[str]:
    """Strings in executable geometry are dimension IDs, never free-form CAD expressions."""
    references = _feature_size_references(feature)
    placement = FeaturePlacement.model_validate(feature.placement)
    if placement.origin:
        for value in placement.origin:
            if isinstance(value, str):
                references.append(value)
    return references


def project_from_specification(specification: DraftSpecification) -> CADProject:
    """Compile a fully confirmed specification into the trusted project representation."""
    validate_specification(specification)
    parameters = {}
    for item in specification.dimensions:
        parameter_type = "expression" if item.expression else "number" if isinstance(item.value, (int, float)) else "text"
        parameters[item.id] = CADParameter(
            label=item.label,
            type=parameter_type,
            value=item.value,
            expression=item.expression,
            unit=item.unit,
            min=item.min,
            max=item.max,
            source=item.source,
            confidence=item.confidence,
            editable=item.expression is None,
        )
    operations = []
    summaries = []
    for feature in specification.features:
        if feature.status == "unsupported":
            continue
        placement_model = FeaturePlacement.model_validate(feature.placement)
        placement = placement_model if placement_model.model_dump(exclude_none=True) else None
        operations.append(
            FeatureOperation(
                id=feature.id,
                name=feature.label,
                type=feature.type,
                operation=feature.operation,
                target=feature.target,
                parameters=feature.parameters,
                profile=feature.profile,
                placement=placement,
                pattern=feature.pattern,
                source_feature_ids=feature.source_feature_ids or [feature.id],
                confidence=feature.confidence,
                status="implemented",
                implementation=feature.id,
                capability_status="experimental",
            )
        )
        summaries.append(FeatureSummary(id=feature.id, name=feature.label, type=feature.type, description=feature.label))
    project = CADProject(
        title=specification.title,
        units=specification.units,
        source=specification.source,
        analysis=specification.analysis,
        parameters=parameters,
        feature_graph=FeatureGraph(operations=operations),
        feature_coverage=FeatureCoverageReport(
            entries=[FeatureCoverageEntry(feature_id=item.id, operation_ids=[] if item.status == "unsupported" else [item.id], status="unsupported" if item.status == "unsupported" else "implemented", confidence=item.confidence, explanation=item.label if item.status == "unsupported" else None) for item in specification.features]
        ),
        feature_summary=summaries,
        assumptions=[],
        cad=CADSource(source="", source_kind="compiled"),
    )
    return compile_project_feature_graph(project)
