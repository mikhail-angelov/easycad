"""Real-provider user journey: upload a drawing, accept all proposals, build an STL."""
from __future__ import annotations

import json
import os
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app, load_env


ROOT = Path(__file__).resolve().parent.parent
IMAGE = ROOT / "fixtures" / "3.png"
OUT = ROOT / "artifacts" / "real-user-flow-bracket"


class RealUserSpecificationFlowE2E(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        load_env()
        if not os.environ.get("OPEN_ROUTER_KEY") or not os.environ.get("DEEP_SEEK_KEY"):
            raise unittest.SkipTest("Missing OPEN_ROUTER_KEY or DEEP_SEEK_KEY")

    def test_accepting_all_proposals_builds_and_exports_stl(self):
        OUT.mkdir(parents=True, exist_ok=True)
        client = TestClient(app)
        analysis = client.post(
            "/api/specifications/analyze",
            files={"file": (IMAGE.name, IMAGE.read_bytes(), "image/png")},
            data={
                "input_mode": "engineering",
                "has_orthographic_views": "true",
                "has_isometric_view": "true",
                "has_units_and_overall_dimensions": "true",
                "has_feature_positions": "true",
                "has_feature_dimensions_and_directions": "true",
            },
        )
        self.assertEqual(analysis.status_code, 200, analysis.text)
        initial = analysis.json()["specification"]
        _write_json("initial_draft.json", initial)

        validation = client.post(
            "/api/specifications/validate",
            json={
                "specification": initial,
                "accepted_feature_ids": [item["id"] for item in initial["features"]],
                "accepted_assumption_ids": [item["id"] for item in initial["assumptions"]],
            },
        )
        self.assertEqual(validation.status_code, 200, validation.text)
        validation_payload = validation.json()
        _write_json("validation.json", validation_payload)
        replanned = validation_payload["specification"]
        _write_json("replanned_specification.json", replanned)
        self.assertTrue(validation_payload["valid"], validation_payload.get("diagnostics"))
        self.assertEqual(replanned["questions"], [], replanned["questions"])
        self.assertTrue(replanned["features"], "replan removed the complete feature graph")
        self.assertTrue(
            set(item["id"] for item in initial["features"]).issubset(item["id"] for item in replanned["features"]),
            "replan removed a feature accepted by the user",
        )

        build = client.post("/api/specifications/build", json=replanned)
        self.assertEqual(build.status_code, 200, build.text)
        build_payload = build.json()
        _write_json("build.json", build_payload)
        self.assertEqual(build_payload["status"], "success", build_payload.get("diagnostics"))

        export = client.post(
            "/api/projects/export?format=stl",
            json={"project": build_payload["project"], "parameters": {}},
        )
        self.assertEqual(export.status_code, 200, export.text)
        self.assertEqual(export.headers["content-type"], "model/stl")
        (OUT / "model.stl").write_bytes(export.content)
        _write_json(
            "report.json",
            {
                "initial_feature_ids": [item["id"] for item in initial["features"]],
                "initial_assumption_ids": [item["id"] for item in initial["assumptions"]],
                "replanned_feature_ids": [item["id"] for item in replanned["features"]],
                "stl_bytes": len(export.content),
            },
        )


def _write_json(name: str, value: object) -> None:
    (OUT / name).write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
