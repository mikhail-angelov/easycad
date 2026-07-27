"""SPEC15: on-demand generation skills.

Triage returns skill tags; they must reach `generate_code` on the direct
("ready") path and survive the confirm_refine round-trip (auto_refine off).
The LLM is stubbed so no live provider is needed; CadQuery still runs the
stub's code, so it must be valid.
"""

import struct

import numpy as np
from fastapi.testclient import TestClient

import app.main as m
from app.main import app
from app import skills
from app.cadquery_exec import execute
from app.refiner import TriageResult, _parse, _triage_system_prompt

BOX = "import cadquery as cq\nresult = cq.Workplane('XY').box(10, 10, 10)\n"


def _load_zr(stl_b64: str) -> tuple[np.ndarray, np.ndarray]:
    """Parse a binary STL and return (z, radius) arrays for every mesh vertex."""
    import base64
    raw = base64.b64decode(stl_b64)
    n = struct.unpack("<I", raw[80:84])[0]
    # 50 bytes/triangle: 12-byte normal, 3×3 float32 verts, 2-byte attr
    v = np.zeros((n * 3, 3), dtype=np.float32)
    off = 84
    for i in range(n):
        v[i * 3:i * 3 + 3] = np.frombuffer(raw[off + 12:off + 48], dtype="<f4").reshape(3, 3)
        off += 50
    return v[:, 2], np.sqrt(v[:, 0] ** 2 + v[:, 1] ** 2)


def _radii_at_z(z: np.ndarray, r: np.ndarray, zc: float, half: float = 0.4) -> np.ndarray:
    """Radii in a thin horizontal slab around z=zc. On a real thread these swing
    from root (~r_min) to crest (~r_maj) as the helix winds past that height; a
    smooth cylinder or a collapsed thread gives a near-constant radius."""
    return r[(z > zc - half) & (z < zc + half)]


def _capture_generate(monkeypatch):
    """Stub generate_code, recording the skills it was called with."""
    seen: dict = {"skills": "unset"}

    def fake_generate(base_code, prompt, provider, model=None, temperature=0.2,
                      api_key=None, skills=None):
        seen["skills"] = skills
        return BOX

    monkeypatch.setattr(m, "generate_code", fake_generate)
    return seen


def test_render_and_clean_tags():
    assert skills.clean_tags(["thread", "THREAD", "bogus", ""]) == ["thread"]
    assert skills.clean_tags(None) == []
    body = skills.render(["thread"])
    assert body and "makeHelix" in body
    assert skills.render(["bogus"]) is None


def test_ready_path_passes_skills(monkeypatch):
    seen = _capture_generate(monkeypatch)
    monkeypatch.setattr(m, "triage", lambda *a, **k: TriageResult("ready", skills=["thread"]))
    client = TestClient(app)
    r = client.post("/api/chat", json={"prompt": "M12 bolt", "auto_refine": True, "current_code": BOX},
                    headers={"x-real-ip": "3.3.3.3"})
    assert r.status_code == 200, r.text
    assert seen["skills"] == ["thread"]


def test_chat_passes_interface_language_to_triage(monkeypatch):
    seen: dict = {}
    monkeypatch.setattr(m, "generate_code", lambda *a, **k: BOX)

    def fake_triage(*args, **kwargs):
        seen["response_language"] = kwargs["response_language"]
        return TriageResult("ready")

    monkeypatch.setattr(m, "triage", fake_triage)
    client = TestClient(app)
    r = client.post("/api/chat", json={
        "prompt": "add a hole", "auto_refine": True, "current_code": BOX,
        "response_language": "ru",
    }, headers={"x-real-ip": "3.3.3.5"})

    assert r.status_code == 200, r.text
    assert seen["response_language"] == "ru"


def test_triage_uses_interface_language_for_prompt_and_fallback_reason():
    assert 'reason" in Russian' in _triage_system_prompt("ru")
    assert 'reason" in English' in _triage_system_prompt("en")
    assert _parse('{"verdict": "invalid"}', "ru").reason == (
        "Запрос, похоже, не соответствует текущей модели."
    )


def test_confirm_refine_carries_skills_server_side(monkeypatch):
    seen = _capture_generate(monkeypatch)
    # First turn: triage asks to refine and tags the thread skill.
    monkeypatch.setattr(
        m, "triage",
        lambda *a, **k: TriageResult("refine", refined_prompt="M12x80, 30mm thread", skills=["thread"]),
    )
    client = TestClient(app)  # one client → cookie persists → same session
    r1 = client.post("/api/chat", json={"prompt": "bolt", "auto_refine": True, "current_code": BOX},
                     headers={"x-real-ip": "3.3.3.4"})
    assert r1.status_code == 200, r1.text
    body = r1.json()
    assert body["action"] == "confirm_refine"
    assert "skills" not in body                    # not exposed to the client
    assert seen["skills"] == "unset"               # no generation yet

    # The server stored the pending skills, bound to the original prompt.
    sid = client.cookies.get("easycad_session")
    assert m.registry.get(sid).pending_skills == ("bolt", ["thread"])

    # Confirm turn: auto_refine off, same original prompt, NO skills in the
    # request — the server pulls them from the pending refinement it stored.
    r2 = client.post("/api/chat", json={
        "prompt": "bolt", "auto_refine": False,
        "refined_prompt": body["refined_prompt"], "current_code": BOX,
    }, headers={"x-real-ip": "3.3.3.4"})
    assert r2.status_code == 200, r2.text
    assert r2.json()["action"] == "generated"
    assert seen["skills"] == ["thread"]            # recipe reached the generator
    # and the pending skills are consumed once (no stale leakage to later turns).
    assert m.registry.get(sid).pending_skills is None


def test_pending_skills_bound_to_prompt(monkeypatch):
    # A pending refinement for one prompt must NOT apply to a DIFFERENT
    # auto_refine=off prompt in the same session (P2 binding).
    seen = _capture_generate(monkeypatch)
    monkeypatch.setattr(
        m, "triage",
        lambda *a, **k: TriageResult("refine", refined_prompt="rp", skills=["thread"]),
    )
    client = TestClient(app)
    client.post("/api/chat", json={"prompt": "make a bolt", "auto_refine": True, "current_code": BOX},
                headers={"x-real-ip": "3.3.4.1"})  # → pending bound to "make a bolt"
    # A different prompt with auto_refine off must not inherit the thread recipe.
    r = client.post("/api/chat", json={"prompt": "make a plate", "auto_refine": False,
                                       "refined_prompt": "make a plate", "current_code": BOX},
                    headers={"x-real-ip": "3.3.4.1"})
    assert r.status_code == 200, r.text
    assert seen["skills"] is None
    # A mismatched turn must NOT destroy the pending — the real confirm of "make a
    # bolt" can still come later and get the recipe.
    sid = client.cookies.get("easycad_session")
    assert m.registry.get(sid).pending_skills == ("make a bolt", ["thread"])


def test_failed_confirm_keeps_pending_skills(monkeypatch):
    # If the confirm turn fails (provider error / budget), the pending refinement
    # must survive so retrying the same confirmation still loads the recipe.
    from app.llm import LLMError

    monkeypatch.setattr(
        m, "triage",
        lambda *a, **k: TriageResult("refine", refined_prompt="rp", skills=["thread"]),
    )
    client = TestClient(app)
    client.post("/api/chat", json={"prompt": "bolt", "auto_refine": True, "current_code": BOX},
                headers={"x-real-ip": "3.3.5.5"})
    sid = client.cookies.get("easycad_session")
    assert m.registry.get(sid).pending_skills == ("bolt", ["thread"])

    def boom(*a, **k):
        raise LLMError("provider down")

    monkeypatch.setattr(m, "generate_code", boom)
    r = client.post("/api/chat", json={"prompt": "bolt", "auto_refine": False,
                                       "refined_prompt": "rp", "current_code": BOX},
                    headers={"x-real-ip": "3.3.5.5"})
    assert r.status_code == 502, r.text
    # pending intact → a retry would still get the recipe
    assert m.registry.get(sid).pending_skills == ("bolt", ["thread"])


def test_failed_exec_keeps_pending_then_retry_gets_recipe(monkeypatch):
    # The most likely real failure: generated code that runs but doesn't produce
    # a valid model (execute().success is False, not an exception). Pending must
    # survive so the retry still loads the recipe — end-to-end through the worker.
    monkeypatch.setattr(m, "TRIAL_ANON", 100)  # allow two generations in one session
    monkeypatch.setattr(
        m, "triage",
        lambda *a, **k: TriageResult("refine", refined_prompt="rp", skills=["thread"]),
    )
    calls = []
    BROKEN = "x = 1\n"  # executes but defines no `result` → execute() reports failure

    def fake_generate(base_code, prompt, provider, model=None, temperature=0.2,
                      api_key=None, skills=None):
        calls.append(skills)
        return BROKEN if len(calls) == 1 else BOX

    monkeypatch.setattr(m, "generate_code", fake_generate)
    client = TestClient(app)
    client.post("/api/chat", json={"prompt": "bolt", "auto_refine": True, "current_code": BOX},
                headers={"x-real-ip": "3.3.6.6"})
    sid = client.cookies.get("easycad_session")
    assert m.registry.get(sid).pending_skills == ("bolt", ["thread"])

    # Confirm 1: exec fails → step.success False, recipe was delivered, pending KEPT.
    r1 = client.post("/api/chat", json={"prompt": "bolt", "auto_refine": False,
                                        "refined_prompt": "rp", "current_code": BOX},
                     headers={"x-real-ip": "3.3.6.6"})
    assert r1.status_code == 200, r1.text
    assert r1.json()["step"]["success"] is False
    assert calls[0] == ["thread"]
    assert m.registry.get(sid).pending_skills == ("bolt", ["thread"])

    # Confirm 2 (retry): good code → success, recipe delivered again, pending consumed.
    r2 = client.post("/api/chat", json={"prompt": "bolt", "auto_refine": False,
                                        "refined_prompt": "rp", "current_code": BOX},
                     headers={"x-real-ip": "3.3.6.6"})
    assert r2.status_code == 200, r2.text
    assert r2.json()["step"]["success"] is True
    assert calls[1] == ["thread"]
    assert m.registry.get(sid).pending_skills is None


def test_budget_exhausted_confirm_keeps_pending(monkeypatch):
    # Budget exhaustion raises before generation; the pending refinement must
    # survive so a retry (once budget frees up) still gets the recipe.
    monkeypatch.setattr(
        m, "triage",
        lambda *a, **k: TriageResult("refine", refined_prompt="rp", skills=["thread"]),
    )
    client = TestClient(app)
    client.post("/api/chat", json={"prompt": "bolt", "auto_refine": True, "current_code": BOX},
                headers={"x-real-ip": "3.3.7.7"})
    sid = client.cookies.get("easycad_session")
    assert m.registry.get(sid).pending_skills == ("bolt", ["thread"])
    # Force the operator-budget charge to fail on the confirm turn.
    monkeypatch.setattr(m, "_charge_operator_call", lambda *a, **k: False)
    r = client.post("/api/chat", json={"prompt": "bolt", "auto_refine": False,
                                       "refined_prompt": "rp", "current_code": BOX},
                    headers={"x-real-ip": "3.3.7.7"})
    assert r.status_code == 402, r.text
    assert m.registry.get(sid).pending_skills == ("bolt", ["thread"])


def test_thread_recipe_executes_with_a_whole_threaded_bolt():
    # Executes the ACTUAL recipe example (skills.THREAD_EXAMPLE, the same string
    # embedded in the prompt) end-to-end through the real worker path — no
    # hand-copied duplicate that could drift. Asserts the WHOLE bolt survives the
    # failure modes hit while developing this recipe (thread collapsing to a
    # smooth cylinder; a bad union order deleting the head+shank) AND that it
    # honours the coordinate contract. THREAD_EXAMPLE is M12x80, lifted to sit on
    # the XY plane: head z[0..8.4], shank z[8.4..58.4], thread top ~z[58.4..88.4].
    res = execute(skills.THREAD_EXAMPLE)
    assert res.success, res.error
    z, r = _load_zr(res.stl_base64)

    # Coordinate contract: the part sits ON the XY plane (z_min ≈ 0, Z up). This
    # also catches the union bug that deleted the head+shank — then the model
    # would start at the thread (z ≈ 58), not at 0.
    assert abs(float(z.min())) < 0.15, f"z_min={z.min():.2f} — bolt not sitting on the XY plane"

    # A REAL thread near the top: radius swings root→crest around the circle.
    # Derive the thread mid-height from the model's own extent (top minus half of
    # THREAD_LEN=30) rather than hard-coding, so it tracks the recipe.
    zc = float(z.max()) - 15.0
    slab = _radii_at_z(z, r, zc=zc)
    assert slab.size, "no surface at mid-thread"
    swing = float(slab.max() - slab.min())
    assert swing > 0.5, f"radius swing only {swing:.2f} mm — no real thread (looks like a cylinder)"
    assert slab.max() > 5.7, f"crest only reached r={slab.max():.2f} — thread collapsed to the core"

    # The hex head is present (large across-corners radius) and the bolt is full
    # height (head + shank + thread).
    assert r.max() > 9.0, f"hex head missing — max radius only {r.max():.1f} mm"
    assert z.max() > 80.0, f"bolt too short — ends at z={z.max():.1f} (head or shank missing)"


def test_recipe_embeds_the_executed_example_and_stays_mathless():
    # The recipe the model sees must contain the exact example the test executes
    # (single source of truth), and must not conflict with the base prompt's
    # "no new imports" rule.
    recipe = skills.SKILLS["thread"].recipe
    assert skills.THREAD_EXAMPLE in recipe              # taught == tested
    assert "import math" not in recipe and "from math" not in recipe
    assert "3 ** 0.5" in recipe
    assert "makeRuledSurface" in skills.THREAD_EXAMPLE  # ruled-surface thread
    assert "sweep(" not in skills.THREAD_EXAMPLE        # the fragile method is gone


def test_variations_passes_skills(monkeypatch):
    seen = {"skills": "unset"}

    def fake_generate(base_code, prompt, provider, model=None, temperature=0.2,
                      api_key=None, skills=None):
        seen["skills"] = skills
        return BOX

    monkeypatch.setattr(m, "generate_code", fake_generate)
    monkeypatch.setattr(m, "triage", lambda *a, **k: TriageResult("ready", skills=["thread"]))
    client = TestClient(app)
    r = client.post("/api/variations", json={
        "prompt": "M12 bolt", "auto_refine": True, "count": 1, "current_code": BOX,
    }, headers={"x-real-ip": "3.3.3.6"})
    assert r.status_code == 200, r.text
    assert seen["skills"] == ["thread"]


def test_refine_endpoint_returns_skills(monkeypatch):
    monkeypatch.setattr(
        m, "triage",
        lambda *a, **k: TriageResult("refine", refined_prompt="M12x80", skills=["thread"]),
    )
    client = TestClient(app)
    r = client.post("/api/refine", json={"prompt": "bolt", "current_code": BOX},
                    headers={"x-real-ip": "3.3.3.7"})
    assert r.status_code == 200, r.text
    assert r.json()["skills"] == ["thread"]
    # /api/refine is stateless — it must NOT stash pending skills (that would race
    # /api/chat and persist for non-refine verdicts).
    sid = client.cookies.get("easycad_session")
    assert m.registry.get(sid).pending_skills is None


def test_client_cannot_forge_skills(monkeypatch):
    # Standards#1: a client that sends skills itself must NOT be able to load a
    # recipe. With no pending refinement on the session and skills only ever set
    # server-side, the request field is inert.
    seen = {"skills": "unset"}

    def fake_generate(base_code, prompt, provider, model=None, temperature=0.2,
                      api_key=None, skills=None):
        seen["skills"] = skills
        return BOX

    monkeypatch.setattr(m, "generate_code", fake_generate)
    client = TestClient(app)
    r = client.post("/api/chat", json={
        "prompt": "add a hole", "auto_refine": False, "skills": ["thread"],
        "refined_prompt": "add a hole", "current_code": BOX,
    }, headers={"x-real-ip": "3.3.3.8"})
    assert r.status_code == 200, r.text
    assert seen["skills"] is None


def test_triage_parse_validates_skill_tags():
    # The real triage parser must keep only known tags from the model's JSON.
    from app.refiner import _parse

    good = _parse('{"verdict": "ready", "skills": ["thread", "bogus"]}')
    assert good.skills == ["thread"]
    none = _parse('{"verdict": "ready"}')
    assert none.skills == []


def test_clean_tags_survives_malformed_values():
    # A malformed LLM "skills" value must degrade to [] (safe fallback), never
    # raise — otherwise triage/generation 500s instead of just skipping skills.
    assert skills.clean_tags(1) == []
    assert skills.clean_tags(3.5) == []
    assert skills.clean_tags({"thread": 1}) == []
    assert skills.clean_tags(True) == []
    assert skills.clean_tags(["thread", 5, "bogus"]) == ["thread"]
    assert skills.clean_tags("thread") == ["thread"]  # lenient: bare string tag


def test_triage_parse_survives_scalar_skills():
    # The exact reviewer case: "skills": 1 must not TypeError through triage.
    from app.refiner import _parse

    assert _parse('{"verdict": "ready", "skills": 1}').skills == []
    assert _parse('{"verdict": "ready", "skills": "thread"}').skills == ["thread"]
