from __future__ import annotations

import json
import hashlib
import unittest
from pathlib import Path

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
                    expected_measurements = case.get("expected_measurements")
                    self.assertIsInstance(expected_measurements, dict)
                    self.assertEqual(expected_measurements.get("result", {}).get("solid_count"), 1)
                    self.assertEqual(
                        set(expected_measurements.get("operations", {})),
                        {operation.id for operation in graph.operations},
                    )
                    self.assertTrue(
                        all(
                            expectation.get("volume_delta_sign") in {"positive", "negative"}
                            for expectation in expected_measurements["operations"].values()
                        )
                    )
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

    def test_supported_capabilities_have_five_independent_drawing_variants(self):
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

    def test_each_independent_variant_exports_stl_and_step(self):
        for case in self.cases():
            if case["status"] != "supported":
                continue
            graph = FeatureGraph.model_validate({"operations": case["operations"]})
            for index, variant in enumerate(case["variants"], start=1):
                with self.subTest(case=case["id"], variant=variant):
                    values = {
                        key: (
                            value
                            if isinstance(value, str)
                            else max(1, int(value + index - 3))
                            if key == "count"
                            else value * (0.8 + index * 0.08)
                        )
                        for key, value in case["parameters"].items()
                    }
                    source = compile_feature_graph(graph, values)
                    model = CADProject(
                        parameters={
                            key: {
                                "label": key,
                                "value": value,
                                "type": "text" if isinstance(value, str) else "number",
                                "unit": "" if isinstance(value, str) else "mm",
                            }
                            for key, value in values.items()
                        },
                        feature_graph=graph,
                        cad=CADSource(source=source, source_kind="compiled"),
                    )
                    for fmt in ("stl", "step"):
                        result = run_project(model, {}, fmt=fmt)
                        self.assertEqual(result["status"], "success")
                        self.assertGreater(result["volume_mm3"], 0)


if __name__ == "__main__":
    unittest.main()
