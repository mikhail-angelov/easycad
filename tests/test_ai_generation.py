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
    def test_expression_parameter_accepts_provider_value_field(self):
        parameters = ai._normalize_parameters(
            [{"id": "height", "type": "expression", "value": "overall_height - base_height"}]
        )
        self.assertEqual(parameters["height"].expression, "overall_height - base_height")

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

    def test_operation_repair_accepts_provider_changes_wrapper(self):
        project = make_plate_project()
        repaired = apply_repair_plan(
            project,
            {
                "operation_updates": [
                    {"id": "base", "changes": {"parameters": {"length": "plate_width", "width": "plate_length", "height": "plate_thickness"}}}
                ],
                "repaired_feature_ids": ["base"],
            },
        )

        self.assertEqual(repaired.feature_graph.operations[0].parameters["length"], "plate_width")

    def test_operation_repair_rejects_provider_type_replacement(self):
        with self.assertRaisesRegex(GenerationError, "cannot change operation type"):
            apply_repair_plan(
                make_plate_project(),
                {
                    "operation_updates": [{"id": "base", "type": "cylinder"}],
                    "repaired_feature_ids": ["base"],
                },
            )

    def test_operation_repair_rejects_provider_target_replacement(self):
        with self.assertRaisesRegex(GenerationError, "cannot change operation target"):
            apply_repair_plan(
                make_plate_project(),
                {
                    "operation_updates": [{"id": "base", "target": "other_body"}],
                    "repaired_feature_ids": ["base"],
                },
            )

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
        self.assertIn("source_feature_ids", system_prompt)
        self.assertIn("do not use an L-shape profile", system_prompt)

    def test_direct_structured_operation_needs_no_model_implementation_object(self):
        plan = plate_plan()
        operation = plan["feature_graph"]["operations"][0]
        operation.pop("implementation")

        project = project_from_plan(plan, plate_analysis(), {}, b"")

        self.assertEqual(project.feature_graph.operations[0].status, "implemented")
        self.assertEqual(project.feature_graph.operations[0].implementation, "base")

    def test_prefixed_operation_id_maps_to_exact_analysis_feature_id(self):
        plan = plate_plan()
        operation = plan["feature_graph"]["operations"][0]
        operation["id"] = "op_base"
        operation["implementation"] = "op_base"
        operation["source_feature_ids"] = []

        project = project_from_plan(plan, plate_analysis(), {}, b"")

        coverage = {entry.feature_id: entry.status for entry in project.feature_coverage.entries}
        self.assertEqual(coverage["base"], "implemented")

    def test_provider_profile_and_placement_extras_become_reviewable_not_invalid_graph(self):
        plan = plate_plan()
        operation = plan["feature_graph"]["operations"][0]
        operation.update(
            {
                "type": "extrude",
                "parameters": {"distance": "plate_thickness"},
                "profile": {"type": "rectangle", "length": "plate_length", "width": "plate_width"},
                "placement": {"origin": [0, 0, 0], "direction": [0, 0, 1]},
            }
        )
        operation.pop("implementation")

        project = project_from_plan(plan, plate_analysis(), {}, b"")

        normalized = project.feature_graph.operations[0]
        self.assertEqual(normalized.profile.dimensions, {"width": "plate_length", "height": "plate_width"})
        self.assertIsNone(normalized.placement.direction)

    def test_provider_string_profile_becomes_reviewable_not_server_error(self):
        plan = plate_plan()
        plan["feature_graph"]["operations"].append(
            {
                "id": "top_groove",
                "type": "slot",
                "operation": "cut",
                "target": "base",
                "source_feature_ids": ["top_groove"],
                "status": "implemented",
                "parameters": {"length": "plate_length", "width": "plate_width", "depth": "plate_thickness"},
                "profile": "semicircular",
            }
        )
        analysis = plate_analysis()
        analysis["features"].append({"id": "top_groove", "type": "groove", "confidence": 0.9})

        project = project_from_plan(plan, analysis, {}, b"")

        normalized = project.feature_graph.operations[1]
        self.assertIsNone(normalized.profile)
        self.assertEqual(normalized.status, "approximated")
        self.assertEqual(normalized.assumption, "A semicircular groove is approximated by a slot; its section is not verified.")

    def test_inline_geometry_expressions_are_lifted_and_groove_slot_is_approximated(self):
        plan = plate_plan()
        plan["feature_graph"]["operations"][0].update(
            {
                "type": "extrude",
                "parameters": {"distance": "plate_thickness"},
                "profile": {"type": "polyline", "points": [[0, 0], ["plate_length + plate_width", 0], [0, "plate_width"]]},
            }
        )
        plan["feature_graph"]["operations"].append(
            {
                "id": "top_groove",
                "type": "slot",
                "operation": "cut",
                "target": "base",
                "source_feature_ids": ["top_groove"],
                "status": "implemented",
                "parameters": {"length": "plate_length", "width": "plate_width", "depth": "plate_thickness"},
            }
        )
        analysis = plate_analysis()
        analysis["features"].append({"id": "top_groove", "type": "groove", "confidence": 1.0})

        project = project_from_plan(plan, analysis, {}, b"")

        self.assertIn("derived_expr_1", project.parameters)
        self.assertEqual(project.feature_graph.operations[0].profile.points[1][0], "derived_expr_1")
        self.assertEqual(project.feature_graph.operations[1].status, "approximated")

    def test_non_scalar_provider_operation_parameter_becomes_unsupported_not_invalid_graph(self):
        plan = plate_plan()
        plan["feature_graph"]["operations"].append(
            {
                "id": "base_round_end",
                "type": "fillet",
                "operation": "modify",
                "target": "base",
                "source_feature_ids": ["base_round_end"],
                "status": "implemented",
                "parameters": {"radius": 2, "edges": ["front_edge"]},
            }
        )
        analysis = plate_analysis()
        analysis["features"].append({"id": "base_round_end", "type": "fillet", "confidence": 1.0})

        project = project_from_plan(plan, analysis, {}, b"")

        operation = project.feature_graph.operations[1]
        self.assertEqual(operation.status, "unsupported")
        self.assertIn("cannot be mapped safely", operation.assumption)

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
