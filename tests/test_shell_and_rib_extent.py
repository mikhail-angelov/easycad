"""Regression tests for two real compiler bugs found while investigating a live a3 open-box
build: shell() grew the part outward instead of hollowing inward, and rib features had no
resolvable extent so lint could never catch one placed outside its target."""

import unittest

from app.draft_lint import resolved_feature_extent
from app.models import DraftSpecification, SpecificationFeature
from app.specification import project_from_specification


def _box(**overrides) -> SpecificationFeature:
    payload = {
        "id": "body", "label": "Body", "type": "box", "operation": "add", "status": "confirmed",
        "parameters": {"length": 80, "width": 50, "height": 30},
        "placement": {"plane": "XY", "origin": [0, 0, 0]},
    }
    payload.update(overrides)
    return SpecificationFeature.model_validate(payload)


class ShellDirectionTests(unittest.TestCase):
    def test_compiled_shell_expression_hollows_inward(self):
        shell = SpecificationFeature.model_validate({
            "id": "hollow", "label": "Hollow", "type": "shell", "operation": "modify", "target": "body",
            "status": "confirmed", "parameters": {"thickness": 3},
            "placement": {"reference": ">Z"},
        })
        draft = DraftSpecification(features=[_box(), shell])
        project = project_from_specification(draft)
        self.assertIn(".shell(-(3))", project.cad.source, project.cad.source)
        self.assertNotIn(".shell(3)", project.cad.source)

    def test_real_cadquery_build_keeps_the_outer_envelope(self):
        import cadquery as cq  # skip cleanly if the worker's CadQuery isn't on this interpreter
        draft = DraftSpecification(features=[_box(), SpecificationFeature.model_validate({
            "id": "hollow", "label": "Hollow", "type": "shell", "operation": "modify", "target": "body",
            "status": "confirmed", "parameters": {"thickness": 3}, "placement": {"reference": ">Z"},
        })])
        project = project_from_specification(draft)
        namespace = {"cq": cq, "PARAMETERS": {}}
        exec(compile(project.cad.source, "<compiled>", "exec"), namespace)  # noqa: S102
        bbox = namespace["result"].val().BoundingBox()
        self.assertAlmostEqual(bbox.xlen, 80, delta=0.01)
        self.assertAlmostEqual(bbox.ylen, 50, delta=0.01)
        self.assertAlmostEqual(bbox.zlen, 30, delta=0.01, msg="shelling must not grow the outer envelope")


class RibExtentTests(unittest.TestCase):
    def test_rib_extent_matches_its_compiled_box_shape(self):
        rib = SpecificationFeature.model_validate({
            "id": "rim", "label": "Rim", "type": "rib", "operation": "add", "target": "body", "status": "confirmed",
            "parameters": {"length": 50, "thickness": 5, "height": 3},
            "placement": {"origin": [40, 0, 30]},
        })
        extent = resolved_feature_extent(rib, {})
        self.assertIsNotNone(extent, "rib must now be lint-visible instead of silently unevaluated")
        self.assertEqual(extent.minimum, (40.0, 0.0, 30.0))
        self.assertEqual(extent.maximum, (90.0, 5.0, 33.0))

    def test_rib_missing_a_dimension_is_unevaluated_not_silently_dropped(self):
        from app.draft_lint import lint_draft
        rib = SpecificationFeature.model_validate({
            "id": "rim", "label": "Rim", "type": "rib", "operation": "add", "target": "body", "status": "confirmed",
            "parameters": {"length": 50, "thickness": "missing_dim", "height": 3},
            "placement": {"origin": [40, 0, 30]},
        })
        draft = DraftSpecification(features=[_box(), rib])
        result = lint_draft(draft)
        self.assertIn("rim", result.unevaluated_feature_ids)


if __name__ == "__main__":
    unittest.main()
