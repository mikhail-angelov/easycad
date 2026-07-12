from __future__ import annotations

import unittest

from app.ai_generation import project_from_plan
from app.source_images import get_source_image, store_source_image


class SourceImageTests(unittest.TestCase):
    def test_store_returns_stable_memory_reference(self):
        first_ref, first_digest = store_source_image(b"drawing-bytes")
        second_ref, second_digest = store_source_image(b"drawing-bytes")

        self.assertEqual(first_ref, second_ref)
        self.assertEqual(first_digest, second_digest)
        self.assertEqual(get_source_image(first_ref), b"drawing-bytes")
        self.assertTrue(first_ref.startswith("memory://sha256/"))

    def test_project_keeps_source_reference_without_embedding_image_by_default(self):
        project = project_from_plan(
            {
                "title": "Plate",
                "parameters": [{"id": "length", "label": "Length", "value": 10}],
                "feature_graph": {
                    "operations": [
                        {
                            "id": "base",
                            "type": "box",
                            "operation": "add",
                            "parameters": {"length": "length", "width": 5, "height": 2},
                            "status": "implemented",
                            "implementation": "base",
                        }
                    ]
                },
            },
            {"title": "Plate", "units": "mm"},
            {"filename": "plate.png", "mime_type": "image/png", "width": 640, "height": 480},
            b"source-image",
        )

        self.assertEqual(project.source.width, 640)
        self.assertEqual(project.source.height, 480)
        self.assertIsNotNone(project.source.image_ref)
        self.assertIsNotNone(project.source.image_sha256)
        self.assertIsNone(project.source.image_data)
        self.assertEqual(get_source_image(project.source.image_ref), b"source-image")


if __name__ == "__main__":
    unittest.main()
