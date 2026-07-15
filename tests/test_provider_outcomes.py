from __future__ import annotations

import unittest

from app.provider_outcomes import TERMINAL_OUTCOMES, classify_provider_outcome


class ProviderOutcomeTests(unittest.TestCase):
    def test_classifies_every_terminal_outcome(self):
        cases = {
            "contract_ok": ({"status": "pending", "project": {}}, False, False),
            "needs_review": ({"status": "needs_review", "project": {"generation": {}}}, False, False),
            "verified_export": ({"status": "success", "project": {"generation": {"semantic_status": "success"}}}, True, True),
            "invalid_plan": ({"status": "needs_review", "project": {"generation": {"error": {"stage": "cad_generation"}}}}, False, False),
            "worker_failed": ({"status": "needs_review", "project": {"generation": {"error": {"stage": "worker"}}}}, False, False),
            "semantic_failed": ({"status": "needs_review", "project": {"generation": {"error": {"stage": "semantic_validation"}}}}, False, False),
        }
        self.assertEqual(set(cases), set(TERMINAL_OUTCOMES))
        for expected, (result, stl, step) in cases.items():
            with self.subTest(expected=expected):
                self.assertEqual(classify_provider_outcome(result, stl_exported=stl, step_exported=step), expected)

    def test_success_without_semantic_exports_is_not_verified_export(self):
        result = {"status": "success", "project": {"generation": {"semantic_status": "not_run"}}}
        self.assertEqual(classify_provider_outcome(result, stl_exported=True, step_exported=True), "contract_ok")
