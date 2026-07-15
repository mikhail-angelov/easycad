"""Deterministic validation and editing for the pre-CAD user specification."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, List

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
    SpecificationDimension,
    SpecificationQuestion,
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


def apply_clarification_patch(
    specification: DraftSpecification,
    question_id: str,
    patch: Dict[str, object],
) -> DraftSpecification:
    """Apply a planner proposal without allowing it to rewrite the specification."""
    updated = specification.model_copy(deep=True)
    question = next((item for item in updated.questions if item.id == question_id), None)
    if question is None:
        raise SpecificationValidationError([question_id], [f"Unknown clarification question '{question_id}'"])

    unresolved_ids = {
        item.id for item in updated.dimensions if item.status != "confirmed"
    } | {item.id for item in updated.features if item.status != "confirmed"}
    allowed_ids = unresolved_ids | {question.field_id}

    dimensions = {item.id: item for item in updated.dimensions}
    values = patch.get("dimension_values", {})
    if not isinstance(values, dict):
        raise SpecificationValidationError([question_id], ["Clarification patch has invalid dimension values"])
    for field_id, value in values.items():
        if field_id not in dimensions or field_id not in allowed_ids:
            raise SpecificationValidationError([str(field_id)], ["Clarification patch changed an unrelated dimension"])
        dimension = dimensions[field_id]
        replacement = SpecificationDimension.model_validate(
            {**dimension.model_dump(), "value": value, "expression": None, "status": "assumed", "source": "inferred"}
        )
        updated.dimensions[updated.dimensions.index(dimension)] = replacement

    features = {item.id: item for item in updated.features}
    feature_updates = patch.get("feature_updates", {})
    if not isinstance(feature_updates, dict):
        raise SpecificationValidationError([question_id], ["Clarification patch has invalid feature updates"])
    for feature_id, changes in feature_updates.items():
        if feature_id not in features or feature_id not in allowed_ids or not isinstance(changes, dict):
            raise SpecificationValidationError([str(feature_id)], ["Clarification patch changed an unrelated feature"])
        allowed_fields = {"parameters", "placement", "target"}
        if set(changes) - allowed_fields:
            raise SpecificationValidationError([str(feature_id)], ["Clarification patch changed unsupported feature fields"])
        replacement = SpecificationFeature.model_validate({**features[feature_id].model_dump(), **changes, "status": "assumed"})
        index = updated.features.index(features[feature_id])
        updated.features[index] = replacement

    unresolved_question = patch.get("unresolved_question")
    if unresolved_question is not None:
        if not isinstance(unresolved_question, dict):
            raise SpecificationValidationError([question_id], ["Clarification patch has an invalid unresolved question"])
        replacement = SpecificationQuestion.model_validate({**question.model_dump(), **unresolved_question, "id": question.id, "field_id": question.field_id})
        updated.questions[updated.questions.index(question)] = replacement
    elif patch.get("resolved_question") is True:
        updated.questions.remove(question)
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
        if feature.status in {"needs_input", "conflicted", "assumed"}:
            field_ids.append(feature.id)
            messages.append(f"{feature.id} requires input")
        if feature.type not in compiler_operation_types():
            field_ids.append(feature.id)
            messages.append(f"{feature.id} uses unsupported operation type {feature.type}")
        if feature.operation in {"cut", "intersect", "modify", "pattern"} and not feature.target:
            field_ids.append(feature.id)
            messages.append(f"{feature.id} requires a target")
        if feature.target and feature.target not in known_features:
            field_ids.append(feature.id)
            messages.append(f"{feature.id} targets an unknown or later feature")
        for field in feature.critical_fields:
            if field not in feature.parameters and field not in feature.placement:
                field_ids.append(feature.id)
                messages.append(f"{feature.id} is missing {field}")
        known_features.add(feature.id)
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
        placement = FeaturePlacement.model_validate(feature.placement) if feature.placement else None
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
