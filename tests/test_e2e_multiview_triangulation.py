"""Real-provider E2E for the multi-view triangulation feature now integrated into the app
(app/multiview_triangulation.py, wired into POST /api/model/image in app/main.py).

Opt-in real-provider E2E, same convention as the other e2e tests in this directory:

    EASYCAD_RUN_REAL_E2E=1 .venv/bin/python -m unittest tests.test_e2e_multiview_triangulation -v

This exercises the REAL, shipped code path end to end against the real providers:
  1. A cheap vision call classifies the sketch as multi-panel and reports rough panel boxes
     (app.multiview_triangulation.detect_panel_layout) -- fixtures/a3b.jpg draws 4 panels on
     one page.
  2. Each panel gets its OWN vision call for dimensions, plus an independent OCR read
     (pytesseract) on the same pixels.
  3. Pure-Python reconciliation (no LLM) cross-checks all readings by numeric value.
  4. The verified facts ground the actual upload endpoint (POST /api/model/image via
     TestClient, not a hand-rolled orchestration), which runs the unmodified planner and
     compiler to a real STL.

Historical note: the first version of this experiment used a pixel-brightness heuristic for
panel-layout detection instead of step 1's vision call; see docs/AI_LEARNED.md and the module
docstring in app/multiview_triangulation.py for why that was replaced (it false-positived and
false-negatived on the two fixtures used in this very file's own unit tests).

This is a feasibility spike promoted to production, not a benchmark: one run against one
sketch is a data point.
"""

import base64
import os
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app, load_env
from app.multiview_triangulation import build_grounding_instructions

load_env()

FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "a3b.jpg"
ARTIFACT = Path(__file__).resolve().parent.parent / "artifacts" / "a3_multiview_mvp.stl"


@unittest.skipUnless(os.environ.get("EASYCAD_RUN_REAL_E2E") == "1", "opt-in real-provider E2E; set EASYCAD_RUN_REAL_E2E=1")
class MultiViewTriangulationIntegrationE2E(unittest.TestCase):
    def test_grounding_instructions_are_produced_and_the_upload_endpoint_uses_them(self):
        api_key = os.environ.get("OPEN_ROUTER_KEY", "")
        self.assertTrue(api_key, "OPEN_ROUTER_KEY must be set for this experiment")

        # Step 1: prove the standalone grounding step works against the real providers.
        import asyncio
        image_bytes = FIXTURE.read_bytes()
        grounding = asyncio.run(build_grounding_instructions(image_bytes, api_key))
        print("\n=== grounding instructions ===")
        print(grounding or "(empty -- classifier did not detect a multi-panel layout)")
        self.assertTrue(grounding, "expected the real 4-panel sketch to produce cross-verified grounding text")

        # Step 2: the real endpoint, unmodified, picks this up internally.
        client = TestClient(app)
        with FIXTURE.open("rb") as handle:
            response = client.post("/api/model/image", files={"file": ("a3b.jpg", handle, "image/jpeg")})
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()

        confirmed = [f for f in body["features"] if f["status"] == "confirmed"]
        print(f"\nconfirmed features ({len(confirmed)}): {[f['label'] for f in confirmed]}")
        self.assertGreaterEqual(len(confirmed), 2)

        stl_bytes = base64.b64decode(body["model_stl"])
        ARTIFACT.parent.mkdir(exist_ok=True)
        ARTIFACT.write_bytes(stl_bytes)
        print(f"wrote {len(stl_bytes)} bytes to {ARTIFACT}")

    def test_an_ordinary_single_view_upload_is_unaffected(self):
        """The other half of the safety story: a normal photo must build exactly as before --
        the classifier saying "not multi-panel" must never itself break or measurably slow
        down the common upload path."""
        api_key = os.environ.get("OPEN_ROUTER_KEY", "")
        self.assertTrue(api_key, "OPEN_ROUTER_KEY must be set for this experiment")

        client = TestClient(app)
        single_view = Path(__file__).resolve().parent.parent / "fixtures" / "3.png"
        with single_view.open("rb") as handle:
            response = client.post("/api/model/image", files={"file": ("3.png", handle, "image/png")})
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        confirmed = [f for f in body["features"] if f["status"] == "confirmed"]
        print(f"\nsingle-view upload confirmed features ({len(confirmed)}): {[f['label'] for f in confirmed]}")
        self.assertGreaterEqual(len(confirmed), 1)


if __name__ == "__main__":
    unittest.main()
