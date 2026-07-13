from __future__ import annotations

import unittest

import app.main as main
from app.feature_compiler import CompilerError, compile_feature_graph
from app.models import CADProject, CADSource, FeatureGraph
from app.runner import RunnerError, run_project


def project(operations, parameters) -> CADProject:
    graph = FeatureGraph.model_validate({"operations": operations})
    source = compile_feature_graph(graph, parameters)
    return CADProject(
        parameters={
            key: {"label": key, "value": value, "type": "text" if isinstance(value, str) else "number"}
            for key, value in parameters.items()
        },
        feature_graph=graph,
        cad=CADSource(source=source, source_kind="compiled"),
    )


class CompilerNegativeTests(unittest.TestCase):
    def test_invalid_pattern_axis_is_scoped_to_operation(self):
        graph = FeatureGraph.model_validate(
            {
                "operations": [
                    {"id": "base", "type": "box", "operation": "add", "parameters": {"length": 20, "width": 10, "height": 5}},
                    {
                        "id": "bad_pattern",
                        "type": "hole_pattern",
                        "operation": "pattern",
                        "target": "base",
                        "profile": {"type": "circle", "dimensions": {"diameter": 2}},
                        "parameters": {"depth": 7},
                        "pattern": {"type": "linear", "count": 2, "pitch": 5, "axis": "Q"},
                    },
                ]
            }
        )
        with self.assertRaisesRegex(CompilerError, "bad_pattern.*axis must be X or Y"):
            compile_feature_graph(graph, {})

    def test_zero_count_pattern_fails_with_operation_id(self):
        model = project(
            [
                {"id": "base", "type": "box", "operation": "add", "parameters": {"length": 20, "width": 10, "height": 5}},
                {
                    "id": "zero_pattern",
                    "type": "hole_pattern",
                    "operation": "pattern",
                    "target": "base",
                    "profile": {"type": "circle", "dimensions": {"diameter": 2}},
                    "parameters": {"depth": 7},
                    "pattern": {"type": "linear", "count": 0, "pitch": 5, "axis": "X"},
                },
            ],
            {},
        )
        with self.assertRaises(RunnerError) as raised:
            run_project(model, {}, fmt="stl")
        self.assertEqual(raised.exception.detail.get("operation_id"), "zero_pattern")

    def test_oversized_modifier_fails_with_operation_id(self):
        model = project(
            [
                {"id": "base", "type": "box", "operation": "add", "parameters": {"length": 20, "width": 10, "height": 5}},
                {"id": "bad_fillet", "type": "fillet", "operation": "modify", "target": "base", "parameters": {"radius": 100}},
            ],
            {},
        )
        with self.assertRaises(RunnerError) as raised:
            run_project(model, {}, fmt="stl")
        self.assertEqual(raised.exception.detail.get("operation_id"), "bad_fillet")

    def test_cut_outside_target_is_rejected_as_noop(self):
        model = project(
            [
                {"id": "base", "type": "box", "operation": "add", "parameters": {"length": 20, "width": 10, "height": 5}},
                {"id": "outside_hole", "type": "hole", "operation": "cut", "target": "base", "parameters": {"diameter": 2, "depth": 7}, "placement": {"origin": [100, 100, -1]}},
            ],
            {},
        )
        result = run_project(model, {}, fmt="stl")
        with self.assertRaises(RunnerError) as raised:
            main.validate_feature_measurements(model, result)
        self.assertIn("outside_hole", str(raised.exception.detail))
        self.assertIn("did not remove material", str(raised.exception.detail))

    def test_empty_text_cannot_be_reported_as_successful_cut(self):
        model = project(
            [
                {"id": "base", "type": "box", "operation": "add", "parameters": {"length": 20, "width": 10, "height": 5}},
                {"id": "label", "type": "text", "operation": "cut", "target": "base", "parameters": {"content": "label_text", "size": 4, "distance": -1}, "placement": {"origin": [10, 5, 5]}},
            ],
            {"label_text": ""},
        )
        with self.assertRaises(RunnerError) as raised:
            run_project(model, {}, fmt="stl")
        self.assertEqual(raised.exception.detail.get("operation_id"), "label")


if __name__ == "__main__":
    unittest.main()
