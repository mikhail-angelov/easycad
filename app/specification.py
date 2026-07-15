"""Deterministic validation and editing for the pre-CAD user specification."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, List

from pydantic import ValidationError as PydanticValidationError

from .expressions import ExpressionError, evaluate_expression
from .feature_compiler import compile_project_feature_graph, compiler_operation_types
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
    ParameterValue,
    SpecificationFeature,
)


@dataclass
class SpecificationValidationError(ValueError):
    field_ids: List[str]
    messages: List[str]

    def __str__(self) -> str:
        return "; ".join(self.messages)


def apply_specification_edits(
    specification: DraftSpecification,
    values: Dict[str, ParameterValue],
    accepted_assumption_ids: List[str],
    free_text: str,
    accepted_feature_ids: List[str] | None = None,
) -> DraftSpecification:
    updated = specification.model_copy(deep=True)
    dimensions = {item.id: item for item in updated.dimensions}
    for field_id, value in values.items():
        if field_id not in dimensions:
            raise SpecificationValidationError([field_id], [f"Unknown dimension '{field_id}'"])
        dimension = dimensions[field_id]
        dimension.value = value
        dimension.expression = None
        dimension.status = "confirmed"
        dimension.source = "manual"
    accepted = set(accepted_assumption_ids)
    for assumption in updated.assumptions:
        if assumption.id in accepted:
            assumption.status = "confirmed"
    accepted_features = set(accepted_feature_ids or [])
    for feature in updated.features:
        if feature.id in accepted_features:
            feature.status = "confirmed"
    updated.free_text = free_text.strip()
    return updated


def validate_specification(specification: DraftSpecification) -> Dict[str, float]:
    field_ids: List[str] = []
    messages: List[str] = []
    values: Dict[str, float] = {}
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
            continue
        value = float(dimension.value)
        if not math.isfinite(value) or value <= 0:
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
            if not math.isfinite(value) or value <= 0:
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
    for assumption in specification.assumptions:
        if assumption.status == "assumed":
            field_ids.append(assumption.id)
            messages.append(f"{assumption.id} must be accepted or replaced")
    known_features = set()
    for feature in specification.features:
        if feature.status == "unsupported":
            known_features.add(feature.id)
            continue
        if feature.status in {"needs_input", "conflicted", "assumed"}:
            field_ids.append(feature.id)
            messages.append(f"{feature.id} requires input")
        if feature.type not in compiler_operation_types():
            field_ids.append(feature.id)
            messages.append(f"{feature.id} uses unsupported operation type {feature.type}")
        try:
            placement = FeaturePlacement.model_validate(feature.placement).model_dump(exclude_none=True)
        except PydanticValidationError:
            field_ids.append(feature.id)
            messages.append(f"{feature.id} placement is invalid")
            placement = {}
        if feature.operation in {"cut", "intersect", "modify", "pattern"} and not feature.target:
            field_ids.append(feature.id)
            messages.append(f"{feature.id} requires a target")
        if feature.target and feature.target not in known_features:
            field_ids.append(feature.id)
            messages.append(f"{feature.id} targets an unknown or later feature")
        for field in feature.critical_fields:
            if field in {"placement", "position"} and placement:
                continue
            if field not in feature.parameters and field not in placement:
                field_ids.append(feature.id)
                messages.append(f"{feature.id} is missing {field}")
        known_features.add(feature.id)
    if not known_features:
        field_ids.append("features")
        messages.append("specification must include at least one supported feature")
    if messages:
        raise SpecificationValidationError(list(dict.fromkeys(field_ids)), list(dict.fromkeys(messages)))
    return values


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
                placement=placement,
                source_feature_ids=[feature.id],
                confidence=feature.confidence,
                status="implemented",
                implementation=feature.id,
                capability_status="supported",
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
            entries=[FeatureCoverageEntry(feature_id=item.id, operation_ids=[item.id], status="implemented", confidence=item.confidence) for item in specification.features]
        ),
        feature_summary=summaries,
        assumptions=[item.rationale for item in specification.assumptions if item.status == "confirmed"],
        cad=CADSource(source="", source_kind="compiled"),
    )
    return compile_project_feature_graph(project)
