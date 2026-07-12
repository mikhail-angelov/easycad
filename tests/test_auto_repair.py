from __future__ import annotations

import asyncio
import unittest
from unittest.mock import patch

import app.main as main
from app.models import FeatureCoverageReport, FeatureGraph
from app.runner import RunnerError
from tests.test_ai_generation import make_plate_project


class AutoRepairTests(unittest.TestCase):
    def make_plate_project(self):
        return make_plate_project()

    def test_unresolved_high_confidence_feature_remains_needs_review_after_repair_limit(self):
        project = self.make_plate_project()
        project.feature_graph = FeatureGraph.model_validate(
            {
                "operations": [
                    project.feature_graph.operations[0].model_dump(),
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
        project.feature_coverage = FeatureCoverageReport.model_validate(
            {
                "entries": [
                    {
                        "feature_id": "rib_perforation",
                        "operation_ids": ["rib_perforation"],
                        "status": "unresolved",
                        "confidence": 0.95,
                        "explanation": "Hole count is unreadable.",
                    }
                ],
                "all_accounted_for": True,
                "has_unresolved": True,
            }
        )

        async def unchanged_repair(project, user_feedback="", current_view=None, validate_result=True):
            return project

        with patch.object(main, "repair_project", unchanged_repair), patch.object(
            main,
            "run_project",
            return_value={"artifact_bytes": b"stl", "duration_ms": 1, "bounding_box": {"x": 80, "y": 40, "z": 8}},
        ):
            result = asyncio.run(main.finalize_project_with_auto_repair(project, max_repairs=1))

        self.assertEqual(result["status"], "needs_review")
        self.assertEqual(result["project"]["generation"]["error"]["stage"], "feature_coverage")
        self.assertEqual(
            result["project"]["generation"]["error"]["detail"]["feature_ids"],
            ["rib_perforation"],
        )
        self.assertEqual(result["project"]["generation"]["syntax_status"], "success")
        self.assertEqual(result["project"]["generation"]["geometry_status"], "success")
        self.assertEqual(result["project"]["generation"]["semantic_status"], "failed")

    def test_export_rejects_unresolved_high_confidence_feature_before_any_format(self):
        project = self.make_plate_project()
        project.feature_coverage = FeatureCoverageReport.model_validate(
            {
                "entries": [
                    {
                        "feature_id": "missing_rib",
                        "status": "unsupported",
                        "confidence": 0.95,
                    }
                ],
                "all_accounted_for": True,
                "has_unresolved": True,
            }
        )

        for fmt in ("stl", "step", "json"):
            with self.subTest(fmt=fmt), self.assertRaises(main.HTTPException) as raised:
                main.export(main.PreviewRequest(project=project), format=fmt)
            self.assertEqual(raised.exception.status_code, 422)
            self.assertEqual(raised.exception.detail["stage"], "feature_coverage")

    def test_generation_failure_statuses_are_independent(self):
        project = self.make_plate_project()
        main.mark_generation_error(project, "static_validation", "bad source")
        self.assertEqual(project.generation.syntax_status, "failed")
        self.assertEqual(project.generation.geometry_status, "not_run")
        self.assertEqual(project.generation.semantic_status, "not_run")

        project = self.make_plate_project()
        project.generation.syntax_status = "success"
        main.mark_generation_error(project, "cadquery_execution", "boolean failed")
        self.assertEqual(project.generation.syntax_status, "success")
        self.assertEqual(project.generation.geometry_status, "failed")
        self.assertEqual(project.generation.semantic_status, "not_run")

        project = self.make_plate_project()
        project.generation.syntax_status = "success"
        project.generation.geometry_status = "success"
        main.mark_generation_error(project, "semantic_validation", "feature missing")
        self.assertEqual(project.generation.syntax_status, "success")
        self.assertEqual(project.generation.geometry_status, "success")
        self.assertEqual(project.generation.semantic_status, "failed")

    def test_geometry_validation_rejects_overall_bbox_mismatch(self):
        project = self.make_plate_project()
        project.parameters["overall_length"] = project.parameters.pop("plate_length")
        project.parameters["overall_width"] = project.parameters.pop("plate_width")
        project.parameters["overall_height"] = project.parameters.pop("plate_thickness")
        project.parameters["overall_height"].value = 60

        with self.assertRaisesRegex(RunnerError, "bounding box"):
            main.validate_generation_geometry(
                project,
                {"bounding_box": {"x": 80, "y": 40, "z": 55}},
            )

    def test_geometry_validation_rejects_excessive_l_bracket_volume(self):
        project = self.make_plate_project()
        project.parameters["overall_length"] = project.parameters.pop("plate_length")
        project.parameters["overall_width"] = project.parameters.pop("plate_width")
        project.parameters["overall_height"] = project.parameters.pop("plate_thickness")
        project.parameters["overall_length"].value = 90
        project.parameters["overall_width"].value = 50
        project.parameters["overall_height"].value = 60
        project.parameters["base_height"] = project.parameters["overall_height"].model_copy(update={"value": 25})
        project.parameters["upright_thickness"] = project.parameters["overall_height"].model_copy(update={"value": 30})

        with self.assertRaisesRegex(RunnerError, "volume is too large"):
            main.validate_generation_geometry(
                project,
                {"bounding_box": {"x": 90, "y": 50, "z": 60}, "volume_mm3": 217464},
            )

    def test_semantic_validation_rejects_wrong_hole_count_and_noop_pocket(self):
        project = self.make_plate_project()
        project.feature_graph = FeatureGraph.model_validate(
            {
                "operations": [
                    {
                        "id": "holes",
                        "type": "hole_pattern",
                        "operation": "pattern",
                        "target": "plate",
                        "pattern": {"type": "linear", "count": 5, "pitch": 10, "axis": "X"},
                    },
                    {
                        "id": "pocket",
                        "type": "pocket",
                        "operation": "cut",
                        "target": "plate",
                    },
                    {"id": "plate", "type": "box", "operation": "add"},
                ]
            }
        )

        with self.assertRaises(RunnerError) as raised:
            main.validate_feature_measurements(
                project,
                {
                    "feature_measurements": {
                        "holes": {"expected_instance_count": 5, "cylindrical_faces_delta": 4},
                        "pocket": {"volume_delta_mm3": 0},
                    }
                },
            )
        self.assertEqual(raised.exception.stage, "semantic_validation")
        self.assertEqual(raised.exception.detail["feature_ids"], ["holes", "pocket"])

    def test_semantic_validation_rejects_noop_add_and_cut(self):
        project = self.make_plate_project()
        project.feature_graph = FeatureGraph.model_validate(
            {
                "operations": [
                    {"id": "body", "type": "box", "operation": "add"},
                    {"id": "boss", "type": "box", "operation": "add", "target": "body"},
                    {"id": "cut", "type": "hole", "operation": "cut", "target": "body"},
                ]
            }
        )

        with self.assertRaises(RunnerError) as raised:
            main.validate_feature_measurements(
                project,
                {
                    "feature_measurements": {
                        "body": {"volume_delta_mm3": 100},
                        "boss": {"volume_delta_mm3": 0},
                        "cut": {"volume_delta_mm3": 0},
                    }
                },
            )
        self.assertEqual(raised.exception.detail["feature_ids"], ["boss", "cut"])

    def test_printable_validation_rejects_disconnected_and_thin_rib(self):
        project = self.make_plate_project()
        project.feature_graph = FeatureGraph.model_validate(
            {
                "operations": [
                    {
                        "id": "thin_rib",
                        "type": "rib",
                        "operation": "add",
                        "parameters": {"thickness": "plate_thickness"},
                        "minimum_printable_thickness": 10,
                    }
                ]
            }
        )

        with self.assertRaises(RunnerError) as raised:
            main.validate_feature_measurements(
                project,
                {
                    "solid_count": 2,
                    "feature_measurements": {
                        "thin_rib": {
                            "volume_delta_mm3": 100,
                            "solid_count": 2,
                        }
                    },
                },
            )
        mismatches = raised.exception.detail["mismatches"]
        self.assertTrue(any("expected one printable solid" in item for item in mismatches))
        self.assertTrue(any("thin_rib: additive feature is disconnected" in item for item in mismatches))
        self.assertTrue(any("below printable minimum" in item for item in mismatches))


if __name__ == "__main__":
    unittest.main()
