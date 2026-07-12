from __future__ import annotations

from statistics import median
from typing import Iterable


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
