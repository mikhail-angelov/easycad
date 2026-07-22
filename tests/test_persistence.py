"""Tests for session autosave/resume and project file export/import."""

import json

import pytest
from fastapi.testclient import TestClient

import app.main as m
from app.main import app, store
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
    # New ids must continue after the max imported id.
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

    store.reset()
    imported = client.post("/api/project/import", json=proj).json()
    assert len(imported["steps"]) == n_steps
    assert imported["current_id"] == proj["current_id"]


def test_import_rejects_invalid_project():
    client = TestClient(app)
    assert client.post("/api/project/import", json={"garbage": True}).status_code == 400


def test_autosave_and_resume():
    client = TestClient(app)
    client.get("/api/session")
    client.post("/api/execute-manual", json={"code": BOX.format(s=9)})

    assert m.AUTOSAVE.exists()
    saved = json.loads(m.AUTOSAVE.read_text())
    assert len(saved["steps"]) == 2

    # Simulate a server restart: clear memory, then a fresh request should
    # resume from disk rather than start a new single-step session.
    store.reset()
    resumed = client.get("/api/session").json()
    assert len(resumed["steps"]) == 2


def test_reset_starts_fresh_not_resume():
    client = TestClient(app)
    client.get("/api/session")
    client.post("/api/execute-manual", json={"code": BOX.format(s=5)})
    # Reset must NOT reload the autosaved 2-step session.
    fresh = client.post("/api/session/reset").json()
    assert len(fresh["steps"]) == 1
    assert fresh["steps"][0]["kind"] == "initial"
