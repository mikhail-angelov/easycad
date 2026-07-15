from __future__ import annotations

import unittest

from fastapi.testclient import TestClient

from app.main import app
from tests.test_specification import complete_specification


class SpecificationE2E(unittest.TestCase):
    def test_confirmed_specification_builds_and_exports_stl(self):
        client = TestClient(app)
        draft = complete_specification().model_dump(mode="json")
        validation = client.post("/api/specifications/validate", json={"specification": draft})
        self.assertEqual(validation.status_code, 200, validation.text)
        built = client.post("/api/specifications/build", json=validation.json()["specification"])
        self.assertEqual(built.status_code, 200, built.text)
        self.assertEqual(built.json()["status"], "success")


if __name__ == "__main__":
    unittest.main()
