from __future__ import annotations

import unittest

from pydantic import ValidationError

from app.models import FeatureGraph


class FeatureGraphModelTests(unittest.TestCase):
    def make_perforated_rib_graph(self):
        return {
            "operations": [
                {
                    "id": "enclosure_body",
                    "name": "Enclosure body",
                    "type": "box",
                    "operation": "add",
                    "profile": {
                        "type": "rectangle",
                        "dimensions": {"length": "body_length", "width": "body_width"},
                    },
                    "parameters": {"height": "body_height"},
                    "evidence": {"views": ["front", "top"], "source": "visible_geometry"},
                    "confidence": 0.98,
                },
                {
                    "id": "left_rib",
                    "name": "Left rib",
                    "type": "rib",
                    "operation": "add",
                    "target": "enclosure_body",
                    "placement": {
                        "reference": "left_inner_face",
                        "direction": "inward",
                        "origin": ["rib_x", 0, "rib_z"],
                    },
                    "parameters": {"thickness": "rib_thickness"},
                    "confidence": 0.95,
                },
                {
                    "id": "left_rib_perforation",
                    "name": "Left rib perforation",
                    "type": "hole_pattern",
                    "operation": "pattern",
                    "target": "left_rib",
                    "profile": {
                        "type": "circle",
                        "dimensions": {"diameter": "rib_hole_diameter"},
                    },
                    "placement": {
                        "reference": "left_rib_outer_face",
                        "direction": "through_target",
                    },
                    "pattern": {
                        "type": "linear",
                        "count": "rib_hole_count",
                        "pitch": "rib_hole_pitch",
                        "axis": "rib_length_axis",
                        "start_margin": "rib_hole_margin",
                    },
                    "depends_on": ["left_rib"],
                    "evidence": {"views": ["front", "section_a"], "source": "visible_geometry"},
                    "confidence": 0.91,
                    "status": "planned",
                },
            ]
        }

    def test_valid_perforated_rib_graph(self):
        graph = FeatureGraph.model_validate(self.make_perforated_rib_graph())

        self.assertEqual(len(graph.operations), 3)
        self.assertEqual(graph.operations[2].pattern.type, "linear")
        self.assertEqual(graph.operations[2].target, "left_rib")

    def test_rejects_duplicate_operation_ids(self):
        payload = self.make_perforated_rib_graph()
        payload["operations"][2]["id"] = "left_rib"

        with self.assertRaisesRegex(ValidationError, "duplicate feature operation IDs"):
            FeatureGraph.model_validate(payload)

    def test_rejects_unknown_target(self):
        payload = self.make_perforated_rib_graph()
        payload["operations"][2]["target"] = "missing_rib"

        with self.assertRaisesRegex(ValidationError, "references unknown feature 'missing_rib'"):
            FeatureGraph.model_validate(payload)

    def test_rejects_malformed_placement(self):
        payload = self.make_perforated_rib_graph()
        payload["operations"][1]["placement"]["origin"] = [0, 1]

        with self.assertRaisesRegex(ValidationError, "exactly three coordinates"):
            FeatureGraph.model_validate(payload)

    def test_rejects_malformed_pattern(self):
        payload = self.make_perforated_rib_graph()
        del payload["operations"][2]["pattern"]["pitch"]

        with self.assertRaisesRegex(ValidationError, "linear pattern requires pitch and axis"):
            FeatureGraph.model_validate(payload)

    def test_rejects_unsupported_coverage_status(self):
        payload = self.make_perforated_rib_graph()
        payload["operations"][2]["status"] = "ignored"

        with self.assertRaises(ValidationError):
            FeatureGraph.model_validate(payload)

    def test_non_final_coverage_requires_explanation(self):
        payload = self.make_perforated_rib_graph()
        payload["operations"][2]["status"] = "unsupported"

        with self.assertRaisesRegex(ValidationError, "unsupported feature requires assumption"):
            FeatureGraph.model_validate(payload)

    def test_project_backward_compatibility_is_covered_by_existing_fixtures(self):
        self.assertEqual(FeatureGraph().operations, [])


if __name__ == "__main__":
    unittest.main()
