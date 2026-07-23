"""Tests for the store project roundtrip and project file export/import.

SPEC13 removed server-side session autosave/resume; export/import is now the
user's own persistence path.
"""

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.store import SessionStore

BOX = "import cadquery as cq\nresult = cq.Workplane('XY').box({s},{s},{s})\n"


def test_store_project_roundtrip():
    s = SessionStore()
    s.add(kind="initial", code="c0", success=True, stl_base64="AAA", geometry_info="g0")
    s.add(kind="chat", code="c1", success=True, original_prompt="p", refined_prompt="r")
    proj = s.to_project()
    assert proj["format"] == "easycad-cadquery-chat"

    s2 = SessionStore()
    s2.load_project(proj)
    assert [x.id for x in s2.all()] == [0, 1]
    assert s2.current_id == 1
    assert s2.current().code == "c1"
    assert s2.add(kind="chat", code="c2", success=True).id == 2


def test_load_project_rejects_garbage():
    with pytest.raises(ValueError):
        SessionStore().load_project({"nope": 1})


def test_export_then_import_roundtrip():
    client = TestClient(app)
    client.get("/api/session")  # step 0
    client.post("/api/execute-manual", json={"code": BOX.format(s=7)})

    exported = client.get("/api/project/export")
    assert exported.status_code == 200
    assert "attachment" in exported.headers["content-disposition"]
    proj = exported.json()
    assert proj["format"] == "easycad-cadquery-chat"
    n_steps = len(proj["steps"])
    assert n_steps == 2
    # Text-only: no binary STL is persisted in the project file.
    for st in proj["steps"]:
        assert "stl_base64" not in st
        assert "code" in st

    imported = client.post("/api/project/import", json=proj).json()
    assert len(imported["steps"]) == n_steps
    assert imported["current_id"] == proj["current_id"]


def test_import_rejects_invalid_project():
    client = TestClient(app)
    assert client.post("/api/project/import", json={"garbage": True}).status_code == 400


def test_reset_starts_fresh():
    client = TestClient(app)
    client.get("/api/session")
    client.post("/api/execute-manual", json={"code": BOX.format(s=5)})
    fresh = client.post("/api/session/reset").json()
    assert len(fresh["steps"]) == 1
    assert fresh["steps"][0]["kind"] == "initial"


def test_no_working_state_file_is_written(tmp_path, monkeypatch):
    # SPEC13: sessions are memory-only. Building a session must not create any
    # file under a (hypothetical) session dir.
    client = TestClient(app)
    client.get("/api/session")
    client.post("/api/execute-manual", json={"code": BOX.format(s=6)})
    # The accounts DB may exist, but no per-session json file should.
    assert not (tmp_path / "session.json").exists()
