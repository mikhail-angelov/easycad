from __future__ import annotations

import unittest
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw

from app.runner import run_project
from app.silhouette import compare_silhouettes, silhouette_mask, silhouette_metrics
from tests.generate_silhouette_goldens import bracket_project, rib_project


ROOT = Path(__file__).resolve().parent.parent
GOLDENS = ROOT / "tests" / "fixtures" / "silhouettes"


def drawing(*, notch: bool = False, rotated: bool = False) -> Image.Image:
    image = Image.new("RGB", (100, 100), "white")
    draw = ImageDraw.Draw(image)
    if rotated:
        draw.rectangle((35, 10, 65, 90), fill="black")
    else:
        draw.rectangle((10, 35, 90, 65), fill="black")
    if notch:
        draw.rectangle((45, 35, 55, 45), fill="white")
    return image


class SilhouetteTests(unittest.TestCase):
    def render(self, project, view):
        result = run_project(project, {}, fmt="stl", render_views=True)
        return Image.open(BytesIO(result["render_artifacts"][view])).copy()

    def test_mask_reports_occupied_bounds_and_area(self):
        metrics = silhouette_metrics(silhouette_mask(drawing()))
        self.assertEqual(metrics.bounds, (10, 35, 91, 66))
        self.assertGreater(metrics.occupied_pixels, 0)

    def test_matching_calibrated_render_passes(self):
        comparison = compare_silhouettes(drawing(), drawing())
        self.assertEqual(comparison["symmetric_difference"], 0.0)
        self.assertEqual(comparison["expected_bounds"], comparison["actual_bounds"])

    def test_missing_feature_and_wrong_orientation_fail(self):
        expected = drawing(notch=True)
        missing_feature = compare_silhouettes(expected, drawing())
        wrong_orientation = compare_silhouettes(expected, drawing(rotated=True))

        self.assertGreater(missing_feature["symmetric_difference"], 0.005)
        self.assertGreater(wrong_orientation["symmetric_difference"], 0.2)

    def test_rejects_uncalibrated_image_sizes(self):
        with self.assertRaisesRegex(ValueError, "identical dimensions"):
            compare_silhouettes(drawing(), Image.new("RGB", (80, 80), "white"))

    def test_bracket_and_asymmetric_rib_match_golden_masks(self):
        cases = [
            ("bracket_fixture.front.png", bracket_project(), "front"),
            ("asymmetric_rib.isometric.png", rib_project(), "isometric"),
        ]
        for filename, project, view in cases:
            with self.subTest(filename=filename):
                expected = Image.open(GOLDENS / filename)
                comparison = compare_silhouettes(expected, self.render(project, view))
                self.assertLessEqual(comparison["symmetric_difference"], 0.01)

    def test_missing_bracket_groove_and_wrong_projection_fail_golden_comparison(self):
        expected = Image.open(GOLDENS / "bracket_fixture.front.png")
        missing_groove = bracket_project()
        groove = next(operation for operation in missing_groove.feature_graph.operations if operation.id == "top_groove")
        groove.placement.origin[2] = 100

        missing_comparison = compare_silhouettes(expected, self.render(missing_groove, "front"))
        orientation_comparison = compare_silhouettes(expected, self.render(bracket_project(), "right"))

        self.assertGreater(missing_comparison["symmetric_difference"], 0.001)
        self.assertGreater(orientation_comparison["symmetric_difference"], 0.02)
