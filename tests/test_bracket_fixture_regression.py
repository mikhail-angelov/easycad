from __future__ import annotations

import unittest
from pathlib import Path

from app.main import load_project_json
from app.runner import run_project


ROOT = Path(__file__).resolve().parent.parent
BRACKET_PATH = ROOT / "projects" / "bracket_fixture.json"
EXPECTED_OPERATION_IDS = ["base", "upright", "top_groove", "main_hole", "front_notch"]


class BracketFixtureRegressionTests(unittest.TestCase):
    def load_fixture(self):
        return load_project_json(BRACKET_PATH.read_text(encoding="utf-8"))

    def assert_graph_contract(self, project):
        operations = {operation.id: operation for operation in project.feature_graph.operations}
        self.assertEqual([operation.id for operation in project.feature_graph.operations], EXPECTED_OPERATION_IDS)
        self.assertEqual(operations["base"].type, "box")
        self.assertIsNone(operations["base"].target)
        self.assertEqual(operations["upright"].type, "box")
        self.assertEqual(operations["upright"].target, "base")
        self.assertEqual(operations["upright"].placement.origin, ["upright_x", 0, "base_thickness"])
        self.assertEqual(operations["top_groove"].type, "pocket")
        self.assertEqual(operations["top_groove"].target, "upright")
        self.assertEqual(operations["top_groove"].parameters["depth"], "groove_depth")
        self.assertEqual(operations["main_hole"].type, "through_hole")
        self.assertEqual(operations["main_hole"].target, "top_groove")
        self.assertEqual(operations["main_hole"].parameters["diameter"], "main_hole_diameter")
        self.assertEqual(operations["front_notch"].type, "through_hole")
        self.assertEqual(operations["front_notch"].target, "main_hole")
        self.assertEqual(operations["front_notch"].parameters["diameter"], "front_notch_diameter")

    def test_complete_bracket_graph_is_compiled_and_measured(self):
        project = self.load_fixture()
        self.assertEqual(project.cad.source, "")
        self.assert_graph_contract(project)

        result = run_project(project, {}, fmt="stl")
        measurements = result["feature_measurements"]

        self.assertEqual(result["bounding_box"], {"x": 90.0, "y": 50.0, "z": 60.0})
        self.assertGreater(measurements["upright"]["volume_delta_mm3"], 59_000)
        self.assertAlmostEqual(abs(measurements["top_groove"]["volume_delta_mm3"]), 9_000, delta=1)
        self.assertLess(measurements["main_hole"]["volume_delta_mm3"], 0)
        self.assertAlmostEqual(measurements["main_hole"]["measured_cylinder_diameter"], 30, delta=0.2)
        self.assertLess(measurements["front_notch"]["volume_delta_mm3"], 0)

    def test_removing_each_protected_feature_fails_graph_contract_with_its_id(self):
        for operation_id in EXPECTED_OPERATION_IDS:
            with self.subTest(operation_id=operation_id):
                project = self.load_fixture()
                project.feature_graph.operations = [
                    operation for operation in project.feature_graph.operations if operation.id != operation_id
                ]
                with self.assertRaisesRegex(AssertionError, operation_id):
                    self.assert_graph_contract(project)
