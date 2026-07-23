"""Test isolation for the multi-tenant app (SPEC13).

Point the accounts DB at a throwaway file, set a test JWT secret, and reset the
in-memory session registry / rate limiter / DB rows before each test.
"""

import os
import tempfile
from pathlib import Path

# Must be set before app.main / app.db are imported.
os.environ["EASYCAD_DB_PATH"] = str(Path(tempfile.mkdtemp()) / "easycad-test.db")
os.environ.setdefault("JWT_SECRET", "test-secret")

import pytest


@pytest.fixture(autouse=True)
def _isolate():
    import app.main as m
    from app import db

    m.registry.clear()
    m.limiter.reset()
    db._reset_for_tests()
    yield
