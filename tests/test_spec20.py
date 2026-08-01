"""SPEC20: base-code invariant and LLM-only geometry reconciliation."""

from fastapi.testclient import TestClient

import app.main as m
from app.cadquery_exec import append_geometry_block, strip_geometry_block
from app.main import app
from app.refiner import TriageResult
from app.store import SessionStore


BOX = "import cadquery as cq\nresult = cq.Workplane('XY').box(10, 10, 10)\n"
INFO = "# ── Geometry info (auto-generated, do not edit) ──\n# Size: 10.0 x 10.0 x 10.0 mm\n# Topology: 1 solid(s), 6 faces, 12 edges"


def test_store_writes_and_legacy_imports_are_always_base_code():
    with_block = append_geometry_block(BOX, INFO)
    store = SessionStore()
    step = store.add(kind="manual", code=with_block, success=True, geometry_info=INFO)
    assert step.code == strip_geometry_block(BOX)
    assert "Geometry info" not in step.to_public()["code"]

    store.load_project({"steps": [{"id": 4, "code": with_block, "geometry_info": INFO}]})
    assert store.current().code == strip_geometry_block(BOX)
    assert "Geometry info" not in store.to_project()["steps"][0]["code"]


def test_geometry_is_reattached_only_for_llm_calls(monkeypatch):
    seen: dict[str, str] = {}

    def fake_generate(base_code, *args, **kwargs):
        seen["generate"] = base_code
        return BOX

    monkeypatch.setattr(m, "generate_code", fake_generate)
    client = TestClient(app)
    # Bootstrap creates an initial step with measured geometry; the first request
    # must still replace that placeholder while the LLM receives its context.
    initial = client.get("/api/session").json()["current"]
    assert "Geometry info" not in initial["code"]
    response = client.post(
        "/api/chat", json={"prompt": "make a box", "auto_refine": False}, headers={"x-real-ip": "20.0.0.1"}
    )
    assert response.status_code == 200, response.text
    assert "Geometry info" in seen["generate"]
    assert "Geometry info" not in response.json()["step"]["code"]


def test_first_variation_replaces_initial_and_returns_base_code(monkeypatch):
    seen: dict[str, object] = {}

    def fake_generate(base_code, *args, **kwargs):
        seen["code"] = base_code
        seen["replace_initial"] = kwargs.get("replace_initial")
        return append_geometry_block(BOX, INFO)

    monkeypatch.setattr(m, "generate_code", fake_generate)
    client = TestClient(app)
    response = client.post(
        "/api/variations",
        json={"prompt": "make alternatives", "auto_refine": False, "count": 1},
        headers={"x-real-ip": "20.0.0.3"},
    )
    assert response.status_code == 200, response.text
    candidate = response.json()["candidates"][0]
    assert seen["replace_initial"] is True
    assert "Geometry info" in seen["code"]
    assert "Geometry info" not in candidate["code"]
    committed = client.post(
        "/api/commit", json={"code": candidate["code"], "original_prompt": "make alternatives"}
    ).json()["step"]
    assert "Geometry info" not in committed["code"]


def test_refine_is_null_safe_and_receives_geometry_when_available(monkeypatch):
    seen: dict[str, str] = {}

    def fake_triage(prompt, code, *args, **kwargs):
        seen["code"] = code
        return TriageResult("ready")

    monkeypatch.setattr(m, "triage", fake_triage)
    client = TestClient(app)
    r = client.post("/api/refine", json={"prompt": "make it taller", "current_code": BOX}, headers={"x-real-ip": "20.0.0.2"})
    assert r.status_code == 200, r.text
    assert "Geometry info" in seen["code"]

    store = SessionStore()
    store.add(kind="initial", code=BOX, success=False, geometry_info=None)
    assert m._with_geometry(store, BOX) == BOX


def test_execute_endpoint_returns_base_code():
    client = TestClient(app)
    result = client.post("/api/execute", json={"code": append_geometry_block(BOX, INFO)}).json()
    assert result["success"]
    assert result["code"] == strip_geometry_block(BOX)
    assert "code_with_geometry" not in result


def test_base_code_survives_manual_revert_reload_export_and_import():
    client = TestClient(app)
    with_block = append_geometry_block(BOX, INFO)
    created = client.post("/api/execute-manual", json={"code": with_block}).json()["step"]
    assert "Geometry info" not in created["code"]

    step_id = created["id"]
    for payload in (
        client.get("/api/session").json()["current"],
        client.get("/api/steps").json()[-1],
        client.post(f"/api/steps/{step_id}/revert").json()["current"],
    ):
        assert "Geometry info" not in payload["code"]

    assert "Geometry info" not in client.get(f"/api/export/{step_id}/source").text
    project = client.get("/api/project/export").json()
    assert "Geometry info" not in project["steps"][-1]["code"]
    imported = client.post("/api/project/import", json=project).json()
    assert "Geometry info" not in imported["current"]["code"]
