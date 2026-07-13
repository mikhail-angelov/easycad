from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
INDEX = ROOT / "static" / "index.html"


class FrontendMarkupTests(unittest.TestCase):
    def test_dom_helper_references_existing_elements(self):
        page = INDEX.read_text(encoding="utf-8")
        element_ids = set(re.findall(r'\bid=["\']([^"\']+)["\']', page))
        helper_references = set(re.findall(r"\$\('([^']+)'\)", page))

        self.assertSetEqual(helper_references - element_ids, set())


if __name__ == "__main__":
    unittest.main()
