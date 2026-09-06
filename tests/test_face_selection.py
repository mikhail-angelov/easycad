"""Face-selection contract: browser IDs resolve only to the current CAD snapshot."""

from types import SimpleNamespace

from fastapi.testclient import TestClient

from app import llm
from app.main import app


BOX = "import cadquery as cq\nresult = cq.Workplane('XY').box(50, 80, 30)\n"


def _selection(step: dict) -> dict:
    mesh = step["face_mesh"]
    face = next(item for item in mesh["faces"] if item["planar"] and item["center"] is not None)
    return {"face_revision": mesh["revision"], "face_id": face["id"]}


def test_current_step_exposes_a_snapshot_scoped_face_map():
    current = TestClient(app).get("/api/session").json()["current"]
    mesh = current["face_mesh"]
    assert len(mesh["revision"]) == 32
    assert len(mesh["faces"]) == 6
    assert mesh["positions"] and mesh["indices"]


def test_selected_planar_face_is_server_verified_and_reaches_generator(monkeypatch):
    messages = []

    async def completion(seen, *args, **kwargs):
        messages.extend(seen)
        return SimpleNamespace(content=BOX)

    monkeypatch.setattr(llm, "completion", completion)
    client = TestClient(app)
    current = client.get("/api/session").json()["current"]
    response = client.post("/api/chat", json={
        "prompt": "Engrave ABC in the centre of the selected face", "auto_refine": False,
        **_selection(current),
    })
    assert response.status_code == 200, response.text
    assert any("TARGET SURFACE (server-verified" in item["content"] for item in messages)
    assert response.json()["step"]["face_mesh"]["revision"] != current["face_mesh"]["revision"]


def test_stale_face_selection_is_rejected_before_provider_call(monkeypatch):
    async def completion(*args, **kwargs):
        raise AssertionError("stale selection must not reach the provider")

    monkeypatch.setattr(llm, "completion", completion)
    client = TestClient(app)
    current = client.get("/api/session").json()["current"]
    response = client.post("/api/chat", json={
        "prompt": "Engrave ABC", "auto_refine": False,
        "face_revision": "not-current", "face_id": 0,
    })
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "stale_face_selection"


def test_face_selection_does_not_apply_to_unexecuted_editor_code(monkeypatch):
    async def completion(*args, **kwargs):
        raise AssertionError("a selection must not apply to different editor code")

    monkeypatch.setattr(llm, "completion", completion)
    client = TestClient(app)
    current = client.get("/api/session").json()["current"]
    response = client.post("/api/chat", json={
        "prompt": "Engrave ABC", "auto_refine": False,
        "current_code": BOX.replace("50, 80, 30", "60, 80, 30"),
        **_selection(current),
    })
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "stale_face_selection"


def test_annular_planar_face_cannot_be_used_as_a_centre_target(monkeypatch):
    async def completion(*args, **kwargs):
        raise AssertionError("an annular centre target must not reach the provider")

    monkeypatch.setattr(llm, "completion", completion)
    client = TestClient(app)
    ring = client.post("/api/execute-manual", json={
        "code": "import cadquery as cq\nresult = cq.Workplane('XY').circle(10).circle(5).extrude(3)\n",
    }).json()["step"]
    mesh = ring["face_mesh"]
    face = next(item for item in mesh["faces"] if item["planar"] and item["center"] is None)
    response = client.post("/api/chat", json={
        "prompt": "Engrave ABC", "auto_refine": False,
        "face_revision": mesh["revision"], "face_id": face["id"],
    })
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "unsupported_face_selection"
