from __future__ import annotations

import asyncio
import json
import os
import unittest
from pathlib import Path

import app.ai_generation as ai
from app.main import load_env


ROOT = Path(__file__).resolve().parent.parent
RECORDED_DIR = ROOT / "tests" / "fixtures" / "llm_features"
CASES = {
    "repeated_cuts": {
        "path": ROOT / "fixtures" / "4.jpg",
        "mime_type": "image/jpeg",
        "required_groups": [("notch", "cutout", "slot")],
        "minimum_cut_features": 2,
    },
    "shell_ribs_perforation": {
        "path": ROOT / "fixtures" / "feature_shell_ribs_perforation.png",
        "mime_type": "image/png",
        "required_groups": [("shell", "thin_wall", "open-top"), ("rib", "gusset"), ("perfor", "hole_pattern")],
    },
    "polar_perforation": {
        "path": ROOT / "fixtures" / "feature_polar_perforation.png",
        "mime_type": "image/png",
        "required_groups": [("polar", "circular_pattern"), ("hole", "perfor")],
    },
    "recessed_text": {
        "path": ROOT / "artifacts" / "recessed_text_cube_probe.png",
        "mime_type": "image/png",
        "required_groups": [("text", "letter", "engrave"), ("recess", "cut")],
    },
    "sweep": {
        "path": ROOT / "fixtures" / "feature_sweep.png",
        "mime_type": "image/png",
        "required_groups": [("sweep", "bent", "tube"), ("path", "bend")],
    },
    "loft": {
        "path": ROOT / "fixtures" / "feature_loft.png",
        "mime_type": "image/png",
        "required_groups": [("loft", "transition"), ("circle", "square")],
    },
}


class RealFeatureAnalysisE2E(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        load_env()
        if not os.environ.get("OPEN_ROUTER_KEY"):
            raise unittest.SkipTest("Missing OPEN_ROUTER_KEY")

    def test_feature_inventory_recordings(self):
        selected = os.environ.get("EASYCAD_FEATURE_FIXTURE", "").strip()
        cases = {selected: CASES[selected]} if selected else CASES
        for name, case in cases.items():
            with self.subTest(name=name):
                analysis = asyncio.run(
                    ai.analyze_drawing(
                        case["path"].read_bytes(),
                        case["mime_type"],
                        "Preserve every visible feature separately and do not generate CAD code.",
                        os.environ["OPEN_ROUTER_KEY"],
                    )
                )
                feature_text = json.dumps(analysis.get("features", []), ensure_ascii=False).lower()
                for alternatives in case["required_groups"]:
                    self.assertTrue(
                        any(token in feature_text for token in alternatives),
                        f"{name}: expected one of {alternatives} in {feature_text}",
                    )
                cut_features = [
                    feature
                    for feature in analysis.get("features", [])
                    if feature.get("operation_hint") == "cut"
                ]
                self.assertGreaterEqual(
                    len(cut_features),
                    case.get("minimum_cut_features", 0),
                    f"{name}: expected repeated/independent cut features",
                )
                RECORDED_DIR.mkdir(parents=True, exist_ok=True)
                path = RECORDED_DIR / f"{name}.analysis.json"
                path.write_text(json.dumps(analysis, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
