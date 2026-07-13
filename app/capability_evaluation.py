from __future__ import annotations

from statistics import median
from typing import Any, Iterable


GRAPH_FIELDS = ("type", "operation", "target", "placement", "parameters", "status")


def compare_feature_graph(
    expected_operations: Iterable[dict[str, Any]], actual_operations: Iterable[dict[str, Any]]
) -> dict[str, object]:
    expected_by_id = {operation["id"]: operation for operation in expected_operations}
    actual_by_id = {operation["id"]: operation for operation in actual_operations}
    mismatches: list[dict[str, object]] = []

    for operation_id, expected in expected_by_id.items():
        actual = actual_by_id.get(operation_id)
        if actual is None:
            mismatches.append({"operation_id": operation_id, "field": "operation", "expected": "present", "actual": "missing"})
            continue
        for field in GRAPH_FIELDS:
            expected_value = expected.get(field, "implemented" if field == "status" else None)
            actual_value = actual.get(field, "implemented" if field == "status" else None)
            if actual_value != expected_value:
                mismatches.append(
                    {
                        "operation_id": operation_id,
                        "field": field,
                        "expected": expected_value,
                        "actual": actual_value,
                    }
                )

    for operation_id in sorted(actual_by_id.keys() - expected_by_id.keys()):
        mismatches.append({"operation_id": operation_id, "field": "operation", "expected": "absent", "actual": "present"})

    return {
        "expected_operation_ids": sorted(expected_by_id),
        "actual_operation_ids": sorted(actual_by_id),
        "matches": not mismatches,
        "mismatches": mismatches,
    }


def compare_named_dimensions(
    expected_parameters: dict[str, float | int], actual_parameters: dict[str, Any]
) -> dict[str, object]:
    errors_mm: dict[str, float] = {}
    missing_parameter_ids: list[str] = []
    for parameter_id, expected_value in expected_parameters.items():
        if isinstance(expected_value, bool) or not isinstance(expected_value, (int, float)):
            continue
        actual = actual_parameters.get(parameter_id)
        if isinstance(actual, dict):
            actual = actual.get("value")
        if isinstance(actual, bool) or not isinstance(actual, (int, float)):
            missing_parameter_ids.append(parameter_id)
            continue
        errors_mm[parameter_id] = abs(float(actual) - float(expected_value))
    return {"errors_mm": errors_mm, "missing_parameter_ids": sorted(missing_parameter_ids)}


def evaluate_capability(
    *,
    expected_feature_ids: Iterable[str],
    predicted_feature_ids: Iterable[str],
    high_confidence_feature_ids: Iterable[str] = (),
    export_results: Iterable[bool] = (),
    dimension_errors_mm: Iterable[float] = (),
    declared_dimensions_mm: Iterable[float] = (),
) -> dict[str, object]:
    expected = set(expected_feature_ids)
    predicted = set(predicted_feature_ids)
    true_positives = expected & predicted
    exports = list(export_results)
    errors = [abs(float(error)) for error in dimension_errors_mm]
    dimensions = [abs(float(value)) for value in declared_dimensions_mm]
    dimension_limit_mm = max(1.0, 0.02 * max(dimensions, default=0.0))
    missed_high_confidence = sorted(set(high_confidence_feature_ids) - predicted)

    precision = len(true_positives) / len(predicted) if predicted else (1.0 if not expected else 0.0)
    recall = len(true_positives) / len(expected) if expected else 1.0
    valid_export_rate = sum(exports) / len(exports) if exports else 0.0
    median_dimension_error_mm = median(errors) if errors else 0.0

    metrics = {
        "feature_precision": precision,
        "feature_recall": recall,
        "valid_export_rate": valid_export_rate,
        "median_dimension_error_mm": median_dimension_error_mm,
        "dimension_error_limit_mm": dimension_limit_mm,
        "missed_high_confidence_feature_ids": missed_high_confidence,
    }
    metrics["passes_supported_gate"] = (
        precision >= 0.9
        and recall >= 0.9
        and valid_export_rate >= 0.95
        and median_dimension_error_mm <= dimension_limit_mm
        and not missed_high_confidence
    )
    return metrics
