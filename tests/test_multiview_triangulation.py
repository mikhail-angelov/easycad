"""Unit tests for the app-integrated multi-view triangulation gate (app/multiview_triangulation.py).

The real-provider round trip (actual per-panel LLM calls, actual layout classification) is
covered by the opt-in E2E in tests/test_e2e_multiview_triangulation.py; these tests mock the
one network dependency (_chat_json) to cover the parsing/gating logic deterministically, plus
the parts that never touch the network at all: OCR resilience and the reconciliation
arithmetic.
"""

import asyncio
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from PIL import Image

from app.multiview_triangulation import (
    build_grounding_instructions,
    detect_panel_layout,
    format_grounding_instructions,
    ocr_panel_dimensions,
    reconcile,
)

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"


class PanelLayoutDetectionTests(unittest.TestCase):
    def test_multi_panel_response_is_cropped_into_the_reported_boxes(self):
        image = Image.new("RGB", (1000, 800), color="white")
        fake_response = {
            "multi_panel": True,
            "panels": [
                {"label": "front", "left": 0.0, "top": 0.0, "right": 0.5, "bottom": 0.5},
                {"label": "top", "left": 0.5, "top": 0.0, "right": 1.0, "bottom": 0.5},
            ],
        }
        with patch("app.multiview_triangulation._chat_json", new=AsyncMock(return_value=fake_response)):
            layout = asyncio.run(detect_panel_layout(image, api_key="fake-key"))
        self.assertIsNotNone(layout)
        self.assertEqual(set(layout.panels), {"front", "top"})
        self.assertEqual(layout.panels["front"].size, (500, 400))

    def test_multi_panel_false_returns_none(self):
        with patch("app.multiview_triangulation._chat_json", new=AsyncMock(return_value={"multi_panel": False})):
            layout = asyncio.run(detect_panel_layout(Image.new("RGB", (500, 500)), api_key="fake-key"))
        self.assertIsNone(layout)

    def test_a_single_reported_panel_is_not_enough_to_count_as_multi_panel(self):
        # Guards against a model that says multi_panel=true but only describes one region --
        # there is nothing to cross-view-verify with fewer than two panels.
        fake_response = {"multi_panel": True, "panels": [{"label": "front", "left": 0, "top": 0, "right": 1, "bottom": 1}]}
        with patch("app.multiview_triangulation._chat_json", new=AsyncMock(return_value=fake_response)):
            layout = asyncio.run(detect_panel_layout(Image.new("RGB", (500, 500)), api_key="fake-key"))
        self.assertIsNone(layout)

    def test_a_degenerate_zero_size_box_is_dropped_without_crashing(self):
        fake_response = {
            "multi_panel": True,
            "panels": [
                {"label": "front", "left": 0.0, "top": 0.0, "right": 0.5, "bottom": 0.5},
                {"label": "bad", "left": 0.5, "top": 0.5, "right": 0.5, "bottom": 0.5},
                {"label": "top", "left": 0.5, "top": 0.0, "right": 1.0, "bottom": 0.5},
            ],
        }
        with patch("app.multiview_triangulation._chat_json", new=AsyncMock(return_value=fake_response)):
            layout = asyncio.run(detect_panel_layout(Image.new("RGB", (1000, 1000)), api_key="fake-key"))
        self.assertIsNotNone(layout)
        self.assertEqual(set(layout.panels), {"front", "top"})


class OcrResilienceTests(unittest.TestCase):
    def test_ocr_never_raises_even_on_a_blank_image(self):
        readings = ocr_panel_dimensions(Image.new("L", (200, 200), color=255))
        self.assertIsInstance(readings, list)


class ReconcileTests(unittest.TestCase):
    def test_two_independent_sources_agreeing_counts_as_verified(self):
        panel_results = {
            "front_view": {"dimensions": [{"value": 30, "measures": "overall height", "confidence": 0.95}]},
            "side_reference_view": {"dimensions": [{"value": 30, "measures": "height", "confidence": 0.9}]},
        }
        result = reconcile(panel_results, ocr_results={})
        self.assertEqual(len(result["verified"]), 1)
        self.assertEqual(result["verified"][0]["value"], 30.0)
        self.assertEqual(set(result["verified"][0]["confirmed_by"]), {"front_view/llm", "side_reference_view/llm"})

    def test_llm_and_ocr_on_the_same_panel_is_a_cross_method_confirmation(self):
        panel_results = {"front_view": {"dimensions": [{"value": 30, "measures": "overall height", "confidence": 0.95}]}}
        ocr_results = {"front_view": [{"value": 30.0, "measures": "(ocr digit, no semantic label)", "confidence": 0.94}]}
        result = reconcile(panel_results, ocr_results)
        self.assertEqual(len(result["verified"]), 1)
        self.assertEqual(set(result["verified"][0]["confirmed_by"]), {"front_view/llm", "front_view/ocr"})

    def test_a_single_unconfirmed_reading_is_not_verified(self):
        panel_results = {"front_view": {"dimensions": [{"value": 30, "measures": "overall height", "confidence": 0.95}]}}
        result = reconcile(panel_results, ocr_results={})
        self.assertEqual(result["verified"], [])
        self.assertEqual(len(result["single_source_only"]), 1)

    def test_format_grounding_instructions_is_empty_when_nothing_is_verified(self):
        self.assertEqual(format_grounding_instructions({"verified": [], "single_source_only": []}), "")


class BuildGroundingInstructionsResilienceTests(unittest.TestCase):
    def test_returns_empty_string_without_an_api_key(self):
        result = asyncio.run(build_grounding_instructions(b"not a real image", api_key=""))
        self.assertEqual(result, "")

    def test_returns_empty_string_for_unparseable_image_bytes_instead_of_raising(self):
        result = asyncio.run(build_grounding_instructions(b"not a real image", api_key="fake-key"))
        self.assertEqual(result, "")

    def test_returns_empty_string_when_the_classifier_says_single_panel(self):
        with (FIXTURES / "3.png").open("rb") as handle:
            image_bytes = handle.read()
        with patch("app.multiview_triangulation._chat_json", new=AsyncMock(return_value={"multi_panel": False})):
            result = asyncio.run(build_grounding_instructions(image_bytes, api_key="fake-key"))
        self.assertEqual(result, "")

    def test_survives_the_classifier_itself_raising(self):
        with (FIXTURES / "3.png").open("rb") as handle:
            image_bytes = handle.read()
        with patch("app.multiview_triangulation._chat_json", new=AsyncMock(side_effect=RuntimeError("provider down"))):
            result = asyncio.run(build_grounding_instructions(image_bytes, api_key="fake-key"))
        self.assertEqual(result, "")


if __name__ == "__main__":
    unittest.main()
