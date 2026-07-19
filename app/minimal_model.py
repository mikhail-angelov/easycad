"""Produce the smallest deterministic model that can be compiled from a drawing draft."""

from __future__ import annotations

from .feature_compiler import feature_contract_issues
from .draft_lint import lint_draft
from .models import DraftSpecification, SpecificationFeature
from .specification import project_from_specification, resolve_dimension_values


def minimal_reliable_draft(draft: DraftSpecification) -> DraftSpecification:
    """Keep only confirmed, self-contained geometry; disclose everything else as omitted."""
    result = draft.model_copy(deep=True)
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
        _insert_fallback_box(result)
    _omit_lint_failures(result)
    _omit_orphans(result)
    if not any(item.status == "confirmed" and item.operation == "add" for item in result.features):
        _insert_fallback_box(result)
    _cover_unmodeled_analysis(result)
    if not _compiles(result):
        result = fallback_draft(result)
    return result


def fallback_draft(draft: DraftSpecification) -> DraftSpecification:
    """The guaranteed-safe reduction: everything omitted except a fresh fallback box.

    Callers use this to recover when even a schema-valid `minimal_reliable_draft`
    result fails the real worker build (`run_project`) — a class of geometric
    failure (e.g. an infeasible fillet) that `_compiles`'s schema-level check
    cannot see. A user should never receive a build error; this is the backstop.

    Dimensions are dropped entirely, not just features: `_fallback_box` is pure
    literals and references none, but a dimension left over from an omitted
    feature (e.g. one that failed a positivity check) is still independently
    validated by `validate_specification` regardless of which features survive
    — an orphaned bad dimension would otherwise keep failing this exact "safe"
    path on every retry.
    """
    result = draft.model_copy(deep=True)
    for feature in result.features:
        _omit(feature, "Omitted because it prevented a deterministic first preview")
    _insert_fallback_box(result)
    result.dimensions = []
    _cover_unmodeled_analysis(result)
    return result


def _insert_fallback_box(draft: DraftSpecification) -> None:
    """Insert one fresh, confirmed fallback box — never a second `minimal_body` id.

    A prior round's fallback box (or an echo of it from a seeded replan) can already
    carry the `minimal_body` id while `unsupported` (omitted by an earlier, unrelated
    pass in this same function). Blindly checking "does a minimal_body id already
    exist" then skipping the insert leaves zero confirmed features — the one
    guarantee this module exists to uphold. Dropping any stale same-id feature before
    inserting a fresh one makes the insert unconditionally safe.
    """
    draft.features = [item for item in draft.features if item.id != "minimal_body"]
    draft.features.insert(0, _fallback_box())


def _references(feature: SpecificationFeature) -> set[str]:
    values = [*feature.parameters.values(), *(feature.placement.origin or [])]
    return {item for item in values if isinstance(item, str)}


def _omit(feature: SpecificationFeature, reason: str) -> None:
    feature.status = "unsupported"
    feature.omission_reason = reason


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
                id=f"omitted_feature_{index}", label=source_id, type="freeform",
                operation="add", status="unsupported", source_feature_ids=[source_id],
                omission_reason="no deterministic geometry",
            ))


def _compiles(draft: DraftSpecification) -> bool:
    try:
        project_from_specification(draft)
    except Exception:
        return False
    return True
