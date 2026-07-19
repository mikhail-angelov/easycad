"""A placement-origin coordinate may be negative; a size parameter must stay positive."""

import unittest

from app.models import DraftSpecification, SpecificationDimension, SpecificationFeature
from app.specification import SpecificationValidationError, validate_specification


def _box(**overrides) -> SpecificationFeature:
    payload = {
        "id": "body", "label": "Body", "type": "box", "operation": "add", "status": "confirmed",
        "parameters": {"length": 10, "width": 10, "height": 10},
        "placement": {"plane": "XY", "origin": [0, 0, 0]},
    }
    payload.update(overrides)
    return SpecificationFeature.model_validate(payload)


def _dimension(**overrides) -> SpecificationDimension:
    payload = {"id": "d", "label": "D", "status": "confirmed", "critical": True}
    payload.update(overrides)
    return SpecificationDimension.model_validate(payload)


class OriginSignTests(unittest.TestCase):
    def test_negative_dimension_used_only_as_an_origin_coordinate_is_accepted(self):
        draft = DraftSpecification(
            dimensions=[_dimension(id="rim_x_pos", value=-40)],
            features=[_box(id="rim", placement={"plane": "XY", "origin": ["rim_x_pos", 0, 0]})],
        )
        values = validate_specification(draft)
        self.assertEqual(values["rim_x_pos"], -40.0)

    def test_negative_expression_derived_origin_coordinate_is_accepted(self):
        draft = DraftSpecification(
            dimensions=[_dimension(id="half_width", value=80), _dimension(id="rim_x_pos", expression="-half_width / 2")],
            features=[_box(id="rim", placement={"plane": "XY", "origin": ["rim_x_pos", 0, 0]})],
        )
        values = validate_specification(draft)
        self.assertEqual(values["rim_x_pos"], -40.0)

    def test_negative_size_parameter_is_still_rejected(self):
        draft = DraftSpecification(
            dimensions=[_dimension(id="bad_length", value=-10)],
            features=[_box(parameters={"length": "bad_length", "width": 10, "height": 10})],
        )
        with self.assertRaises(SpecificationValidationError) as ctx:
            validate_specification(draft)
        self.assertIn("bad_length", ctx.exception.field_ids)

    def test_dimension_used_as_both_a_size_and_an_origin_must_stay_positive(self):
        # An unusual case (the same id doing double duty), but the size use must win.
        draft = DraftSpecification(
            dimensions=[_dimension(id="shared", value=-5)],
            features=[_box(
                parameters={"length": "shared", "width": 10, "height": 10},
                placement={"plane": "XY", "origin": ["shared", 0, 0]},
            )],
        )
        with self.assertRaises(SpecificationValidationError):
            validate_specification(draft)


if __name__ == "__main__":
    unittest.main()
