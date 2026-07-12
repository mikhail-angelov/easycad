from __future__ import annotations

import asyncio
import json
import unittest
from unittest.mock import AsyncMock, patch

import app.ai_generation as ai
import app.main as main
from app.ai_generation import GenerationError, apply_repair_plan, plan_cad_project, plan_repair, project_from_plan
from app.models import CompareRequest, RenderArtifact, VisualComparison, VisualIssue


def plate_plan() -> dict:
    return {
        "title": "Plate",
        "parameters": [
            {"id": "plate_length", "label": "Length", "type": "number", "value": 40},
            {"id": "plate_width", "label": "Width", "type": "number", "value": 30},
            {"id": "plate_thickness", "label": "Height", "type": "number", "value": 5},
        ],
        "feature_graph": {
            "operations": [
                {
                    "id": "base",
                    "type": "box",
                    "operation": "add",
                    "source_feature_ids": ["base"],
                    "parameters": {"length": "plate_length", "width": "plate_width", "height": "plate_thickness"},
                    "confidence": 1.0,
                    "status": "implemented",
                    "implementation": "base",
                }
            ]
        },
        "feature_summary": [{"id": "base", "name": "Base", "type": "body", "description": "Plate"}],
        "assumptions": [],
    }


def plate_analysis() -> dict:
    return {
        "title": "Plate",
        "features": [{"id": "base", "type": "body", "confidence": 1.0}],
        "dimensions": [],
        "views": [],
        "uncertainties": [],
    }


def make_plate_project():
    return project_from_plan(
        plate_plan(),
        plate_analysis(),
        {"filename": "plate.png", "mime_type": "image/png", "width": 100, "height": 100},
        b"drawing",
    )


class AIGenerationTests(unittest.TestCase):
    def test_project_from_plan_ignores_model_code_and_uses_trusted_compiler(self):
        plan = plate_plan()
        plan["code"] = "import os\nresult = os.system('bad')"

        project = project_from_plan(plan, plate_analysis(), {}, b"")

        self.assertEqual(project.cad.source_kind, "compiled")
        self.assertIn("# feature:base", project.cad.source)
        self.assertNotIn("import os", project.cad.source)
        self.assertEqual(project.cad.implemented_feature_ids, ["base"])

    def test_project_from_plan_rejects_implemented_unsupported_operation(self):
        plan = plate_plan()
        plan["feature_graph"]["operations"].append(
            {
                "id": "freeform",
                "type": "freeform_sweep",
                "operation": "add",
                "target": "base",
                "status": "implemented",
                "implementation": "freeform",
            }
        )

        with self.assertRaisesRegex(GenerationError, "freeform.*unsupported primitive"):
            project_from_plan(plan, plate_analysis(), {}, b"")

    def test_project_from_plan_preserves_explicit_unsupported_feature_for_review(self):
        plan = plate_plan()
        plan["feature_graph"]["operations"].append(
            {
                "id": "freeform",
                "type": "freeform_sweep",
                "operation": "add",
                "target": "base",
                "status": "unsupported",
                "assumption": "No trusted compiler operation.",
            }
        )
        analysis = plate_analysis()
        analysis["features"].append({"id": "freeform", "type": "sweep", "confidence": 0.95})

        project = project_from_plan(plan, analysis, {}, b"")

        coverage = {entry.feature_id: entry.status for entry in project.feature_coverage.entries}
        self.assertEqual(coverage["freeform"], "unsupported")
        self.assertTrue(project.feature_coverage.has_unresolved)

    def test_apply_repair_plan_requires_operation_updates(self):
        with self.assertRaisesRegex(GenerationError, "operation updates"):
            apply_repair_plan(make_plate_project(), {"code": "result = cq.Workplane('XY')"})

    def test_operation_repair_preserves_unrelated_operations_and_recompiles(self):
        project = make_plate_project()
        repaired = apply_repair_plan(
            project,
            {
                "operation_updates": [
                    {"id": "base", "parameters": {"length": "plate_width", "width": "plate_length", "height": "plate_thickness"}}
                ],
                "repaired_feature_ids": ["base"],
            },
        )

        self.assertEqual(repaired.cad.source_kind, "compiled")
        self.assertEqual(repaired.cad.generation_attempt, project.cad.generation_attempt + 1)
        self.assertEqual(repaired.feature_graph.operations[0].parameters["length"], "plate_width")
        self.assertEqual(len(repaired.generation_history), 1)

    def test_plan_shape_requires_parameters_and_feature_graph_not_code(self):
        self.assertTrue(ai._has_cad_plan_shape(plate_plan()))
        self.assertFalse(ai._has_cad_plan_shape({"parameters": plate_plan()["parameters"], "code": "result = 1"}))

    def test_plan_prompt_forbids_python_source(self):
        response = plate_plan()
        with patch.object(ai, "_chat_json", AsyncMock(return_value=response)) as chat:
            result = asyncio.run(plan_cad_project(plate_analysis(), "", "key"))

        self.assertEqual(result, response)
        system_prompt = chat.await_args.args[2]["messages"][0]["content"]
        self.assertIn("Do not return Python or CadQuery source", system_prompt)

    def test_repair_prompt_contains_graph_but_not_previous_code(self):
        project = make_plate_project()
        with patch.object(ai, "_chat_json", AsyncMock(return_value={"operation_updates": []})) as chat:
            asyncio.run(plan_repair(project, "fix base", None, "key"))

        payload = json.loads(chat.await_args.args[2]["messages"][1]["content"])
        self.assertIn("feature_graph", payload)
        self.assertNotIn("previous_code", payload)

    def test_advisory_comparison_does_not_mutate_geometry(self):
        project = make_plate_project()
        project.generation.render_artifacts = {
            view: RenderArtifact(view=view, image_data="data:image/png;base64,eA==", sha256="x", width=1, height=1)
            for view in ("front", "top", "right", "isometric")
        }
        source_before = project.cad.source
        graph_before = project.feature_graph.model_dump()
        comparison = VisualComparison(
            status="advisory",
            issues=[VisualIssue(issue_type="missing", description="Missing hole", severity="high", feature_id="base")],
        )

        with patch.object(main, "compare_project_renders", AsyncMock(return_value=comparison)), patch.dict(
            main.os.environ, {"OPEN_ROUTER_KEY": "key"}
        ):
            response = asyncio.run(main.compare_generated_project(CompareRequest(project=project)))

        self.assertEqual(response["project"]["cad"]["source"], source_before)
        self.assertEqual(response["project"]["feature_graph"], graph_before)


if __name__ == "__main__":
    unittest.main()
