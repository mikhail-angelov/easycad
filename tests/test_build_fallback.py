"""No user-visible build errors: a real worker failure must recover via the fallback, not 422."""

import unittest
from unittest.mock import patch

from fastapi import HTTPException

from app.main import _build_or_fallback
from app.minimal_model import fallback_draft
from app.models import DraftSpecification, SpecificationFeature
from app.runner import RunnerError


def _confirmed_box(feature_id: str = "body") -> SpecificationFeature:
    return SpecificationFeature.model_validate({
        "id": feature_id, "label": "Body", "type": "box", "operation": "add",
        "status": "confirmed",
        "parameters": {"length": 10, "width": 10, "height": 10},
        "placement": {"plane": "XY", "origin": [0, 0, 0]},
    })


def _fake_build_result() -> dict:
    return {
        "artifact_bytes": b"solid stl bytes",
        "duration_ms": 12,
        "bounding_box": {"x": 10, "y": 10, "z": 10},
        "volume_mm3": 1000.0,
        "solid_count": 1,
        "feature_measurements": {},
    }


class BuildOrFallbackTests(unittest.TestCase):
    def test_recovers_from_a_real_worker_failure_without_raising(self):
        draft = DraftSpecification(features=[_confirmed_box("fragile_fillet")])
        with patch(
            "app.main.run_project",
            side_effect=[RunnerError("compile", "CadQuery kernel rejected this fillet"), _fake_build_result()],
        ) as mocked:
            _project, result, final_draft = _build_or_fallback(draft)
        self.assertEqual(mocked.call_count, 2)
        self.assertEqual(result["artifact_bytes"], b"solid stl bytes")
        self.assertTrue(any(item.id == "minimal_body" for item in final_draft.features))
        fragile = next(item for item in final_draft.features if item.id == "fragile_fillet")
        self.assertEqual(fragile.status, "unsupported")
        self.assertIsNotNone(fragile.omission_reason)

    def test_succeeds_on_first_try_without_any_fallback(self):
        draft = DraftSpecification(features=[_confirmed_box()])
        with patch("app.main.run_project", side_effect=[_fake_build_result()]) as mocked:
            _project, _result, final_draft = _build_or_fallback(draft)
        self.assertEqual(mocked.call_count, 1)
        self.assertEqual([item.id for item in final_draft.features], ["body"])

    def test_raises_only_if_even_the_fallback_box_fails(self):
        draft = DraftSpecification(features=[_confirmed_box()])
        always_fails = RunnerError("compile", "worker is down")
        with patch("app.main.run_project", side_effect=[always_fails, always_fails]):
            with self.assertRaises(HTTPException):
                _build_or_fallback(draft)


class FallbackDraftReentrancyTests(unittest.TestCase):
    def test_replaces_a_stale_unsupported_minimal_body_instead_of_skipping_it(self):
        # Reproduces a real reachable state: an earlier pass (or a seeded replan echoing
        # a prior round's fallback) already omitted a feature literally named
        # "minimal_body". fallback_draft must not treat its mere presence as "already
        # have a safe fallback" — it must still end up with exactly one *confirmed* one.
        stale = SpecificationFeature.model_validate({
            "id": "minimal_body", "label": "Minimal fallback body", "type": "box", "operation": "add",
            "status": "unsupported", "omission_reason": "omitted by an earlier, unrelated pass",
            "parameters": {"length": 100, "width": 100, "height": 10},
            "placement": {"plane": "XY", "origin": [0, 0, 0]},
        })
        draft = DraftSpecification(features=[stale, _confirmed_box("other")])
        result = fallback_draft(draft)
        minimal_bodies = [item for item in result.features if item.id == "minimal_body"]
        self.assertEqual(len(minimal_bodies), 1, "must not end up with zero or duplicate minimal_body ids")
        self.assertEqual(minimal_bodies[0].status, "confirmed")
        self.assertTrue(
            any(item.status == "confirmed" for item in result.features),
            "the whole guarantee is that at least one confirmed feature always survives",
        )


if __name__ == "__main__":
    unittest.main()
