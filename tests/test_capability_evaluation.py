from __future__ import annotations

import unittest

from app.capability_evaluation import compare_feature_graph, compare_named_dimensions, evaluate_capability


class CapabilityEvaluationTests(unittest.TestCase):
    def test_named_dimension_comparison_uses_matching_parameter_ids_only(self):
        comparison = compare_named_dimensions(
            {"length": 70, "slot_length": 18},
            {"length": {"value": 70}, "wrong_parameter": {"value": 18}},
        )

        self.assertEqual(comparison["errors_mm"], {"length": 0.0})
        self.assertEqual(comparison["missing_parameter_ids"], ["slot_length"])

    def test_graph_comparison_matches_type_target_placement_parameters_and_coverage(self):
        expected = [
            {
                "id": "base",
                "type": "box",
                "operation": "add",
                "target": None,
                "placement": {"origin": [0, 0, 0]},
                "parameters": {"length": "length", "width": "width", "height": "height"},
                "status": "implemented",
            },
            {
                "id": "hole",
                "type": "hole",
                "operation": "cut",
                "target": "base",
                "placement": {"origin": [10, 10, 0]},
                "parameters": {"diameter": "diameter", "depth": "depth"},
                "status": "implemented",
            },
        ]

        comparison = compare_feature_graph(expected, [dict(operation) for operation in expected])

        self.assertTrue(comparison["matches"])
        self.assertEqual(comparison["mismatches"], [])

    def test_graph_comparison_reports_wrong_type_target_placement_parameter_and_coverage(self):
        expected = [{
            "id": "hole", "type": "hole", "operation": "cut", "target": "base",
            "placement": {"origin": [10, 10, 0]}, "parameters": {"diameter": "diameter", "depth": "depth"},
            "status": "implemented",
        }]
        actual = [{
            "id": "hole", "type": "slot", "operation": "cut", "target": "wrong_base",
            "placement": {"origin": [11, 10, 0]}, "parameters": {"diameter": "wrong_diameter", "depth": "depth"},
            "status": "approximated",
        }]

        comparison = compare_feature_graph(expected, actual)

        self.assertFalse(comparison["matches"])
        self.assertEqual(
            {item["field"] for item in comparison["mismatches"]},
            {"type", "target", "placement", "parameters", "status"},
        )

    def test_graph_comparison_reports_omitted_operation(self):
        comparison = compare_feature_graph(
            [{"id": "base", "type": "box", "operation": "add"}],
            [],
        )

        self.assertEqual(comparison["mismatches"][0]["operation_id"], "base")
        self.assertEqual(comparison["mismatches"][0]["actual"], "missing")

    def test_tracks_quality_dimensions_separately_and_passes_gate(self):
        metrics = evaluate_capability(
            expected_feature_ids=["base", "rib", "holes"],
            predicted_feature_ids=["base", "rib", "holes"],
            high_confidence_feature_ids=["rib", "holes"],
            export_results=[True, True],
            dimension_errors_mm=[0.2, 0.4, 0.1],
            declared_dimensions_mm=[40, 30, 6],
        )

        self.assertEqual(metrics["feature_precision"], 1.0)
        self.assertEqual(metrics["feature_recall"], 1.0)
        self.assertEqual(metrics["valid_export_rate"], 1.0)
        self.assertEqual(metrics["median_dimension_error_mm"], 0.2)
        self.assertTrue(metrics["passes_supported_gate"])

    def test_failed_export_does_not_change_precision_or_recall(self):
        metrics = evaluate_capability(
            expected_feature_ids=["base", "hole"],
            predicted_feature_ids=["base", "extra"],
            high_confidence_feature_ids=["hole"],
            export_results=[True, False],
            dimension_errors_mm=[2.0],
            declared_dimensions_mm=[20],
        )

        self.assertEqual(metrics["feature_precision"], 0.5)
        self.assertEqual(metrics["feature_recall"], 0.5)
        self.assertEqual(metrics["valid_export_rate"], 0.5)
        self.assertEqual(metrics["missed_high_confidence_feature_ids"], ["hole"])
        self.assertFalse(metrics["passes_supported_gate"])

    def test_dimension_gate_uses_larger_of_one_mm_or_two_percent(self):
        small = evaluate_capability(
            expected_feature_ids=[], predicted_feature_ids=[], export_results=[True],
            dimension_errors_mm=[0.9], declared_dimensions_mm=[20],
        )
        large = evaluate_capability(
            expected_feature_ids=[], predicted_feature_ids=[], export_results=[True],
            dimension_errors_mm=[1.5], declared_dimensions_mm=[100],
        )

        self.assertEqual(small["dimension_error_limit_mm"], 1.0)
        self.assertEqual(large["dimension_error_limit_mm"], 2.0)
        self.assertTrue(small["passes_supported_gate"])
        self.assertTrue(large["passes_supported_gate"])

    def test_manifest_does_not_claim_metrics_without_observed_outcomes(self):
        import json
        from pathlib import Path

        cases = json.loads(
            (Path(__file__).resolve().parent / "fixtures" / "capabilities" / "cases.json").read_text()
        )
        for case in cases:
            self.assertNotIn("metrics", case)
            self.assertNotIn("export_results", case)

if __name__ == "__main__":
    unittest.main()
