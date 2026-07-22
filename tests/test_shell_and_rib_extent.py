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

    def test_shell_with_no_face_reference_actually_hollows_the_part(self):
        """A second, real bug found investigating a user question ('why can't the program build
        a hollow box with 2mm walls'): with no explicit face reference (the common case -- a
        user asking for "wall thickness" rarely names one specific open face), the compiler
        called `.faces()` with no selector at all. CadQuery treats an empty `.faces()` selection
        as "every face is an opening", which is a no-op -- confirmed with the real worker, the
        resulting volume was identical to the solid box's volume, not a thin shell. The bbox-only
        assertion above could not have caught this; only real volume can. `.shell()` must be
        called directly on the solid when there is no face reference."""
        import cadquery as cq  # skip cleanly if the worker's CadQuery isn't on this interpreter
        draft = DraftSpecification(features=[_box(), SpecificationFeature.model_validate({
            "id": "hollow", "label": "Hollow", "type": "shell", "operation": "modify", "target": "body",
            "status": "confirmed", "parameters": {"thickness": 2},
        })])
        project = project_from_specification(draft)
        self.assertNotIn(".faces()", project.cad.source, project.cad.source)
        namespace = {"cq": cq, "PARAMETERS": {}}
        exec(compile(project.cad.source, "<compiled>", "exec"), namespace)  # noqa: S102
        solid = namespace["result"].val()
        bbox = solid.BoundingBox()
        self.assertAlmostEqual(bbox.xlen, 80, delta=0.01)
        self.assertAlmostEqual(bbox.ylen, 50, delta=0.01)
        self.assertAlmostEqual(bbox.zlen, 30, delta=0.01)
        solid_volume = 80 * 50 * 30
        self.assertLess(
            solid.Volume(), solid_volume * 0.5,
            f"a 2mm-wall shell of an 80x50x30 box must remove most of the material; "
            f"got {solid.Volume()} out of {solid_volume} (a no-op shell leaves it unchanged)",
        )


class SelectEverythingWordTests(unittest.TestCase):
    """A third real bug found the same session, investigating why a plain-language "box with
    rounded edges and 2mm walls" request failed: the planner wrote placement.reference="all"
    for "fillet every edge", which the compiler passed straight through as a literal CadQuery
    selector string. CadQuery's own selector grammar has no "all" keyword and rejected it
    ("Expected 'not' operations, found 'all'") -- the correct way to select every edge/face is
    to omit the selector entirely. Confirmed same-session with the real worker."""

    def test_fillet_with_reference_all_compiles_as_no_selector(self):
        fillet = SpecificationFeature.model_validate({
            "id": "rounded", "label": "Rounded", "type": "fillet", "operation": "modify", "target": "body",
            "status": "confirmed", "parameters": {"radius": 5}, "placement": {"reference": "all"},
        })
        draft = DraftSpecification(features=[_box(), fillet])
        project = project_from_specification(draft)
        self.assertIn(".edges().fillet(5)", project.cad.source, project.cad.source)
        self.assertNotIn('"all"', project.cad.source)

    def test_shell_with_reference_every_compiles_as_no_selector(self):
        shell = SpecificationFeature.model_validate({
            "id": "hollow", "label": "Hollow", "type": "shell", "operation": "modify", "target": "body",
            "status": "confirmed", "parameters": {"thickness": 2}, "placement": {"reference": "every"},
        })
        draft = DraftSpecification(features=[_box(), shell])
        project = project_from_specification(draft)
        self.assertIn(".shell(-(2))", project.cad.source, project.cad.source)
        self.assertNotIn(".faces(", project.cad.source)

    def test_a_real_edge_selector_is_left_untouched(self):
        fillet = SpecificationFeature.model_validate({
            "id": "rounded", "label": "Rounded", "type": "fillet", "operation": "modify", "target": "body",
            "status": "confirmed", "parameters": {"radius": 5}, "placement": {"reference": "|Z"},
        })
        draft = DraftSpecification(features=[_box(), fillet])
        project = project_from_specification(draft)
        self.assertIn('.edges("|Z").fillet(5)', project.cad.source, project.cad.source)


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
