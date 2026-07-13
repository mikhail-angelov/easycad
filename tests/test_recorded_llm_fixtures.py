from __future__ import annotations

import json
import unittest
from pathlib import Path

from app.ai_generation import GenerationError, apply_repair_plan, project_from_plan
from app.main import validate_feature_coverage
from app.runner import RunnerError


ROOT = Path(__file__).resolve().parent.parent
RECORDED_DIR = ROOT / "tests" / "fixtures" / "llm"


class RecordedLegacyLLMFixtureTests(unittest.TestCase):
    def test_legacy_code_only_plans_are_not_executable_projects(self):
        plan = {
            "parameters": [{"id": "length", "label": "Length", "value": 10}],
            "code": "result = cq.Workplane('XY').box(10, 10, 10)",
        }
        project = project_from_plan(plan, {}, {}, b"")
        self.assertEqual(project.generation.status, "needs_review")
        self.assertFalse(project.feature_graph.operations)
        self.assertEqual(project.cad.source, "")

    def test_legacy_recordings_contain_no_secret_fields(self):
        for path in sorted(RECORDED_DIR.glob("*.json")):
            lowered = path.read_text(encoding="utf-8").lower()
            self.assertNotIn("authorization", lowered)
            self.assertNotIn("api_key", lowered)
            self.assertNotIn("bearer ", lowered)

    def test_current_structured_plans_normalize_without_executable_fallback(self):
        for base in ("1", "2", "4"):
            with self.subTest(base=base):
                plan = json.loads((RECORDED_DIR / f"{base}.plan.json").read_text(encoding="utf-8"))
                analysis = json.loads((RECORDED_DIR / f"{base}.analysis.json").read_text(encoding="utf-8"))
                self.assertNotIn("code", plan)
                self.assertNotIn("source", plan)

                try:
                    project = project_from_plan(plan, analysis, {}, b"")
                except GenerationError as exc:
                    self.assertEqual(exc.stage, "cad_generation")
                    continue

                self.assertTrue(project.feature_graph.operations)
                self.assertTrue(
                    all(operation.status in {"implemented", "approximated", "unresolved", "unsupported"} for operation in project.feature_graph.operations)
                )
                self.assertTrue(project.cad.source_kind == "compiled" or not project.cad.source)

    def test_recorded_operation_only_repair_replays_deterministically(self):
        plan = json.loads((RECORDED_DIR / "1.plan.json").read_text(encoding="utf-8"))
        analysis = json.loads((RECORDED_DIR / "1.analysis.json").read_text(encoding="utf-8"))
        repair_records = json.loads((RECORDED_DIR / "1.repairs.json").read_text(encoding="utf-8"))
        repair = repair_records[-1]["plan"]
        self.assertNotIn("code", repair)
        self.assertNotIn("source", repair)

        project = project_from_plan(plan, analysis, {}, b"")
        declared = set(repair.get("repaired_feature_ids", []))
        updated = {item["id"] for item in repair.get("operation_updates", [])}
        if updated <= declared:
            repaired = apply_repair_plan(project, repair)
            self.assertEqual(repaired.cad.source_kind, "compiled")
            with self.assertRaises(RunnerError):
                validate_feature_coverage(repaired)
        else:
            with self.assertRaisesRegex(GenerationError, "not declared in repaired_feature_ids"):
                apply_repair_plan(project, repair)


if __name__ == "__main__":
    unittest.main()
