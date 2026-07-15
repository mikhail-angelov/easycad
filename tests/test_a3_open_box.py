from __future__ import annotations

import unittest

from scripts.generate_a3_open_box import build_model


class A3OpenBoxTests(unittest.TestCase):
    def test_confirmed_dimensions_and_single_printable_solid(self):
        shape = build_model()
        bounding_box = shape.val().BoundingBox()

        self.assertAlmostEqual(bounding_box.xlen, 80.0, places=3)
        self.assertAlmostEqual(bounding_box.ylen, 50.0, places=3)
        self.assertAlmostEqual(bounding_box.zlen, 30.0, places=3)
        self.assertEqual(shape.solids().size(), 1)


if __name__ == "__main__":
    unittest.main()
