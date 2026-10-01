"""SPEC23 W1/W5: a chat step that runs but changes nothing is not reported as success."""

from types import SimpleNamespace

from fastapi.testclient import TestClient

from app import llm, main
from app.main import app
from app.store import same_geometry

PLATE = "import cadquery as cq\nresult = cq.Workplane('XY').box(60, 40, 5)\n"
FILLETED = PLATE.rstrip() + ".edges('|Z').fillet(4)\n"
# Same geometry as FILLETED from different source text: the model "did something".
FILLETED_AGAIN = (
    "import cadquery as cq\nplate = cq.Workplane('XY').box(60, 40, 5)\n"
    "result = plate.edges('|Z').fillet(4)\n"
)
HOLE_LEFT = PLATE.rstrip() + ".faces('>Z').workplane().center(-15, 0).hole(8)\n"
HOLE_RIGHT = PLATE.rstrip() + ".faces('>Z').workplane().center(15, 0).hole(8)\n"


def _turns(monkeypatch, *codes: str) -> list[dict]:
    replies = iter(codes)

    async def completion(*args, **kwargs):
        return SimpleNamespace(content=next(replies))

    monkeypatch.setattr(llm, "completion", completion)
    monkeypatch.setattr(main, "TRIAL_ANON", 100)  # several generations in one session
    client = TestClient(app)
    steps = []
    for _ in codes:
        response = client.post("/api/chat", json={"prompt": "Round the vertical edges", "auto_refine": False})
        assert response.status_code == 200, response.text
        steps.append(response.json()["step"])
    return steps


def test_repeated_fillet_is_flagged_not_reported_as_success(monkeypatch):
    plate, filleted, again = _turns(monkeypatch, PLATE, FILLETED, FILLETED_AGAIN)
    assert plate["verdict"] == "changed"
    assert filleted["verdict"] == "changed"
    assert again["verdict"] == "no_change_detected"
    # A valid model, not an error: it stays current and keeps its facts.
    assert again["success"] is True and again["error"] is None
    assert again["facts"] == filleted["facts"]


def test_moved_feature_with_identical_volume_and_bbox_is_a_change(monkeypatch):
    left, right = _turns(monkeypatch, HOLE_LEFT, HOLE_RIGHT)
    assert left["facts"]["volume_mm3"] == right["facts"]["volume_mm3"]
    assert right["verdict"] == "changed"


def test_facts_describe_the_model(monkeypatch):
    (plate,) = _turns(monkeypatch, PLATE)
    facts = plate["facts"]
    assert abs(facts["volume_mm3"] - 60 * 40 * 5) < 1e-6
    assert [round(v, 3) for v in facts["bbox_mm"]] == [-30, -20, -2.5, 30, 20, 2.5]
    assert (facts["solids"], facts["faces"], facts["edges"]) == (1, 6, 12)


def test_missing_measurement_never_claims_a_no_op():
    facts = {"volume_mm3": 1.0, "area_mm2": 6.0, "bbox_mm": [0] * 6, "center_mm": [0] * 3,
             "solids": 1, "faces": 6, "edges": 12}
    assert same_geometry(None, facts) is None
    assert same_geometry(facts, {"volume_mm3": 1.0}) is None
    assert same_geometry(facts, dict(facts)) is True


def test_cq_index_is_regenerated_from_the_allow_list():
    from app import cq_index

    assert cq_index.load() == cq_index.build(), "run: .venv-poc/bin/python -m app.cq_index"
    text = cq_index.load()
    for _, method, _ in cq_index.ALLOW_LIST:
        assert f"- {method}(" in text
    # Hard budget (~1.5k tokens): the 2026-07-29 A/B showed prompt bloat costs quality.
    assert len(text) <= 6000


def test_code_is_kept_when_the_model_wraps_it_in_prose():
    from app.llm import strip_markdown_fences

    code = "import cadquery as cq\nresult = cq.Workplane('XY').box(1, 1, 1)"
    for reply in (
        code,
        f"```python\n{code}\n```",
        f"Here is the model:\n\n```python\n{code}\n```\n\nIt is a 1 mm cube.",
        f"{code}\n```\n\n**What this builds**\n- a 1 × 1 × 1 mm cube",  # bench 2026-10-01
    ):
        assert strip_markdown_fences(reply) == code
