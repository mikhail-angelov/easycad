"""Unit tests for the read-only feature roster (SPEC 9 Part A)."""

import unittest

from app.feature_roster import feature_roster
from app.minimal_model import _omit
from app.models import DraftSpecification, SpecificationFeature


def _feature(**overrides) -> SpecificationFeature:
    payload = {
        "id": "body", "label": "Body", "type": "box", "operation": "add",
        "status": "confirmed",
        "parameters": {"length": 10, "width": 20, "height": 5},
        "placement": {"plane": "XY", "origin": [0, 0, 0]},
    }
    payload.update(overrides)
    return SpecificationFeature.model_validate(payload)


class OmitTests(unittest.TestCase):
    def test_omit_sets_reason_and_leaves_label_untouched(self):
        feature = _feature(label="Rib")
        _omit(feature, "no deterministic geometry")
        self.assertEqual(feature.status, "unsupported")
        self.assertEqual(feature.label, "Rib")
        self.assertEqual(feature.omission_reason, "no deterministic geometry")


class FeatureRosterTests(unittest.TestCase):
    def test_confirmed_feature_gets_resolved_extent(self):
        draft = DraftSpecification(features=[_feature()])
        roster = feature_roster(draft, {})
        self.assertEqual(len(roster), 1)
        entry = roster[0]
        self.assertEqual(entry.id, "body")
        self.assertEqual(entry.label, "Body")
        self.assertEqual(entry.status, "confirmed")
        self.assertIsNone(entry.omission_reason)
        self.assertEqual(entry.extent, {"minimum": [0.0, 0.0, 0.0], "maximum": [10.0, 20.0, 5.0]})

    def test_unsupported_feature_reports_reason(self):
        feature = _feature(id="rib", label="Rib", type="rib")
        _omit(feature, "insufficient reliable dimensions")
        draft = DraftSpecification(features=[feature])
        entry = feature_roster(draft, {})[0]
        self.assertEqual(entry.status, "unsupported")
        self.assertEqual(entry.omission_reason, "insufficient reliable dimensions")
        self.assertEqual(entry.label, "Rib")

    def test_non_computable_type_has_no_extent_even_when_confirmed(self):
        feature = _feature(
            id="engraving", label="Engraving", type="text",
            parameters={"content": "hi", "size": 10, "depth": 1},
        )
        draft = DraftSpecification(features=[feature])
        self.assertIsNone(feature_roster(draft, {})[0].extent)

    def test_roster_preserves_draft_feature_order(self):
        draft = DraftSpecification(features=[
            _feature(id="a", label="A"),
            _feature(id="b", label="B"),
        ])
        roster = feature_roster(draft, {})
        self.assertEqual([entry.id for entry in roster], ["a", "b"])


if __name__ == "__main__":
    unittest.main()
