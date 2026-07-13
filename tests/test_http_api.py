from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

import app.ai_generation as ai
import app.main as main
from app.models import FeatureOperation, VisualComparison
from tests.test_ai_generation import make_plate_project, plate_analysis, plate_plan


ROOT = Path(__file__).resolve().parent.parent


class HTTPAPITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(main.app)

    def test_generate_ignores_provider_executable_fields_and_runs_trusted_pipeline(self):
        plan = plate_plan()
        plan["code"] = "import os\nresult = os.system('bad')"
        plan["source"] = "raise RuntimeError('bad')"
        with patch.object(ai, "analyze_drawing", AsyncMock(return_value=plate_analysis())), patch.object(
            ai, "plan_cad_project", AsyncMock(return_value=plan)
        ), patch.dict(main.os.environ, {"OPEN_ROUTER_KEY": "x", "DEEP_SEEK_KEY": "x"}):
            response = self.client.post(
                "/api/projects/generate",
                files={"file": ("plate.png", (ROOT / "fixtures" / "feature_polar_perforation.png").read_bytes(), "image/png")},
            )

        self.assertEqual(response.status_code, 200, response.text)
        project = response.json()["project"]
        self.assertEqual(project["cad"]["source_kind"], "compiled")
        self.assertIn("# feature:base", project["cad"]["source"])
        self.assertNotIn("import os", project["cad"]["source"])

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

    def test_repair_route_recompiles_operation_updates(self):
        project = make_plate_project()
        repaired = ai.apply_repair_plan(
            project,
            {
                "operation_updates": [
                    {
                        "id": "base",
                        "parameters": {
                            "length": "plate_width",
                            "width": "plate_length",
                            "height": "plate_thickness",
                        },
                    }
                ],
                "repaired_feature_ids": ["base"],
            },
        )
        with patch.object(main, "repair_project", AsyncMock(return_value=repaired)):
            response = self.client.post(
                "/api/projects/repair",
                json={"project": project.model_dump(mode="json"), "user_feedback": "swap dimensions"},
            )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["project"]["cad"]["source_kind"], "compiled")

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
