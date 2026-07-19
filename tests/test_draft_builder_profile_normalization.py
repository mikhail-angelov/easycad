"""A real hex-bolt drawing exposed a gap: the planner sometimes sends `profile` as a shorthand
or JSON-encoded string instead of a nested object -- the exact habit `_normalize_placement`
already works around for `placement`, but with no equivalent normalization for `profile`. Every
attempt across a real 12-turn run failed the same way and the planner never recovered (it kept
renaming the feature id instead of fixing the payload shape), producing a "Box Approximation"
hex head instead of the true hexagonal prism the new `polygon` profile type supports.
"""

import unittest

from app.draft_builder import DraftBuilder


def _seeded_builder() -> DraftBuilder:
    builder = DraftBuilder({})
    builder.set_metadata({"title": "hex bolt", "units": "mm"})
    builder.add_dimension({"id": "head_af", "label": "across flats", "value": 27, "unit": "mm", "status": "confirmed"})
    builder.add_dimension({"id": "head_sides", "label": "sides", "value": 6, "unit": "mm", "status": "confirmed"})
    builder.add_dimension({"id": "head_t", "label": "head thickness", "value": 12, "unit": "mm", "status": "confirmed"})
    return builder


def _hex_head_payload(profile, placement) -> dict:
    return {
        "id": "hex_head_body", "label": "Hexagonal Head", "type": "extrude", "operation": "add",
        "target": None, "parameters": {"distance": "head_t"}, "profile": profile, "placement": placement,
        "status": "confirmed", "confidence": 1.0, "evidence": ["x"], "critical_fields": [],
        "alternatives": {}, "source_feature_ids": ["hex_head"],
    }


class ProfileShorthandStringTests(unittest.TestCase):
    """Reproduces the exact payload shapes a real provider sent for this feature, captured
    verbatim from logs/llm_responses.jsonl."""

    def test_colon_equals_shorthand_string_is_parsed(self):
        builder = _seeded_builder()
        result = builder.add_feature(_hex_head_payload(
            "polygon: sides=head_sides, across_flats=head_af",
            "plane: XY, origin: [0, 0, 0]",
        ))
        self.assertTrue(result.get("ok"), result)
        feature = builder.draft.features[0]
        self.assertEqual(feature.profile.type, "polygon")
        self.assertEqual(feature.profile.dimensions, {"sides": "head_sides", "across_flats": "head_af"})
        self.assertEqual(feature.placement.plane, "XY")
        self.assertEqual(feature.placement.origin, [0, 0, 0])

    def test_json_encoded_string_with_flattened_dimensions_is_repaired(self):
        builder = _seeded_builder()
        result = builder.add_feature(_hex_head_payload(
            '{"type": "polygon", "sides": 6, "across_flats": "head_af"}',
            '{"plane": "XY", "origin": [0, 0, 0]}',
        ))
        self.assertTrue(result.get("ok"), result)
        feature = builder.draft.features[0]
        self.assertEqual(feature.profile.type, "polygon")
        self.assertEqual(feature.profile.dimensions, {"sides": 6, "across_flats": "head_af"})

    def test_well_formed_nested_profile_is_unaffected(self):
        builder = _seeded_builder()
        result = builder.add_feature(_hex_head_payload(
            {"type": "polygon", "dimensions": {"sides": "head_sides", "across_flats": "head_af"}, "points": []},
            {"plane": "XY", "origin": [0, 0, 0]},
        ))
        self.assertTrue(result.get("ok"), result)
        feature = builder.draft.features[0]
        self.assertEqual(feature.profile.dimensions, {"sides": "head_sides", "across_flats": "head_af"})

    def test_flattened_dict_profile_without_string_encoding_is_also_repaired(self):
        builder = _seeded_builder()
        result = builder.add_feature(_hex_head_payload(
            {"type": "polygon", "sides": 6, "across_flats": "head_af"},
            {"plane": "XY", "origin": [0, 0, 0]},
        ))
        self.assertTrue(result.get("ok"), result)
        feature = builder.draft.features[0]
        self.assertEqual(feature.profile.dimensions, {"sides": 6, "across_flats": "head_af"})

    def test_placement_shorthand_with_a_plane_label_prefix_is_parsed(self):
        builder = _seeded_builder()
        result = builder.add_feature(_hex_head_payload(
            {"type": "polygon", "dimensions": {"sides": 6, "across_flats": "head_af"}, "points": []},
            "plane: XY, origin: [0, 0, 0]",
        ))
        self.assertTrue(result.get("ok"), result)
        feature = builder.draft.features[0]
        self.assertEqual(feature.placement.plane, "XY")
        self.assertEqual(feature.placement.origin, [0, 0, 0])

    def test_a_vector_direction_does_not_block_an_otherwise_valid_feature(self):
        # Real payload captured verbatim from a live run: `direction` is schema'd as a short
        # axis string but a provider sent a raw vector; `direction` is not read anywhere
        # downstream, so it should be dropped rather than fail the whole feature.
        builder = _seeded_builder()
        result = builder.add_feature(_hex_head_payload(
            {"type": "polygon", "dimensions": {"sides": 6, "across_flats": "head_af"}, "points": []},
            '{"plane": "YZ", "origin": [0, 0, 0], "direction": [1, 0, 0]}',
        ))
        self.assertTrue(result.get("ok"), result)
        feature = builder.draft.features[0]
        self.assertIsNone(feature.placement.direction)
        self.assertEqual(feature.placement.plane, "YZ")


if __name__ == "__main__":
    unittest.main()
