from __future__ import annotations

import asyncio
import json
import os
import unittest
from pathlib import Path

import app.ai_generation as ai
from app.main import load_env


ROOT = Path(__file__).resolve().parent.parent
IMAGE_DIR = ROOT / "tests" / "fixtures" / "capabilities" / "images"
OUT_DIR = ROOT / "tests" / "fixtures" / "capabilities" / "observations"
TOKENS = {
    "ribs": ("rib", "gusset"),
    "linear_perforations": ("hole", "perfor", "pattern"),
    "polar_perforations": ("hole", "perfor", "polar", "circular"),
    "slots": ("slot",),
    "pockets": ("pocket", "recess"),
    "shells": ("shell", "wall", "open top"),
    "text": ("text", "engrave", "letter"),
}


class RealCapabilityEvaluation(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        load_env()
        if not os.environ.get("OPEN_ROUTER_KEY"):
            raise unittest.SkipTest("Missing OPEN_ROUTER_KEY")

    def test_independent_capability_drawings(self):
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        manifest = json.loads((ROOT / "tests" / "fixtures" / "capabilities" / "cases.json").read_text())
        for case in manifest:
            if case["status"] != "supported":
                continue
            for index, variant in enumerate(case["variants"], start=1):
                with self.subTest(capability=case["id"], variant=variant):
                    path = IMAGE_DIR / f"{case['id']}.{variant}.png"
                    analysis = asyncio.run(
                        ai.analyze_drawing(
                            path.read_bytes(),
                            "image/png",
                            "Identify every feature and preserve all written dimensions. Do not generate CAD code.",
                            os.environ["OPEN_ROUTER_KEY"],
                        )
                    )
                    feature_text = json.dumps(analysis.get("features", []), ensure_ascii=False).lower()
                    detected = any(token in feature_text for token in TOKENS[case["id"]])
                    expected_dimensions = [60 + index * 10, 30 + index * 5]
                    observed_dimensions = _numeric_values(analysis.get("dimensions"))
                    errors = [
                        min((abs(expected - observed) for observed in observed_dimensions), default=float("inf"))
                        for expected in expected_dimensions
                    ]
                    record = {
                        "capability": case["id"],
                        "variant": variant,
                        "detected": detected,
                        "expected_dimensions_mm": expected_dimensions,
                        "dimension_errors_mm": errors,
                        "analysis": analysis,
                    }
                    (OUT_DIR / f"{case['id']}.{variant}.json").write_text(
                        json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
                    )


def _numeric_values(value) -> list[float]:
    values: list[float] = []
    if isinstance(value, dict):
        for item in value.values():
            values.extend(_numeric_values(item))
    elif isinstance(value, list):
        for item in value:
            values.extend(_numeric_values(item))
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        values.append(float(value))
    return values


if __name__ == "__main__":
    unittest.main()
