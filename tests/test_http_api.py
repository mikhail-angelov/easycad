from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

import app.ai_generation as ai
import app.main as main
from app.models import FeatureOperation, SpecificationQuestion, VisualComparison
from tests.project_helpers import make_plate_project
from tests.test_specification import complete_specification


ROOT = Path(__file__).resolve().parent.parent


class HTTPAPITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(main.app)

    def test_engineering_input_quality_gate_blocks_before_llm(self):
        with patch.object(main, "generate_draft_specification_from_image", AsyncMock()) as analyze:
            response = self.client.post(
                "/api/specifications/analyze",
                files={"file": ("plate.png", (ROOT / "fixtures" / "feature_polar_perforation.png").read_bytes(), "image/png")},
                data={"input_mode": "engineering"},
            )

        self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(response.json()["detail"]["stage"], "input_quality")
        self.assertIn("orthographic_views", response.json()["detail"]["detail"]["missing"])
        analyze.assert_not_awaited()

    def test_engineering_gate_does_not_require_isometric_view(self):
        warning = main.validate_input_quality_gate(
            "engineering",
            has_orthographic_views=True,
            has_isometric_view=False,
            has_units_and_overall_dimensions=True,
            has_feature_positions=True,
            has_feature_dimensions_and_directions=True,
        )
        self.assertIn("isometric", warning)

    def test_analysis_failure_returns_stage_and_request_id(self):
        with patch.object(
            main,
            "generate_draft_specification_from_image",
            AsyncMock(side_effect=ai.GenerationError("vision_analysis", "Provider request failed", {"status_code": 429})),
        ):
            response = self.client.post(
                "/api/specifications/analyze",
                files={"file": ("plate.png", (ROOT / "fixtures" / "feature_polar_perforation.png").read_bytes(), "image/png")},
                data={"input_mode": "sketch"},
            )

        self.assertEqual(response.status_code, 422, response.text)
        detail = response.json()["detail"]
        self.assertEqual(detail["stage"], "vision_analysis")
        self.assertEqual(detail["detail"], {"status_code": 429})
        self.assertRegex(detail["request_id"], r"^[0-9a-f]{12}$")

    def test_structured_edits_trigger_a_complete_draft_replan(self):
        specification = complete_specification()
        specification.dimensions[0].status = "needs_input"
        with patch.object(main, "plan_draft_specification", AsyncMock(return_value=complete_specification())) as planner:
            response = self.client.post(
                "/api/specifications/validate",
                json={
                    "specification": specification.model_dump(mode="json"),
                    "dimension_values": {"length": 40},
                },
            )
        self.assertEqual(response.status_code, 200, response.text)
        planner.assert_awaited_once()
        self.assertTrue(response.json()["valid"])

    def test_clarifications_trigger_one_complete_draft_replan(self):
        specification = complete_specification()
        specification.dimensions[1].status = "needs_input"
        specification.questions = [SpecificationQuestion(id="width_question", field_id="width", prompt="Enter width")]
        with patch.object(
            main,
            "plan_draft_specification",
            AsyncMock(return_value=complete_specification()),
        ) as planner:
            response = self.client.post(
                "/api/specifications/validate",
                json={
                    "specification": specification.model_dump(mode="json"),
                    "clarifications": {"width_question": "The width is 32 mm."},
                },
            )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["valid"])
        self.assertEqual(planner.await_count, 1)
        self.assertEqual(planner.await_args.args[0], specification.analysis.model_dump(mode="json"))
        self.assertEqual(planner.await_args.kwargs["user_inputs"]["clarifications"], {"width_question": "The width is 32 mm."})

    def test_multiple_question_clarifications_are_sent_in_one_complete_replan(self):
        specification = complete_specification()
        specification.dimensions[0].status = "needs_input"
        specification.dimensions[1].status = "needs_input"
        specification.questions = [
            SpecificationQuestion(id="length_question", field_id="length", prompt="Enter length"),
            SpecificationQuestion(id="width_question", field_id="width", prompt="Enter width"),
        ]
        with patch.object(
            main,
            "plan_draft_specification",
            AsyncMock(return_value=complete_specification()),
        ) as planner:
            response = self.client.post(
                "/api/specifications/validate",
                json={
                    "specification": specification.model_dump(mode="json"),
                    "clarifications": {
                        "length_question": "The length is 42 mm.",
                        "width_question": "The width is 32 mm.",
                    },
                },
            )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["valid"])
        self.assertEqual(planner.await_count, 1)
        self.assertEqual(
            planner.await_args.kwargs["user_inputs"]["clarifications"],
            {"length_question": "The length is 42 mm.", "width_question": "The width is 32 mm."},
        )

    def test_confirmed_specification_builds_without_repair(self):
        response = self.client.post("/api/specifications/build", json=complete_specification().model_dump(mode="json"))
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["status"], "success")
        self.assertEqual(response.json()["project"]["cad"]["source_kind"], "compiled")

    def test_legacy_repair_endpoints_are_removed(self):
        project = make_plate_project().model_dump(mode="json")
        self.assertEqual(self.client.post("/api/projects/repair", json={"project": project}).status_code, 405)
        self.assertEqual(self.client.post("/api/projects/repair-visual", json={"project": project, "feature_id": "base"}).status_code, 405)

    def test_preview_and_stl_step_json_exports(self):
        project = make_plate_project()
        payload = {"project": project.model_dump(mode="json"), "parameters": {}}

        preview = self.client.post("/api/projects/preview", json=payload)
        self.assertEqual(preview.status_code, 200, preview.text)
        self.assertGreater(len(preview.content), 100)
        for fmt in ("stl", "step", "json"):
            with self.subTest(fmt=fmt):
                response = self.client.post(f"/api/projects/export?format={fmt}", json=payload)
                self.assertEqual(response.status_code, 200, response.text)
                self.assertGreater(len(response.content), 100)

    def test_preview_is_available_for_compilable_project_needing_review(self):
        project = make_plate_project()
        project.generation.status = "needs_review"
        project.feature_graph.operations.append(
            FeatureOperation.model_validate(
                {
                "id": "unresolved_detail",
                "type": "freeform_sweep",
                "operation": "add",
                "source_feature_ids": ["unresolved_detail"],
                "status": "unsupported",
                "assumption": "No trusted compiler operation.",
                }
            )
        )

        response = self.client.post(
            "/api/projects/preview",
            json={"project": project.model_dump(mode="json"), "parameters": {}},
        )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertGreater(len(response.content), 100)

    def test_compare_is_advisory(self):
        project = make_plate_project()
        before = project.feature_graph.model_dump()
        with patch.object(main, "compare_project_renders", AsyncMock(return_value=VisualComparison(status="advisory"))), patch.dict(
            main.os.environ, {"OPEN_ROUTER_KEY": "x"}
        ):
            response = self.client.post("/api/projects/compare", json={"project": project.model_dump(mode="json")})

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["project"]["feature_graph"], before)

    def test_legacy_and_unresolved_exports_are_blocked(self):
        project = make_plate_project()
        project.cad.source_kind = "generated"
        response = self.client.post(
            "/api/projects/export?format=stl",
            json={"project": project.model_dump(mode="json"), "parameters": {}},
        )
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["detail"]["stage"], "legacy_project")


if __name__ == "__main__":
    unittest.main()
