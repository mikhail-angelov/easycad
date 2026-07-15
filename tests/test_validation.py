from __future__ import annotations

import unittest
from pathlib import Path

from app.main import export, load_project_json, PreviewRequest
from app.feature_compiler import CompilerError, compile_project_feature_graph
from app.models import FeatureGraph
from app.validator import ValidationError, parameter_references, validate_project, validate_source
from tests.project_helpers import make_plate_project


ROOT = Path(__file__).resolve().parent.parent


class ValidationTests(unittest.TestCase):
    def load_fixture(self, name: str):
        return load_project_json((ROOT / "projects" / name).read_text(encoding="utf-8"))

    def test_fixture_projects_validate(self):
        for name in ("bolt_fixture.json", "bracket_fixture.json"):
            with self.subTest(name=name):
                validate_project(self.load_fixture(name))

    def test_legacy_source_only_project_loads_needs_review_and_cannot_export(self):
        payload = (ROOT / "projects" / "bracket_fixture.json").read_text(encoding="utf-8")
        raw = __import__("json").loads(payload)
        raw.pop("feature_graph")
        raw["cad"].pop("source_kind", None)

        project = load_project_json(__import__("json").dumps(raw))

        self.assertEqual(project.generation.status, "needs_review")
        self.assertEqual(project.generation.error["stage"], "legacy_project")
        with self.assertRaisesRegex(Exception, "Legacy source-only"):
            export(PreviewRequest(project=project), format="stl")

    def test_forbidden_source_is_rejected(self):
        with self.assertRaisesRegex(ValidationError, "Import is not allowed"):
            validate_source("import os\nresult = cq.Workplane('XY').box(1, 1, 1)\n")

    def test_real_generated_cadquery_methods_are_allowed(self):
        validate_source(
            "p = PARAMETERS\n"
            "body = cq.Workplane('XY').box(10, 10, 10).faces('>Z').workplane().circle(2).cutThruAll()\n"
            "result = body.val()\n"
        )

    def test_safe_math_angle_helpers_are_allowed(self):
        validate_source(
            "p = PARAMETERS\n"
            "result = cq.Workplane('XY').box(10, 10, 10).rotate((0, 0, 0), (0, 0, 1), math.degrees(math.radians(45)))\n"
        )

    def test_integer_cast_is_allowed_for_pattern_counts(self):
        validate_source(
            "p = PARAMETERS\n"
            "result = cq.Workplane('XY').rarray(5, 1, int(p['count']), 1).circle(1).extrude(2)\n"
        )

    def test_unknown_parameter_reference_is_rejected(self):
        project = make_plate_project()
        project.feature_graph.operations[0].parameters["length"] = "missing_parameter"
        with self.assertRaisesRegex(CompilerError, "missing_parameter"):
            compile_project_feature_graph(project)

    def test_invalid_parameter_bounds_are_rejected(self):
        project = self.load_fixture("bracket_fixture.json")
        project.parameters["overall_length"].min = 200
        project.parameters["overall_length"].max = 100
        with self.assertRaisesRegex(ValidationError, "minimum is greater than maximum"):
            validate_project(project)

    def test_unresolved_expression_is_rejected(self):
        project = self.load_fixture("bracket_fixture.json")
        project.parameters["upright_height"].expression = "unknown_length - base_thickness"
        with self.assertRaisesRegex(ValidationError, "Could not resolve derived parameters"):
            validate_project(project)

    def test_parameter_reference_extraction(self):
        refs = parameter_references('p = PARAMETERS\nresult = cq.Workplane("XY").box(p["x"], PARAMETERS["y"], 1)\n')
        self.assertEqual(refs, {"x", "y"})

    def test_high_confidence_feature_without_graph_coverage_is_rejected(self):
        project = self.load_fixture("bracket_fixture.json")
        project.analysis.features = [
            {"id": "rib_perforation", "type": "hole_pattern", "confidence": 0.95}
        ]

        with self.assertRaisesRegex(ValidationError, "rib_perforation.*no Feature Graph coverage"):
            validate_project(project)

    def test_high_confidence_feature_requires_final_coverage_state(self):
        project = self.load_fixture("bracket_fixture.json")
        project.analysis.features = [
            {"id": "rib_perforation", "type": "hole_pattern", "confidence": 0.95}
        ]
        project.feature_graph = FeatureGraph.model_validate(
            {
                "operations": [
                    {
                        "id": "rib_perforation",
                        "type": "hole_pattern",
                        "operation": "add",
                        "source_feature_ids": ["rib_perforation"],
                        "status": "planned",
                    }
                ]
            }
        )

        with self.assertRaisesRegex(ValidationError, "rib_perforation.*no final coverage state"):
            validate_project(project)

    def test_explicit_unresolved_feature_is_not_silently_omitted(self):
        project = self.load_fixture("bracket_fixture.json")
        project.analysis.features = [
            {"id": "rib_perforation", "type": "hole_pattern", "confidence": 0.95}
        ]
        project.feature_graph = FeatureGraph.model_validate(
            {
                "operations": [
                    {
                        "id": "rib_perforation",
                        "type": "hole_pattern",
                        "operation": "pattern",
                        "source_feature_ids": ["rib_perforation"],
                        "status": "unresolved",
                        "assumption": "Hole count is unreadable.",
                    }
                ]
            }
        )

        validate_project(project)


if __name__ == "__main__":
    unittest.main()
