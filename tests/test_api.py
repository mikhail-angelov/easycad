"""API wiring tests using FastAPI TestClient.

Multi-tenant (SPEC13): each TestClient has its own cookie jar → its own
in-memory session. Covers the non-LLM paths. The /api/chat path needs a live
provider and is not exercised here. Requires cadquery (.venv-poc interpreter).
"""

from fastapi.testclient import TestClient

from app.main import app


def test_session_bootstrap_creates_initial_step():
    client = TestClient(app)
    data = client.get("/api/session").json()
    assert data["current_id"] == 0
    assert len(data["steps"]) == 1
    assert data["steps"][0]["kind"] == "initial"
    assert data["current"]["success"] is True
    assert "deepseek" in data["providers"]
    assert data["auth"]["authenticated"] is False
    assert data["settings"]["has_key"] is False


def test_execute_endpoint_is_stateless():
    client = TestClient(app)
    client.get("/api/session")  # step 0
    body = {"code": "import cadquery as cq\nresult = cq.Workplane('XY').box(10, 10, 10)\n"}
    res = client.post("/api/execute", json=body).json()
    assert res["success"]
    assert res["stl_base64"]
    assert "Size: 10.0 x 10.0 x 10.0 mm" in res["geometry_info"]
    # /api/execute is stateless — no new step recorded (still just the initial).
    steps = client.get("/api/steps").json()
    assert len(steps) == 1


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
    assert out["session"]["current_id"] == 0


def test_oversize_prompt_is_rejected():
    client = TestClient(app)
    client.get("/api/session")
    r = client.post("/api/chat", json={"prompt": "x" * 20_001})
    assert r.status_code == 422  # exceeds MAX_PROMPT, rejected before generation


def test_oversize_body_is_rejected():
    client = TestClient(app)
    r = client.post("/api/execute", json={"code": "x" * 2_100_000})
    assert r.status_code == 413  # body-size middleware, before parsing


def test_two_clients_have_independent_sessions():
    a = TestClient(app)
    b = TestClient(app)
    a.get("/api/session")
    a.post("/api/execute-manual", json={"code": "import cadquery as cq\nresult = cq.Workplane('XY').box(8, 8, 8)\n"})
    # b's session is untouched by a's manual step.
    b_data = b.get("/api/session").json()
    assert len(b_data["steps"]) == 1
    a_data = a.get("/api/session").json()
    assert len(a_data["steps"]) == 2
