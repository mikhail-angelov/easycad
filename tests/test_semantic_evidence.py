from __future__ import annotations

import unittest

from app.semantic_evidence import SEMANTIC_EVIDENCE, lint_semantic_evidence


class SemanticEvidenceTests(unittest.TestCase):
    def test_every_compiler_kind_has_paired_semantic_evidence(self):
        lint_semantic_evidence()

    def test_lint_rejects_missing_or_incomplete_evidence(self):
        manifest = dict(SEMANTIC_EVIDENCE)
        manifest.pop("fillet")
        manifest["box"] = {"positive": "test", "negative": "", "invariant": ""}

        with self.assertRaisesRegex(ValueError, "missing=.*fillet.*incomplete=.*box"):
            lint_semantic_evidence(manifest)
