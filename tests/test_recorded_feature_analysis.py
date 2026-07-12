from __future__ import annotations

import json
import unittest
from pathlib import Path

from app.ai_generation import normalize_drawing_analysis


ROOT = Path(__file__).resolve().parent.parent
RECORDED_DIR = ROOT / "tests" / "fixtures" / "llm_features"


class RecordedFeatureAnalysisTests(unittest.TestCase):
    def load(self, name: str):
        return json.loads((RECORDED_DIR / f"{name}.analysis.json").read_text(encoding="utf-8"))

    def test_repeated_cuts_recording_has_two_distinct_cut_features(self):
        analysis = self.load("repeated_cuts")
        cuts = [feature for feature in analysis["features"] if feature.get("operation_hint") == "cut"]

        self.assertGreaterEqual(len(cuts), 2)
        self.assertEqual(len({feature["id"] for feature in cuts}), len(cuts))

    def test_shell_recording_preserves_shell_ribs_and_perforation_pattern(self):
        analysis = self.load("shell_ribs_perforation")
        feature_text = json.dumps(analysis["features"], ensure_ascii=False).lower()

        self.assertTrue("shell" in feature_text or "pocket" in feature_text)
        self.assertIn("rib", feature_text)
        self.assertTrue("perfor" in feature_text or "hole_pattern" in feature_text or "repeated_pattern" in feature_text)
        self.assertTrue(any(feature.get("pattern", {}).get("count") == 5 for feature in analysis["features"]))

    def test_polar_recording_preserves_eight_hole_pattern(self):
        analysis = self.load("polar_perforation")
        patterns = [feature for feature in analysis["features"] if feature.get("operation_hint") == "pattern"]

        self.assertEqual(len(patterns), 1)
        self.assertEqual(patterns[0]["pattern"]["count"], 8)
        self.assertEqual(patterns[0]["pattern"]["axis"], "Z")

    def test_recessed_text_recording_preserves_printable_cut(self):
        analysis = self.load("recessed_text")
        text_features = [feature for feature in analysis["features"] if feature.get("type") == "text"]

        self.assertEqual(len(text_features), 1)
        self.assertEqual(text_features[0]["operation_hint"], "cut")
        self.assertIn("ПРОБА", json.dumps(text_features[0], ensure_ascii=False))

    def test_sweep_recording_preserves_path_profile_and_body(self):
        analysis = self.load("sweep")
        feature_text = json.dumps(analysis["features"], ensure_ascii=False).lower()

        self.assertIn("sweep path", feature_text)
        self.assertIn("circular profile", feature_text)
        self.assertIn("sweep", analysis["construction_strategy"].lower())

    def test_loft_recording_preserves_two_profiles_and_transition(self):
        analysis = self.load("loft")
        feature_text = json.dumps(analysis["features"], ensure_ascii=False).lower()

        self.assertIn("square profile", feature_text)
        self.assertIn("circle profile", feature_text)
        self.assertIn("loft", analysis["construction_strategy"].lower())

    def test_recordings_normalize_deterministically_and_contain_no_secret_fields(self):
        for path in sorted(RECORDED_DIR.glob("*.analysis.json")):
            with self.subTest(path=path.name):
                payload = json.loads(path.read_text(encoding="utf-8"))
                self.assertEqual(normalize_drawing_analysis(payload), normalize_drawing_analysis(payload))
                lowered = path.read_text(encoding="utf-8").lower()
                self.assertNotIn("authorization", lowered)
                self.assertNotIn("api_key", lowered)
                self.assertNotIn("bearer ", lowered)


if __name__ == "__main__":
    unittest.main()
