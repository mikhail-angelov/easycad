"""Test isolation: redirect the session autosave to a throwaway temp file so
tests never touch the real ~/.easycad, and reset state before each test.
"""

import os
import tempfile
from pathlib import Path

# Must be set before app.main is imported (conftest loads before test modules).
os.environ["EASYCAD_SESSION_FILE"] = str(Path(tempfile.mkdtemp()) / "session.json")

import pytest


@pytest.fixture(autouse=True)
def _isolate_session():
    import app.main as m

    m.store.reset()
    if m.AUTOSAVE.exists():
        m.AUTOSAVE.unlink()
    yield
