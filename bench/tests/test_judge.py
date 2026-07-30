"""LLM-judge grading for open scenarios (bench-SPEC §4, M1).

The vision call and rendering are stubbed, so these exercise the hybrid grader —
auto `checks` from measured facts + visual `rubric` items — without a network,
key, matplotlib, or trimesh.
"""

import json
import types
from pathlib import Path

import pytest

from bench import judge
from bench.schema import CHECK_MEASURES, SchemaError, _parse, load_scenario

# facts.json that satisfies 011-phone-stand's checks (bodies=1, largest 100mm, z0).
GOOD_FACTS = {
    "valid": True, "watertight": True, "bodies": 1,
    "bbox_mm": [80.0, 100.0, 62.0], "volume_mm3": 120000.0,
    "coordinate_contract": {"ok": True, "z_min_mm": 0.0, "xy_center_mm": [0.0, 0.0]},
}


def _fake_client(content: str):
    resp = types.SimpleNamespace(
        choices=[types.SimpleNamespace(message=types.SimpleNamespace(content=content))])
    create = lambda **kw: resp  # noqa: E731
    return types.SimpleNamespace(
        chat=types.SimpleNamespace(completions=types.SimpleNamespace(create=create)))


def _stub_render(monkeypatch):
    def fake_render(stl, out):
        out.mkdir(parents=True, exist_ok=True)
        p = out / "iso.png"
        p.write_bytes(b"\x89PNG\r\n\x1a\n")  # bytes are only base64'd, never decoded
        return [p]
    monkeypatch.setattr(judge, "render_stl", fake_render)


def _attempt(tmp_path, facts=GOOD_FACTS):
    adir = tmp_path / "attempt-1"
    (adir / "turn-1").mkdir(parents=True)
    (adir / "turn-1" / "facts.json").write_text(json.dumps(facts))
    (adir / "turn-1" / "out.stl").write_bytes(b"solid\n")
    return adir


def test_eval_check_operators():
    f = {"bodies": 1, "bbox_mm": [80.0, 100.0, 62.0],
         "coordinate_contract": {"z_min_mm": 0.3}}
    assert judge.eval_check(f, {"measure": "bodies", "op": "eq", "value": 1})["pass"]
    assert not judge.eval_check(f, {"measure": "bodies", "op": "eq", "value": 2})["pass"]
    assert judge.eval_check(f, {"measure": "largest_dim_mm", "op": "between", "value": [60, 160]})["pass"]
    assert not judge.eval_check(f, {"measure": "largest_dim_mm", "op": "between", "value": [60, 90]})["pass"]
    assert judge.eval_check(f, {"measure": "z_min_mm", "op": "approx", "value": 0, "tol": 1.0})["pass"]
    # An unmeasurable value fails closed (§8 invariant).
    assert not judge.eval_check({}, {"measure": "bodies", "op": "eq", "value": 1})["pass"]


def test_open_attempt_passes_when_checks_and_rubric_pass(tmp_path, monkeypatch):
    _stub_render(monkeypatch)
    sc = load_scenario("011-phone-stand")  # rubric=2 visual, checks=3 fact
    adir = _attempt(tmp_path)
    client = _fake_client('{"items":[{"n":1,"pass":true,"why":"incline"},'
                          '{"n":2,"pass":true,"why":"lip"}]}')
    r = judge.judge_attempt(sc, adir, "fake-judge", client=client)
    assert r["verdict"] == "pass"
    # 3 mandatory invariants (valid/watertight/coordinate_contract) + 3 checks + 2 visual.
    assert len(r["items"]) == 8 and all(it["pass"] for it in r["items"])
    # Cached to judge.json for a pure regrade.
    assert json.loads((adir / "judge.json").read_text())["verdict"] == "pass"


def test_open_attempt_fails_closed_on_invalid_geometry(tmp_path, monkeypatch):
    # bench-SPEC §8: an invalid / non-watertight model can never pass, even with a
    # right body count, plausible size, and a positive visual verdict.
    _stub_render(monkeypatch)
    sc = load_scenario("011-phone-stand")
    adir = _attempt(tmp_path, {**GOOD_FACTS, "watertight": False})
    client = _fake_client('{"items":[{"n":1,"pass":true},{"n":2,"pass":true}]}')
    r = judge.judge_attempt(sc, adir, "fake-judge", client=client)
    assert r["verdict"] == "fail"
    assert next(it for it in r["items"] if it["text"] == "watertight")["pass"] is False


def test_open_attempt_fails_on_a_bad_fact_check(tmp_path, monkeypatch):
    _stub_render(monkeypatch)
    sc = load_scenario("011-phone-stand")
    adir = _attempt(tmp_path, {**GOOD_FACTS, "bodies": 3})  # not a single solid
    client = _fake_client('{"items":[{"n":1,"pass":true},{"n":2,"pass":true}]}')
    r = judge.judge_attempt(sc, adir, "fake-judge", client=client)
    assert r["verdict"] == "fail"
    assert r["primary_failure"] == "fact_check_fail"


def test_open_attempt_fails_on_a_bad_visual_item(tmp_path, monkeypatch):
    _stub_render(monkeypatch)
    sc = load_scenario("011-phone-stand")
    adir = _attempt(tmp_path)
    client = _fake_client('{"items":[{"n":1,"pass":true},{"n":2,"pass":false,"why":"no lip"}]}')
    r = judge.judge_attempt(sc, adir, "fake-judge", client=client)
    assert r["verdict"] == "fail"
    assert r["primary_failure"] == "rubric_fail"


def test_open_attempt_with_no_artifact_fails(tmp_path, monkeypatch):
    sc = load_scenario("011-phone-stand")
    adir = tmp_path / "attempt-1"
    (adir / "turn-1").mkdir(parents=True)  # no facts.json / out.stl
    r = judge.judge_attempt(sc, adir, "fake-judge", client=_fake_client("{}"))
    assert r["verdict"] == "fail" and r["primary_failure"] == "empty_result"


def test_check_measures_vocabulary_matches_judge():
    # Schema owns the measure vocabulary; the evaluator must resolve every one of
    # them from full facts (kept in sync so a schema-valid check never no-ops).
    full = {"bodies": 1, "volume_mm3": 1.0, "bbox_mm": [1.0, 2.0, 3.0],
            "coordinate_contract": {"z_min_mm": 0.0}, "watertight": True, "valid": True}
    for name in CHECK_MEASURES:
        assert judge._measure(full, name) is not None, name


def _open_raw(checks):
    return {"id": "x", "title": "t", "spec": "open", "turns": [{"prompt": "p"}],
            "rubric": ["a", "b", "c"], "checks": checks}


def test_schema_rejects_unknown_measure():
    with pytest.raises(SchemaError):
        _parse("x", _open_raw([{"measure": "nonsense", "op": "eq", "value": 1}]), Path("."))


def test_schema_rejects_bad_between_shape():
    with pytest.raises(SchemaError):
        _parse("x", _open_raw([{"measure": "bodies", "op": "between", "value": [1]}]), Path("."))


def test_schema_rejects_nonsensical_op_for_boolean_measure():
    # bool is an int subclass; `watertight ge 1` must not slip through untyped.
    with pytest.raises(SchemaError):
        _parse("x", _open_raw([{"measure": "watertight", "op": "ge", "value": 1}]), Path("."))
    # A numeric measure must not take a bool value, nor a negative approx tol.
    with pytest.raises(SchemaError):
        _parse("x", _open_raw([{"measure": "bodies", "op": "eq", "value": True}]), Path("."))
    with pytest.raises(SchemaError):
        _parse("x", _open_raw([{"measure": "z_min_mm", "op": "approx", "value": 0, "tol": -1}]), Path("."))


def test_schema_accepts_boolean_measure_eq():
    sc = _parse("x", _open_raw([{"measure": "watertight", "op": "eq", "value": True}]), Path("."))
    assert sc.checks[0]["measure"] == "watertight"


def test_schema_accepts_well_formed_checks():
    sc = _parse("x", _open_raw([{"measure": "largest_dim_mm", "op": "between", "value": [10, 20]}]),
                Path("."))
    assert sc.checks[0]["measure"] == "largest_dim_mm"

def test_model_family_and_self_confirmation_tokens():
    assert judge.model_family("google/gemma-3-27b-it") == "gemma"
    assert judge.model_family("deepseek/deepseek-v4-flash") == "deepseek"
    assert judge.model_family("deepseek-v4-flash") == "deepseek"
    assert judge.model_family(None) is None


def test_judge_visual_strict_and_crash_proof(tmp_path, monkeypatch):
    _stub_render(monkeypatch)
    renders = [tmp_path / "iso.png"]
    renders[0].write_bytes(b"\x89PNG")
    # A string "false" must NOT pass; a non-integer `n` must not crash the run and
    # leaves its item unanswered (fail-closed).
    client = _fake_client('{"items":[{"n":1,"pass":"false"},{"n":"two","pass":true}]}')
    out = judge.judge_visual(renders, ["item one", "item two"], "m", "t", client=client)
    assert out[0]["pass"] is False   # "false" string is not a real true
    assert out[1]["pass"] is False   # {"n":"two"} skipped → item unanswered → fail
    # Only a genuine JSON true passes.
    client2 = _fake_client('{"items":[{"n":1,"pass":true},{"n":2,"pass":1}]}')
    out2 = judge.judge_visual(renders, ["a", "b"], "m", "t", client=client2)
    assert out2[0]["pass"] is True and out2[1]["pass"] is False  # 1 is not True


def test_parse_items_fail_closed_on_malformed():
    pi = judge._parse_items
    assert pi('{"items": null}', 2) == {}          # items not a list → no crash
    assert pi('{"items": 3}', 2) == {}
    assert pi('not json at all', 2) == {}
    assert pi('[1, 2, 3]', 2) == {}                # body not an object
    got = pi('{"items":[{"n":1.9,"pass":true},{"n":"1","pass":true},{"n":3,"pass":true},'
             '{"n":true,"pass":true},{"n":2,"pass":true},{"n":2,"pass":false}]}', 2)
    assert set(got) == {2}                          # 1.9/"1"/true rejected, 3 out of range
    assert got[2]["pass"] is True                   # duplicate n=2 → first wins


def test_schema_rejects_scalar_or_nonstring_rubric():
    bad_scalar = {"id": "x", "title": "t", "spec": "open", "turns": [{"prompt": "p"}],
                  "rubric": "abc", "checks": [{"measure": "bodies", "op": "eq", "value": 1}]}
    with pytest.raises(SchemaError):
        _parse("x", bad_scalar, Path("."))
    bad_item = {**bad_scalar, "rubric": ["ok", "", "  "]}
    with pytest.raises(SchemaError):
        _parse("x", bad_item, Path("."))


def _judged(tmp_path, monkeypatch, visual='{"items":[{"n":1,"pass":true},{"n":2,"pass":true}]}',
            facts=GOOD_FACTS, render=None):
    """Produce a real, fully-formed judge.json (correct inputs hash + full item set +
    run_id 'rid1') via judge_attempt, so tampering tests start from valid."""
    (render or _stub_render)(monkeypatch)
    sc = load_scenario("011-phone-stand")
    adir = _attempt(tmp_path, facts)
    judge.judge_attempt(sc, adir, "m", client=_fake_client(visual),
                        provenance={"judge_run_id": "rid1", "model": "m"})
    return sc, adir


def test_grade_open_trusts_a_well_formed_cache(tmp_path, monkeypatch):
    from bench.grade import _grade_open_attempt
    sc, adir = _judged(tmp_path, monkeypatch)
    assert _grade_open_attempt(sc, adir, "rid1")["verdict"] == "pass"


def test_grade_open_recomputes_verdict_from_items(tmp_path, monkeypatch):
    # A hand-flipped `verdict: pass` over a failing item must be caught.
    from bench.grade import _grade_open_attempt
    sc, adir = _judged(tmp_path, monkeypatch)
    j = json.loads((adir / "judge.json").read_text())
    j["verdict"] = "pass"
    j["items"][-1]["pass"] = False
    (adir / "judge.json").write_text(json.dumps(j))
    assert _grade_open_attempt(sc, adir, "rid1")["verdict"] == "fail"


def test_grade_open_rejects_stale_input(tmp_path, monkeypatch):
    from bench.grade import _grade_open_attempt
    sc, adir = _judged(tmp_path, monkeypatch)
    (adir / "turn-1" / "out.stl").write_bytes(b"CHANGED after judging")
    assert _grade_open_attempt(sc, adir, "rid1")["verdict"] == "unjudged"


def test_grade_open_recomputes_facts_ignoring_cache(tmp_path, monkeypatch):
    # The real threat: a cache whose FACT `pass` is flipped true over invalid
    # geometry. Facts are recomputed from facts.json, so the tamper is ignored.
    from bench.grade import _grade_open_attempt
    sc, adir = _judged(tmp_path, monkeypatch, facts={**GOOD_FACTS, "watertight": False})
    j = json.loads((adir / "judge.json").read_text())
    j["verdict"] = "pass"
    for it in j["items"]:
        it["pass"] = True   # flip everything true, incl. the watertight fact
    (adir / "judge.json").write_text(json.dumps(j))
    r = _grade_open_attempt(sc, adir, "rid1")
    assert r["verdict"] == "fail"   # recomputed watertight=False wins
    assert next(x for x in r["turns"][0]["checks"] if x["text"] == "watertight")["pass"] is False


def test_grade_open_subset_and_render_failure_fail_closed(tmp_path, monkeypatch):
    from bench.grade import _grade_open_attempt
    # A stripped cache (visual answers removed) → each rubric line fails closed.
    sc, adir = _judged(tmp_path, monkeypatch)
    j = json.loads((adir / "judge.json").read_text())
    j["items"] = [it for it in j["items"] if it["kind"] != "visual"]  # drop visual
    (adir / "judge.json").write_text(json.dumps(j))
    assert _grade_open_attempt(sc, adir, "rid1")["verdict"] == "fail"

    # Render failure at judge time (render_stl → []) is a fail, not a vanished row.
    def _stub_render_empty(mp):
        mp.setattr(judge, "render_stl", lambda stl, out: [])
    sc2, adir2 = _judged(tmp_path / "rf", monkeypatch, render=_stub_render_empty)
    assert _grade_open_attempt(sc2, adir2, "rid1")["verdict"] == "fail"


def test_grade_open_unjudged_on_corrupt_or_wrong_id(tmp_path, monkeypatch):
    from bench.grade import _grade_open_attempt
    sc, adir = _judged(tmp_path, monkeypatch)
    (adir / "judge.json").write_text("{not json")            # corrupt → no crash
    assert _grade_open_attempt(sc, adir, "rid1")["verdict"] == "unjudged"
    sc2, adir2 = _judged(tmp_path / "b", monkeypatch)
    assert _grade_open_attempt(sc2, adir2, "other-id")["verdict"] == "unjudged"  # wrong id
    # A non-object `judge` field is malformed, not a crash.
    j = json.loads((adir2 / "judge.json").read_text())
    j["judge"] = "x"
    (adir2 / "judge.json").write_text(json.dumps(j))
    assert _grade_open_attempt(sc2, adir2, "rid1")["verdict"] == "unjudged"


def test_judge_env_config_is_lazy():
    # Overrides must be honoured after .env load, not frozen at import time.
    import os
    old = os.environ.get("BENCH_JUDGE_BASE_URL")
    os.environ["BENCH_JUDGE_BASE_URL"] = "https://example.test/v1"
    try:
        assert judge.judge_base_url() == "https://example.test/v1"
    finally:
        if old is None:
            del os.environ["BENCH_JUDGE_BASE_URL"]
        else:
            os.environ["BENCH_JUDGE_BASE_URL"] = old
