"""Real-provider E2E MVP: process the a3 open-box sketch end to end and produce a real STL.

Opt-in only (hits the real vision/planner providers and costs real time/money):

    EASYCAD_RUN_REAL_E2E=1 .venv/bin/python -m unittest tests.test_e2e_a3_open_box -v

Do not mock the network calls — the point is to prove the actual pipeline (vision
analysis -> planner -> minimal_reliable_draft -> real CadQuery build) produces a
plausible open box from a real hand-drawn sketch, not to replay a fixed script.
"""

import base64
import os
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app

FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "a3b.jpg"
ARTIFACT = Path(__file__).resolve().parent.parent / "artifacts" / "a3_open_box_mvp.stl"

# From the sketch: outer 80 x 50 x 30, wall 3, bottom 2, outer/inner corner radius 5/2,
# rim on the short side (width 5, height 3). Order-independent sanity tolerance in mm.
EXPECTED_OUTER_DIMS = sorted([80, 50, 30])
DIM_TOLERANCE_MM = 6  # generous: rim/fillet placement can nudge the bounding box a little


@unittest.skipUnless(os.environ.get("EASYCAD_RUN_REAL_E2E") == "1", "opt-in real-provider E2E; set EASYCAD_RUN_REAL_E2E=1")
class A3OpenBoxMVPPipelineE2E(unittest.TestCase):
    def test_sketch_to_stl_produces_a_plausible_open_box(self):
        client = TestClient(app)

        with FIXTURE.open("rb") as handle:
            response = client.post("/api/model/image", files={"file": ("a3b.jpg", handle, "image/jpeg")})
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()

        roster = body["features"]
        confirmed = [item for item in roster if item["status"] == "confirmed"]
        omitted = [item for item in roster if item["status"] == "unsupported"]
        print(f"\ndescription: {body['description']}")
        print(f"confirmed features ({len(confirmed)}): {[item['label'] for item in confirmed]}")
        if omitted:
            print(f"omitted features ({len(omitted)}): {[(item['label'], item['omission_reason']) for item in omitted]}")
        self.assertGreaterEqual(len(confirmed), 2, "expected at least a body and one cut/feature to survive")

        bbox = body["model"]["generation"]["bounding_box"]
        self.assertIsNotNone(bbox, "a successful build must report a bounding box")
        actual_dims = sorted([bbox["x"], bbox["y"], bbox["z"]])
        print(f"bounding box (sorted mm): {actual_dims}")
        for actual, expected in zip(actual_dims, EXPECTED_OUTER_DIMS):
            self.assertAlmostEqual(actual, expected, delta=DIM_TOLERANCE_MM,
                                    msg=f"bounding box {actual_dims} does not resemble the sketch's 80x50x30 outer envelope")

        stl_bytes = base64.b64decode(body["model_stl"])
        self.assertGreater(len(stl_bytes), 1000, "STL payload looks too small to be real geometry")
        ARTIFACT.parent.mkdir(exist_ok=True)
        ARTIFACT.write_bytes(stl_bytes)
        print(f"wrote {len(stl_bytes)} bytes to {ARTIFACT}")


if __name__ == "__main__":
    unittest.main()
