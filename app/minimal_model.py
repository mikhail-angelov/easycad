"""Produce the smallest deterministic model that can be compiled from a drawing draft."""

from __future__ import annotations

import math

from .feature_compiler import feature_contract_issues
from .draft_lint import lint_draft
from .models import DraftSpecification, SpecificationFeature
from .specification import project_from_specification, resolve_dimension_values


def minimal_reliable_draft(draft: DraftSpecification) -> DraftSpecification:
    """Keep only confirmed, self-contained geometry; disclose everything else as omitted."""
    result = draft.model_copy(deep=True)
    result.questions = []
    result.annotations = []
    result.assumptions = []
    values, _ = resolve_dimension_values(result)
    text_content_dimensions = {
        str(feature.parameters.get("content"))
        for feature in result.features
        if feature.type == "text" and feature.status == "confirmed" and isinstance(feature.parameters.get("content"), str)
    }
    result.dimensions = [
        item for item in result.dimensions
        if item.status == "confirmed" and (item.id in values or item.id in text_content_dimensions)
    ]
    known_dimensions = {item.id for item in result.dimensions}
    known_features: set[str] = set()
    for feature in result.features:
        if "freeform_instruction" in feature.evidence:
            feature.status = "confirmed"
            feature.source_feature_ids = feature.source_feature_ids or ["freeform_instruction"]
        feature.source_feature_ids = feature.source_feature_ids or [feature.id]
        references = _references(feature)
        valid = (
            feature.status == "confirmed"
            and not feature_contract_issues(feature)
            and references <= known_dimensions
            and (not feature.target or feature.target in known_features)
            and (feature.operation != "add" or not known_features or bool(feature.target))
        )
        if valid:
            known_features.add(feature.id)
        else:
            _omit(feature, "Insufficient reliable dimensions or placement for deterministic geometry")

    if not known_features:
        result.features.insert(0, _fallback_box())
    _omit_lint_failures(result)
    _omit_orphans(result)
    if not any(item.status == "confirmed" and item.operation == "add" for item in result.features):
        result.features.insert(0, _fallback_box())
    _cover_unmodeled_analysis(result)
    if not _compiles(result):
        for feature in result.features:
            if feature.id != "minimal_body":
                _omit(feature, "Omitted because it prevented a deterministic first preview")
        if not any(item.id == "minimal_body" for item in result.features):
            result.features.insert(0, _fallback_box())
        _cover_unmodeled_analysis(result)
    return result


def fallback_draft(draft: DraftSpecification) -> DraftSpecification:
    """Return the always-compilable last-resort body while retaining omissions."""
    result = draft.model_copy(deep=True)
    result.questions = []
    result.annotations = []
    result.assumptions = []
    result.dimensions = []
    for feature in result.features:
        _omit(feature, "Omitted because the requested geometry could not be rendered")
    result.features.insert(0, _fallback_box())
    _cover_unmodeled_analysis(result)
    return result


def _references(feature: SpecificationFeature) -> set[str]:
    values = [*feature.parameters.values(), *(feature.placement.origin or [])]
    return {item for item in values if isinstance(item, str)}


def _omit(feature: SpecificationFeature, reason: str) -> None:
    feature.status = "unsupported"
    feature.label = f"{feature.label} — omitted: {reason}"


def _omit_lint_failures(draft: DraftSpecification) -> None:
    """Remove the feature responsible for each deterministic geometry error."""
    features = {item.id: item for item in draft.features}
    while True:
        issues = [item for item in lint_draft(draft).issues if item.severity == "error"]
        if not issues:
            return
        omitted = False
        for issue in issues:
            # Lint reports the feature being checked first and its target second.
            feature = features.get(issue.feature_ids[0]) if issue.feature_ids else None
            if feature is None or feature.status == "unsupported":
                continue
            _omit(feature, f"Omitted because deterministic geometry validation failed: {issue.message}")
            omitted = True
        if not omitted:
            return


def _omit_orphans(draft: DraftSpecification) -> None:
    """A feature whose target was omitted cannot be part of the reliable model."""
    while True:
        known: set[str] = set()
        omitted = False
        for feature in draft.features:
            if feature.status != "confirmed":
                continue
            if feature.target and feature.target not in known:
                _omit(feature, "Omitted because its target is not part of the reliable model")
                omitted = True
                continue
            known.add(feature.id)
        if not omitted:
            return


def _fallback_box() -> SpecificationFeature:
    return SpecificationFeature(
        id="minimal_body", label="Minimal fallback body", type="box", operation="add",
        parameters={"length": 100, "width": 100, "height": 10},
        placement={"plane": "XY", "origin": [0, 0, 0]}, status="confirmed", source_feature_ids=[],
    )


def _cover_unmodeled_analysis(draft: DraftSpecification) -> None:
    covered = {source_id for feature in draft.features for source_id in feature.source_feature_ids}
    for index, item in enumerate(draft.analysis.features):
        source_id = str(item.get("id", ""))
        if source_id and source_id not in covered:
            draft.features.append(SpecificationFeature(
                id=f"omitted_feature_{index}", label=f"{source_id} — omitted: no deterministic geometry", type="freeform",
                operation="add", status="unsupported", source_feature_ids=[source_id],
            ))


def _compiles(draft: DraftSpecification) -> bool:
    try:
        project_from_specification(draft)
    except Exception:
        return False
    return True
