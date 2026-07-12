from __future__ import annotations

import json
import unittest
from pathlib import Path

from app.ai_generation import GenerationError, project_from_plan


ROOT = Path(__file__).resolve().parent.parent
RECORDED_DIR = ROOT / "tests" / "fixtures" / "llm"


class RecordedLegacyLLMFixtureTests(unittest.TestCase):
    def test_legacy_code_only_plans_are_not_executable_projects(self):
        for plan_path in sorted(RECORDED_DIR.glob("*.plan.json")):
            with self.subTest(path=plan_path.name):
                plan = json.loads(plan_path.read_text(encoding="utf-8"))
                analysis_path = RECORDED_DIR / plan_path.name.replace(".plan.json", ".analysis.json")
                analysis = json.loads(analysis_path.read_text(encoding="utf-8"))

                with self.assertRaises(GenerationError):
                    project_from_plan(plan, analysis, {}, b"")

    def test_legacy_recordings_contain_no_secret_fields(self):
        for path in sorted(RECORDED_DIR.glob("*.json")):
            lowered = path.read_text(encoding="utf-8").lower()
            self.assertNotIn("authorization", lowered)
            self.assertNotIn("api_key", lowered)
            self.assertNotIn("bearer ", lowered)


if __name__ == "__main__":
    unittest.main()
