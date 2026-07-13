from __future__ import annotations

import unittest

from app.capability_reporting import capability_report_entry, report_status, summarize_capability_observations


CASE = {"id": "slots", "status": "supported", "source_drawing": "slots.png", "variants": ["a", "b"]}


def quality_observation(*, target="base", parameter_id="slot_length", export=True):
    return {
        "outcome": "verified_export",
        "feature_graph": {"operations": [{
            "id": "slot", "type": "slot", "operation": "cut", "target": target,
            "placement": {"origin": [10, 10, 0]},
            "parameters": {"length": parameter_id, "width": "slot_width", "depth": "depth"},
            "status": "implemented",
        }]},
        "parameters": {parameter_id: {"value": 18}, "slot_width": {"value": 5}, "depth": {"value": 6}},
        "exports": {"stl": export, "step": export},
    }


QUALITY_CASE = {
    "id": "slots", "status": "supported", "source_drawing": "slots.png", "variants": ["a", "b"],
    "parameters": {"slot_length": 18, "slot_width": 5, "depth": 6},
    "operations": [{
        "id": "slot", "type": "slot", "operation": "cut", "target": "base",
        "placement": {"origin": [10, 10, 0]},
        "parameters": {"length": "slot_length", "width": "slot_width", "depth": "depth"},
        "status": "implemented",
    }],
}


class CapabilityReportingTests(unittest.TestCase):
    def test_missing_outcomes_are_insufficient_evidence(self):
        entry = capability_report_entry(CASE, [{"variant": "a", "detected": True}], worker_suite_passed=True)

        self.assertEqual(entry["evaluation_status"], "insufficient_evidence")
        self.assertEqual(entry["provider_evaluation"]["status"], "insufficient_evidence")
        self.assertIsNone(entry["worker_evaluation"]["stl_step_export_rate"])
        self.assertEqual(report_status(worker_suite_passed=True, capabilities={"slots": entry}), "insufficient_evidence")

    def test_outcome_counters_are_distinct(self):
        outcomes = summarize_capability_observations(
            [{"outcome": outcome} for outcome in (
                "contract_ok", "needs_review", "verified_export", "invalid_plan", "worker_failed", "semantic_failed"
            )]
        )

        self.assertEqual(outcomes, {
            "contract_ok": 1,
            "needs_review": 1,
            "verified_export": 1,
            "invalid_plan": 1,
            "worker_failed": 1,
            "semantic_failed": 1,
        })

    def test_only_verified_exports_can_pass_complete_observation_set(self):
        entry = capability_report_entry(QUALITY_CASE, [quality_observation(), quality_observation()], worker_suite_passed=True)

        self.assertEqual(entry["evaluation_status"], "passed")
        self.assertEqual(report_status(worker_suite_passed=True, capabilities={"slots": entry}), "passed")

    def test_wrong_graph_target_named_parameter_or_export_fails_quality_gate(self):
        entry = capability_report_entry(
            QUALITY_CASE,
            [quality_observation(target="wrong"), quality_observation(parameter_id="wrong_length", export=False)],
            worker_suite_passed=True,
        )

        self.assertEqual(entry["evaluation_status"], "failed")
        self.assertGreater(entry["metrics"]["graph_mismatch_count"], 0)
        self.assertEqual(entry["metrics"]["missing_parameter_ids"], ["slot_length"])
        self.assertLess(entry["metrics"]["valid_export_rate"], 1.0)

    def test_wrong_type_or_omitted_operation_fails_quality_gate(self):
        wrong_type = quality_observation()
        wrong_type["feature_graph"]["operations"][0]["type"] = "pocket"
        omitted = quality_observation()
        omitted["feature_graph"]["operations"] = []

        entry = capability_report_entry(QUALITY_CASE, [wrong_type, omitted], worker_suite_passed=True)

        self.assertEqual(entry["evaluation_status"], "failed")
        self.assertGreaterEqual(entry["metrics"]["graph_mismatch_count"], 2)
