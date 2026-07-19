"""Real-provider E2E for SPEC 9 Part D: scoped refinement with real feature IDs.

Opt-in only (hits the real vision/planner providers and costs real time/money):

    EASYCAD_RUN_REAL_E2E=1 .venv/bin/python -m unittest tests.test_e2e_scoped_refine -v

Do not mock the network calls in this file — that is the one thing a unit test
cannot prove (that a real provider actually receives and acts on
referenced_feature_ids), which is exactly what this test exists to check.
"""

import os
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app

FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "3.png"


@unittest.skipUnless(os.environ.get("EASYCAD_RUN_REAL_E2E") == "1", "opt-in real-provider E2E; set EASYCAD_RUN_REAL_E2E=1")
class ScopedRefineRealProviderE2E(unittest.TestCase):
    def test_scoped_refine_with_a_real_feature_id_updates_the_model(self):
        client = TestClient(app)

        with FIXTURE.open("rb") as handle:
            image_response = client.post("/api/model/image", files={"file": ("3.png", handle, "image/png")})
        self.assertEqual(image_response.status_code, 200, image_response.text)
        image_body = image_response.json()
        roster = image_body["features"]
        self.assertTrue(roster, "the first response must include a non-empty feature roster")
        confirmed = [item for item in roster if item["status"] == "confirmed"]
        self.assertTrue(confirmed, "at least one feature must survive minimal_reliable_draft")
        target = confirmed[0]

        refine_response = client.post(
            "/api/model/refine",
            json={
                "specification": image_body["specification"],
                "prompt": f"make {target['label']} 5mm taller",
                "referenced_feature_ids": [target["id"]],
            },
        )
        self.assertEqual(refine_response.status_code, 200, refine_response.text)
        refine_body = refine_response.json()
        self.assertTrue(refine_body["model_stl"], "refine must return a non-empty STL")
        self.assertIn("features", refine_body)
        self.assertTrue(refine_body["features"], "the refined response must also carry a feature roster")


if __name__ == "__main__":
    unittest.main()
