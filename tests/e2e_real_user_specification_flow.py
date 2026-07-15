"""Real-provider user journey: upload a drawing, accept all proposals, build an STL."""
from __future__ import annotations

import json
import os
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app, load_env


ROOT = Path(__file__).resolve().parent.parent
IMAGE = ROOT / os.environ.get("EASYCAD_USER_FLOW_IMAGE", "fixtures/3.png")
OUT = ROOT / "artifacts" / f"real-user-flow-{IMAGE.stem}"


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
            files={"file": (IMAGE.name, IMAGE.read_bytes(), "image/jpeg" if IMAGE.suffix.lower() == ".jpg" else "image/png")},
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

        validation_payload = _resolve_user_review(client, initial)
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

        build_payload = _build_with_one_user_geometry_correction(client, replanned)
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


def _build_with_one_user_geometry_correction(client: TestClient, specification: dict) -> dict:
    current = specification
    correction = _build_repair_instruction(specification)
    for attempt in range(3):
        build = client.post("/api/specifications/build", json=current)
        unittest.TestCase().assertEqual(build.status_code, 200, build.text)
        result = build.json()
        _write_json("build.json" if attempt == 0 else f"rebuild_{attempt}.json", result)
        if result["status"] == "success":
            return result
        repaired = client.post(
            "/api/specifications/validate",
            json={
                "specification": current,
                "dimension_values": {},
                "accepted_feature_ids": [],
                "accepted_assumption_ids": [],
                "clarifications": {"build_repair": " ".join([*result.get("repair_hints", []), correction])},
            },
        )
        unittest.TestCase().assertEqual(repaired.status_code, 200, repaired.text)
        repair_payload = repaired.json()
        _write_json(f"repair_validation_{attempt + 1}.json", repair_payload)
        unittest.TestCase().assertTrue(repair_payload["valid"], repair_payload.get("diagnostics"))
        current = repair_payload["specification"]
        _write_json(f"repaired_specification_{attempt + 1}.json", current)
    return result


def _build_repair_instruction(specification: dict) -> str:
    if "bolt" in specification.get("title", "").lower():
        return (
            "Correct the complete bolt geometry. The root hex_head is an extrude on XY with a required polyline profile of "
            "six numeric [x,y] points and distance=head_thickness=12. The shank is an additive cylinder targeting hex_head, "
            "plane XY, radius=8, height=total_length minus head_thickness (38), origin [0,0,12]. "
            "There is no thread primitive: retain a smooth shank and record the thread approximation only as an assumption. "
            "Do not create unsupported groove, text, or revolve thread features. Any chamfer must target the existing root and "
            "use positive distance. For chamfer, target is the feature ID; never put a feature ID in placement.reference. "
            "Omit placement.reference unless you supply a real CadQuery edge selector such as >Z."
        )
    return (
        "Correct the rounded base end and the through-hole placement. The R30 arc and Ø24 hole center are at "
        "X=base_straight_length and Y=30 mm (overall_width / 2), never Y=0 or Y=overall_width. "
        "Keep the finished overall Y width exactly 60 mm. Correct the top groove too: it is a cylinder on plane XZ, "
        "with radius 12, height overall_width=60, and origin [14, overall_width=60, overall_height=56], so it runs along Y and "
        "removes the upper semicircle of the upright."
    )


def _resolve_user_review(client: TestClient, specification: dict) -> dict:
    current = specification
    for _ in range(4):
        validation = client.post(
            "/api/specifications/validate",
            json={
                "specification": current,
                "accepted_feature_ids": [item["id"] for item in current["features"]],
                "accepted_assumption_ids": [item["id"] for item in current["assumptions"]],
                "dimension_values": {item["id"]: _user_dimension_value(item) for item in current["dimensions"] if item["status"] in {"needs_input", "conflicted", "assumed"}},
                "clarifications": {question["id"]: _user_answer(question["prompt"]) for question in current["questions"]},
            },
        )
        unittest.TestCase().assertEqual(validation.status_code, 200, validation.text)
        payload = validation.json()
        if payload["valid"]:
            return payload
        current = payload["specification"]
    return payload


def _write_json(name: str, value: object) -> None:
    (OUT / name).write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def _user_answer(prompt: str) -> str:
    lower = prompt.lower()
    if "thread" in lower:
        return "Confirm an M16 thread: use the standard coarse pitch 2 mm, but represent it as a smooth shank because threads are not yet supported."
    if "hex" in lower or "head" in lower:
        return "Confirm the hex head is 12 mm thick and 27 mm across flats."
    if "groove" in lower:
        return "Confirm the R12 groove is centered, lies on the top surface, and runs through the full 60 mm Y extent."
    if "hole" in lower or "concentric" in lower:
        return "Confirm the Ø24 hole is concentric with the R30 arc and cuts through the 20 mm base only."
    return "Confirm the proposed geometry shown in the drawing."


def _user_dimension_value(dimension: dict) -> float:
    if isinstance(dimension.get("value"), (int, float)):
        return float(dimension["value"])
    alternatives = [value for value in dimension.get("alternatives", []) if isinstance(value, (int, float))]
    if alternatives:
        return float(alternatives[0])
    if "chamfer" in dimension.get("label", "").lower():
        return 22.5
    if "thread pitch" in dimension.get("label", "").lower():
        return 2.0
    if "washer" in dimension.get("label", "").lower():
        return 2.0
    if "corner radius" in dimension.get("label", "").lower():
        return 1.0
    if "distance" in dimension.get("label", "").lower():
        return 1.5
    if "total length" in dimension.get("label", "").lower():
        return 50.0 if "bolt" in dimension["id"].lower() else 78.0
    if "upright height" in dimension.get("label", "").lower():
        return 36.0
    raise AssertionError(f"The user-flow fixture needs an explicit value for {dimension['id']}")


if __name__ == "__main__":
    unittest.main()
