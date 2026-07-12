from __future__ import annotations

import asyncio
import json
import os
import unittest
from pathlib import Path

import app.ai_generation as ai
from app.ai_generation import project_from_plan
from app.main import finalize_project_with_auto_repair, load_env
from app.runner import run_project
from app.validator import validate_project


ROOT = Path(__file__).resolve().parent.parent
RECORDED_DIR = ROOT / "tests" / "fixtures" / "llm"


class RealFixtureGenerationE2E(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        load_env()
        missing = [key for key in ("OPEN_ROUTER_KEY", "DEEP_SEEK_KEY") if not os.environ.get(key)]
        if missing:
            raise unittest.SkipTest(f"Missing real API env keys: {', '.join(missing)}")

    def test_fixture_images_generate_valid_projects(self):
        for fixture in sorted((ROOT / "fixtures").glob("*.jpg")):
            with self.subTest(fixture=fixture.name):
                result = asyncio.run(self._generate_fixture(fixture))
                project = result["project"]
                source = project["cad"]["source"]
                self.assertEqual(result["status"], "success", project["generation"].get("error"))
                self.assertIsNone(project["generation"].get("error"))
                self.assertNotIn("import ", source)
                self.assertFalse(any(line.strip().startswith("from ") for line in source.splitlines()))
                self.assertGreaterEqual(len(project["parameters"]), 3)
                self.assertGreater(project["generation"]["bounding_box"]["x"], 0)
                if fixture.name == "1.jpg":
                    self.assert_bbox_close(project["generation"]["bounding_box"], {"x": 90, "y": 50, "z": 60})

    async def _generate_fixture(self, fixture: Path):
        RECORDED_DIR.mkdir(parents=True, exist_ok=True)
        base = fixture.stem
        data = fixture.read_bytes()
        analysis = await ai.analyze_drawing(data, "image/jpeg", "", os.environ["OPEN_ROUTER_KEY"])
        self._write_json(RECORDED_DIR / f"{base}.analysis.json", analysis)
        plan = await ai.plan_cad_project(analysis, "", os.environ["DEEP_SEEK_KEY"])
        self._write_json(RECORDED_DIR / f"{base}.plan.json", plan)
        project = project_from_plan(plan, analysis, {"filename": fixture.name, "mime_type": "image/jpeg"}, data)

        repairs = []
        original_repair_project = ai.repair_project
        original_main_repair_project = __import__("app.main").main.repair_project

        async def recording_repair_project(project, user_feedback="", current_view=None, validate_result=True):
            repair_plan = await ai.plan_repair(project, user_feedback, current_view, os.environ["DEEP_SEEK_KEY"])
            repairs.append(
                {
                    "attempt": project.cad.generation_attempt + 1,
                    "input_error": project.generation.error,
                    "user_feedback": user_feedback,
                    "plan": repair_plan,
                }
            )
            repaired = ai.apply_repair_plan(project, repair_plan)
            if validate_result:
                validate_project(repaired)
            return repaired

        try:
            ai.repair_project = recording_repair_project
            __import__("app.main").main.repair_project = recording_repair_project
            result = await finalize_project_with_auto_repair(project, "")
        finally:
            ai.repair_project = original_repair_project
            __import__("app.main").main.repair_project = original_main_repair_project

        self._write_json(RECORDED_DIR / f"{base}.repairs.json", repairs)
        self._write_json(RECORDED_DIR / f"{base}.final_project.json", result["project"])
        if result["status"] != "success":
            return result
        project_model = type(project).model_validate(result["project"])
        validate_project(project_model)
        run_project(project_model, {}, fmt="stl")
        run_project(project_model, {}, fmt="step")
        return result

    def _write_json(self, path: Path, payload) -> None:
        path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")

    def assert_bbox_close(self, actual, expected, tolerance=1.0):
        for axis, expected_value in expected.items():
            self.assertLessEqual(abs(float(actual[axis]) - expected_value), tolerance, f"{axis}: {actual}")


if __name__ == "__main__":
    unittest.main()
