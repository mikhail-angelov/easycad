"""In-turn repair loop (app.main `_generate_and_step`).

On an execution failure the turn feeds the error back to the model and lets it
fix its own code, up to `MAX_REPAIR` extra attempts — the text-mode equivalent
of an agentic tool loop. The LLM is stubbed; CadQuery still runs the stub's
code, so a "broken" stub must fail `execute()` the way real bad code would.

Money is charged carefully: the operator daily budget is charged per generate
call (so repairs can't overshoot the cap), while the lifetime free-N trial grant
is charged once per turn regardless of how many repairs it took.
"""

from fastapi.testclient import TestClient

import app.main as m
from app.main import app
from app import db, metrics

BOX = "import cadquery as cq\nresult = cq.Workplane('XY').box(10, 10, 10)\n"
BROKEN = "x = 1\n"  # runs but never defines `result` → execute() reports failure


def _chat(client, ip="7.7.7.7", prompt="add a hole"):
    return client.post(
        "/api/chat",
        json={"prompt": prompt, "auto_refine": False, "current_code": BOX},
        headers={"x-real-ip": ip},
    )


def _scripted_generate(monkeypatch, scripted):
    """Stub generate_code to return scripted[i] on the i-th call (repeating the
    last entry once exhausted), recording the `feedback` kwarg of every call."""
    seen = []

    def fake_generate(base_code, prompt, provider, model=None, temperature=0.2,
                      api_key=None, skills=None, feedback=None):
        seen.append(feedback)
        return scripted[min(len(seen) - 1, len(scripted) - 1)]

    monkeypatch.setattr(m, "generate_code", fake_generate)
    return seen


def test_repair_recovers_within_the_turn(monkeypatch):
    monkeypatch.setattr(m, "MAX_REPAIR", 2)
    seen = _scripted_generate(monkeypatch, [BROKEN, BOX])
    client = TestClient(app)
    r = _chat(client, ip="7.1.1.1")
    assert r.status_code == 200, r.text
    assert r.json()["step"]["success"] is True   # repaired → the turn succeeds
    assert len(seen) == 2                          # one repair attempt was enough
    assert seen[0] is None                         # first attempt: no feedback
    # The repair attempt sees ONLY its own failed code + the measured error.
    assert seen[1]["code"] == BROKEN
    assert seen[1]["error"]


def test_repair_bounded_by_max_repair(monkeypatch):
    monkeypatch.setattr(m, "MAX_REPAIR", 2)
    before = metrics.snapshot().get("gen_repair", 0)
    seen = _scripted_generate(monkeypatch, [BROKEN])  # never recovers
    client = TestClient(app)
    r = _chat(client, ip="7.2.2.2")
    assert r.status_code == 200, r.text
    assert r.json()["step"]["success"] is False
    assert len(seen) == 3                              # 1 + MAX_REPAIR, then give up
    assert metrics.snapshot().get("gen_repair", 0) - before == 2


def test_max_repair_zero_is_one_shot(monkeypatch):
    monkeypatch.setattr(m, "MAX_REPAIR", 0)
    seen = _scripted_generate(monkeypatch, [BROKEN])
    client = TestClient(app)
    r = _chat(client, ip="7.3.3.3")
    assert r.status_code == 200, r.text
    assert r.json()["step"]["success"] is False
    assert len(seen) == 1                              # no repair attempts at all


def test_negative_max_repair_is_one_shot_not_500(monkeypatch):
    # A misconfigured (negative) MAX_REPAIR must degrade to one-shot, never leave
    # `res` None and 500. Config is clamped at import; this guards the use site.
    monkeypatch.setattr(m, "MAX_REPAIR", -1)
    seen = _scripted_generate(monkeypatch, [BROKEN])
    client = TestClient(app)
    r = _chat(client, ip="7.7.7.1")
    assert r.status_code == 200, r.text
    assert r.json()["step"]["success"] is False
    assert len(seen) == 1                              # exactly one attempt, no crash


def test_repair_does_not_multiply_charge_trial(monkeypatch):
    # The lifetime free-N grant is charged ONCE per turn even when the turn makes
    # several generate calls to repair itself.
    monkeypatch.setattr(m, "MAX_REPAIR", 2)
    monkeypatch.setattr(m, "TRIAL_ANON", 1)
    _scripted_generate(monkeypatch, [BROKEN, BOX])
    client = TestClient(app)
    r = _chat(client, ip="7.4.4.4")
    assert r.status_code == 200 and r.json()["step"]["success"] is True
    assert db.get_anon_trial("7.4.4.4") == 1           # one grant unit, not two
    # ...and the grant is now spent: the next turn is exhausted.
    r2 = _chat(client, ip="7.4.4.4")
    assert r2.status_code == 402
    assert r2.json()["detail"]["code"] == "trial_exhausted_anon"


def test_repair_charges_operator_budget_per_attempt(monkeypatch):
    # Each generate is one operator-key call → each repair charges the daily
    # budget; a spent budget stops the loop mid-turn (keep the best result so far).
    monkeypatch.setattr(m, "MAX_REPAIR", 5)
    monkeypatch.setattr(m, "TRIAL_DAILY_BUDGET", 2)
    m._budget_state.update(day="", used=0)
    seen = _scripted_generate(monkeypatch, [BROKEN])   # always broken
    client = TestClient(app)
    r = _chat(client, ip="7.5.5.5")
    assert r.status_code == 200, r.text
    assert r.json()["step"]["success"] is False
    assert len(seen) == 2                              # budget of 2 → 2 generates, stop
    assert m._budget_state["used"] == 2


def test_byok_repairs_are_free_and_bounded_only_by_max_repair(monkeypatch):
    # BYOK: no operator budget, no trial grant. Repairs still run, bounded solely
    # by MAX_REPAIR, and never touch an operator budget unit.
    monkeypatch.setattr(m, "MAX_REPAIR", 2)
    monkeypatch.setattr(m, "TRIAL_DAILY_BUDGET", 1)    # would bite if BYOK were charged
    m._budget_state.update(day="", used=0)
    seen = _scripted_generate(monkeypatch, [BROKEN])
    client = TestClient(app)
    client.get("/api/session", headers={"x-real-ip": "7.6.6.6"})
    client.put("/api/settings", json={"provider": "deepseek", "key": "sk-mykey"})
    r = _chat(client, ip="7.6.6.6")
    assert r.status_code == 200, r.text
    assert r.json()["step"]["success"] is False
    assert len(seen) == 3                              # full 1 + MAX_REPAIR, budget ignored
    assert m._budget_state["used"] == 0                # BYOK spent no operator budget
