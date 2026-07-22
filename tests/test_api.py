"""API wiring tests using FastAPI TestClient.

Covers the non-LLM paths (session bootstrap, execute, manual step, revert,
export). The /api/chat path needs a live provider and is not exercised here.
Requires cadquery (run with the .venv-poc interpreter).
"""

import base64

from fastapi.testclient import TestClient

from app.main import app, store


def setup_function():
    store.reset()


def test_session_bootstrap_creates_initial_step():
    client = TestClient(app)
    data = client.get("/api/session").json()
    assert data["current_id"] == 0
    assert len(data["steps"]) == 1
    assert data["steps"][0]["kind"] == "initial"
    assert data["current"]["success"] is True
    assert "deepseek" in data["providers"]


def test_execute_endpoint_is_stateless():
    client = TestClient(app)
    body = {"code": "import cadquery as cq\nresult = cq.Workplane('XY').box(10, 10, 10)\n"}
    res = client.post("/api/execute", json=body).json()
    assert res["success"]
    assert res["stl_base64"]
    assert "Size: 10.0 x 10.0 x 10.0 mm" in res["geometry_info"]
    # /api/execute is stateless — it must not record a step.
    assert store.all() == []


def test_execute_manual_creates_step_and_export_works():
    client = TestClient(app)
    client.get("/api/session")  # step 0
    body = {"code": "import cadquery as cq\nresult = cq.Workplane('XY').box(20, 20, 20)\n"}
    out = client.post("/api/execute-manual", json=body).json()
    assert out["step"]["kind"] == "manual"
    assert out["step"]["success"]
    assert out["session"]["current_id"] == out["step"]["id"]

    step_id = out["step"]["id"]
    resp = client.get(f"/api/export/{step_id}")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "model/stl"
    assert len(resp.content) > 200


def test_revert_moves_current_pointer():
    client = TestClient(app)
    client.get("/api/session")  # step 0
    client.post(
        "/api/execute-manual",
        json={"code": "import cadquery as cq\nresult = cq.Workplane('XY').box(5, 5, 5)\n"},
    )
    reverted = client.post("/api/steps/0/revert").json()
    assert reverted["current_id"] == 0


def test_failed_manual_step_does_not_advance_current():
    client = TestClient(app)
    client.get("/api/session")  # step 0 current
    out = client.post("/api/execute-manual", json={"code": "not valid python"}).json()
    assert out["step"]["success"] is False
    assert out["step"]["error"]
    # current stays on the last good step (0), failed step recorded but not current.
    assert out["session"]["current_id"] == 0
