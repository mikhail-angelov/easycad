from __future__ import annotations

import json
import unittest
from pathlib import Path

import app.main as main
from app.models import CADProject
from app.runner import RunnerError


ROOT = Path(__file__).resolve().parent.parent


class SemanticFixtureTests(unittest.TestCase):
    def test_missing_perforation_fails_with_correct_bounding_box(self):
        fixture = json.loads(
            (ROOT / "tests" / "fixtures" / "semantic" / "missing_perforation.json").read_text(encoding="utf-8")
        )
        project = CADProject.model_validate(fixture["project"])
        result = fixture["worker_result"]

        main.validate_generation_geometry(project, result)
        with self.assertRaises(RunnerError) as raised:
            main.validate_feature_measurements(project, result)

        self.assertEqual(raised.exception.stage, "semantic_validation")
        self.assertEqual(set(raised.exception.detail["feature_ids"]), {"perforation"})


if __name__ == "__main__":
    unittest.main()
