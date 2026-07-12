from __future__ import annotations

import json
import hashlib
import io
import unittest
from pathlib import Path

from PIL import Image, ImageEnhance, ImageOps
from app.feature_compiler import CompilerError, compile_feature_graph
from app.models import CADProject, CADSource, FeatureGraph
from app.runner import run_project
from app.validator import validate_source


ROOT = Path(__file__).resolve().parent.parent


class CapabilityFixtureTests(unittest.TestCase):
    @classmethod
    def cases(cls):
        return json.loads(
            (ROOT / "tests" / "fixtures" / "capabilities" / "cases.json").read_text(encoding="utf-8")
        )

    @staticmethod
    def render_variant(image: Image.Image, variant: str) -> bytes:
        image = image.convert("RGB")
        if variant == "resized":
            image = image.resize((max(1, image.width * 3 // 4), max(1, image.height * 3 // 4)))
        elif variant == "high_contrast":
            image = ImageEnhance.Contrast(image).enhance(1.8)
        elif variant == "rotated":
            image = image.rotate(2, expand=True, fillcolor="white")
        elif variant == "ambiguous_crop":
            margin_x = max(1, image.width // 12)
            margin_y = max(1, image.height // 12)
            image = ImageOps.expand(
                image.crop((margin_x, margin_y, image.width - margin_x, image.height - margin_y)),
                border=(margin_x, margin_y),
                fill="white",
            )
        output = io.BytesIO()
        image.save(output, format="PNG")
        return output.getvalue()

    def test_capability_fixtures_have_sources_and_expected_compiler_status(self):
        expected_ids = {
            "ribs", "linear_perforations", "polar_perforations", "slots", "pockets", "shells", "text", "sweeps", "lofts"
        }
        cases = self.cases()
        self.assertEqual({case["id"] for case in cases}, expected_ids)
        for case in cases:
            with self.subTest(case=case["id"]):
                self.assertTrue((ROOT / case["source_drawing"]).exists())
                graph = FeatureGraph.model_validate({"operations": case["operations"]})
                if case["status"] == "supported":
                    validate_source(compile_feature_graph(graph, case["parameters"]))
                else:
                    with self.assertRaises(CompilerError):
                        compile_feature_graph(graph, case["parameters"])

    def test_supported_capabilities_export_valid_stl_and_step(self):
        for case in self.cases():
            if case["status"] != "supported":
                continue
            with self.subTest(case=case["id"]):
                graph = FeatureGraph.model_validate({"operations": case["operations"]})
                source = compile_feature_graph(graph, case["parameters"])
                project = CADProject(
                    title=f"Capability fixture: {case['id']}",
                    parameters={
                        key: {
                            "label": key,
                            "value": value,
                            "type": "text" if isinstance(value, str) else "number",
                            "unit": "" if isinstance(value, str) else "mm",
                        }
                        for key, value in case["parameters"].items()
                    },
                    feature_graph=graph,
                    cad=CADSource(source=source),
                )

                for fmt in ("stl", "step"):
                    result = run_project(project, {}, fmt=fmt)
                    self.assertEqual(result["status"], "success")
                    self.assertGreater(len(result["artifact_bytes"]), 100)
                    self.assertGreater(result["volume_mm3"], 0)
                    self.assertGreaterEqual(result["solid_count"], 1)

    def test_supported_capabilities_have_five_deterministic_drawing_variants(self):
        expected_variants = {"control", "resized", "high_contrast", "rotated", "ambiguous_crop"}
        for case in self.cases():
            if case["status"] != "supported":
                continue
            with self.subTest(case=case["id"]):
                self.assertEqual(set(case["variants"]), expected_variants)
                fixture_paths = [
                    ROOT / "tests" / "fixtures" / "capabilities" / "images" / f"{case['id']}.{variant}.png"
                    for variant in case["variants"]
                ]
                self.assertTrue(all(path.exists() for path in fixture_paths))
                stored_hashes = {
                    hashlib.sha256(path.read_bytes()).hexdigest() for path in fixture_paths
                }
                self.assertEqual(len(stored_hashes), 5)
                with Image.open(ROOT / case["source_drawing"]) as source:
                    first = [self.render_variant(source, variant) for variant in case["variants"]]
                    second = [self.render_variant(source, variant) for variant in case["variants"]]
                self.assertEqual(first, second)
                self.assertEqual(len({hashlib.sha256(payload).hexdigest() for payload in first}), 5)


if __name__ == "__main__":
    unittest.main()
