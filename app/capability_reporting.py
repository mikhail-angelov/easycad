from __future__ import annotations

from typing import Any, Iterable

from .capability_evaluation import compare_feature_graph, compare_named_dimensions, evaluate_capability

CAPABILITY_OUTCOMES = (
    "contract_ok",
    "needs_review",
    "verified_export",
    "invalid_plan",
    "worker_failed",
    "semantic_failed",
)


def summarize_capability_observations(observations: Iterable[dict[str, Any]]) -> dict[str, int]:
    counts = {outcome: 0 for outcome in CAPABILITY_OUTCOMES}
    for observation in observations:
        outcome = observation.get("outcome")
        if outcome in counts:
            counts[outcome] += 1
    return counts


def capability_report_entry(
    case: dict[str, Any], observations: Iterable[dict[str, Any]], *, worker_suite_passed: bool
) -> dict[str, Any]:
    observations = list(observations)
    outcomes = summarize_capability_observations(observations)
    required_observations = len(case.get("variants", [case["source_drawing"]]))
    gate = case.get("evidence_gate") or {}
    minimum_observations = int(gate.get("minimum_observations", required_observations))
    minimum_verified_export_rate = float(gate.get("minimum_verified_export_rate", 1.0))
    observed_count = sum(outcomes.values())
    unavailable_reasons = sorted(
        {str(observation["unavailable_reason"]) for observation in observations if observation.get("unavailable_reason")}
    )
    quality_observations = [
        observation
        for observation in observations
        if isinstance(observation.get("feature_graph"), dict)
        and isinstance(observation.get("parameters"), dict)
        and isinstance(observation.get("exports"), dict)
    ]

    # Older fixture files have no explicit provider outcome and cannot be
    # promoted into quality evidence merely because they contain model prose.
    provider_status = (
        "measured"
        if observed_count >= minimum_observations and len(quality_observations) >= minimum_observations
        else "insufficient_evidence"
    )
    if unavailable_reasons and not observed_count:
        provider_status = "unavailable"
    evaluation_status = "insufficient_evidence"
    metrics = None
    if provider_status == "measured":
        graph_comparisons = [
            compare_feature_graph(case["operations"], observation["feature_graph"].get("operations", []))
            for observation in quality_observations
        ]
        dimensions = [
            compare_named_dimensions(case["parameters"], observation["parameters"])
            for observation in quality_observations
        ]
        expected_ids = [operation["id"] for operation in case["operations"]]
        actual_ids = [
            operation_id
            for comparison in graph_comparisons
            for operation_id in comparison["actual_operation_ids"]
        ]
        dimension_errors = [
            error for comparison in dimensions for error in comparison["errors_mm"].values()
        ]
        declared_dimensions = [
            value for value in case["parameters"].values() if isinstance(value, (int, float)) and not isinstance(value, bool)
        ]
        export_results = [
            bool(observation["exports"].get(fmt))
            for observation in quality_observations
            for fmt in ("stl", "step")
        ]
        metrics = evaluate_capability(
            expected_feature_ids=expected_ids,
            predicted_feature_ids=actual_ids,
            high_confidence_feature_ids=expected_ids,
            export_results=export_results,
            dimension_errors_mm=dimension_errors,
            declared_dimensions_mm=declared_dimensions,
        )
        metrics["graph_mismatch_count"] = sum(len(comparison["mismatches"]) for comparison in graph_comparisons)
        metrics["missing_parameter_ids"] = sorted(
            {parameter_id for comparison in dimensions for parameter_id in comparison["missing_parameter_ids"]}
        )
        metrics["passes_supported_gate"] = bool(
            metrics["passes_supported_gate"]
            and not metrics["graph_mismatch_count"]
            and not metrics["missing_parameter_ids"]
            and outcomes["verified_export"] / observed_count >= minimum_verified_export_rate
        )
        evaluation_status = "passed" if metrics["passes_supported_gate"] else "failed"

    return {
        "status": case["status"],
        "fixture_count": required_observations,
        "evidence_gate": {
            "minimum_observations": minimum_observations,
            "minimum_verified_export_rate": minimum_verified_export_rate,
        },
        "outcomes": outcomes,
        "worker_evaluation": {
            "status": "passed" if worker_suite_passed else "failed",
            "stl_step_export_rate": None,
            "note": "Per-case STL/STEP observations are required before export quality is measured.",
        },
        "provider_evaluation": {
            "status": provider_status,
            "observation_count": observed_count,
            "unavailable_reasons": unavailable_reasons,
            "metrics": metrics,
        },
        "evaluation_status": evaluation_status,
        "observation_count": observed_count,
        "metrics": metrics,
    }


def report_status(*, worker_suite_passed: bool, capabilities: dict[str, dict[str, Any]]) -> str:
    if not worker_suite_passed:
        return "failed"
    supported = [entry for entry in capabilities.values() if entry["status"] == "supported"]
    if supported and all(entry["evaluation_status"] == "passed" for entry in supported):
        return "passed"
    return "insufficient_evidence"
