from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


class StaticUIContractTests(unittest.TestCase):
    def test_feature_statuses_are_shown_and_reviewed_before_export(self):
        html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")

        self.assertIn("capability: ${escapeAttr(capability)}", html)
        self.assertIn("coverage: ${escapeAttr(coverage)}", html)
        self.assertIn("item.capability_status !== 'supported'", html)
        self.assertIn("item.status !== 'implemented'", html)
        self.assertIn("window.confirm(`Review feature status before export:", html)
        self.assertNotIn('id="downloadPy"', html)
        self.assertNotIn('data-tab="source"', html)


if __name__ == "__main__":
    unittest.main()
