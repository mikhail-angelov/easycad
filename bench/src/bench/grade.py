"""Grade one turn's generated model against its reference (bench-SPEC §5).

Pure and deterministic: given saved facts it produces a verdict with no
generation, so a run can be regraded for free when criteria change (§2.8).

Guiding invariant (§8): every check is asked which way it fails when the check
itself breaks, and any unmeasurable or ambiguous case counts as a failure.
"""

from __future__ import annotations

# primary_failure priority (bench-SPEC §5.2). Earlier = reported first.
PRIORITY = [
    "generation_error", "code_error", "occt_error", "timeout", "empty_result",
    "invalid_geometry", "not_watertight", "coordinate_contract_violation",
    "bbox_mismatch", "volume_mismatch", "shape_mismatch", "unmeasurable",
]


def classify_error(stage: str, message: str) -> str:
    """Bucket a generation/execution error into one of the first five classes
    (§5.2). `stage` is 'generate' or 'execute'."""
    msg = (message or "").lower()
    if stage == "generate":
        return "generation_error"
    if "timed out" in msg or "timeout" in msg:
        return "timeout"
    if "no 'result'" in msg or "no result" in msg or "empty" in msg:
        return "empty_result"
    if any(t in msg for t in ("occt", "stdfail", "brep", "standard_", "export error", "export:")):
        return "occt_error"
    if any(t in msg for t in ("worker unavailable", "worker error", "http", "response invalid")):
        return "generation_error"
    return "code_error"


def _check(name, state, level, expected, actual):
    return {"check": name, "state": state, "level": level,
            "expected": expected, "actual": actual}


def _rel(a: float, b: float) -> float:
    return abs(a - b) / abs(b) if b else (0.0 if a == 0 else float("inf"))


def grade_turn(gen: dict, expected: dict, tol: dict, deviation, deviation_aligned=None) -> dict:
    """Grade one turn.

    `gen`   — either {"error": {"stage", "message"}} for a generation/exec
              failure, or {"facts": <facts.json dict>}.
    `expected` — the reference turn's facts (expected/turn-N.json).
    `tol`   — merged tolerances.
    `deviation` — surface_deviation dict, or None if it could not be computed.
    `deviation_aligned` — deviation after XY/z_min alignment (§3.1), diagnostic
              only; None if not computed.

    Returns {"checks", "failure_classes", "primary_failure", "verdict"}.
    """
    checks: list[dict] = []
    classes: set[str] = set()

    if gen.get("error"):
        err = gen["error"]
        cls = classify_error(err.get("stage", "execute"), err.get("message", ""))
        classes.add(cls)
        checks.append(_check("generation", "fail", "required",
                             {"error": None}, {"stage": err.get("stage"),
                                               "message": err.get("message"), "class": cls}))
        return _finalize(checks, classes)

    facts = gen["facts"]

    # ── validity / watertight ────────────────────────────────────────────────
    valid = facts.get("valid")
    checks.append(_check("valid", "pass" if valid else "fail", "required",
                         {"valid": True}, {"valid": valid}))
    if not valid:
        classes.add("invalid_geometry")

    wt = facts.get("watertight")
    checks.append(_check("watertight", "pass" if wt else "fail", "required",
                         {"watertight": True}, {"watertight": wt}))
    if not wt:
        classes.add("not_watertight")

    # ── coordinate contract (checked before shape — §3.1) ────────────────────
    cc = facts.get("coordinate_contract") or {}
    cc_ok = bool(cc.get("ok"))
    checks.append(_check("coordinate_contract", "pass" if cc_ok else "fail", "required",
                         {"z_min≈0, xy_center≈0": True},
                         {"z_min_mm": cc.get("z_min_mm"), "xy_center_mm": cc.get("xy_center_mm")}))
    if not cc_ok:
        classes.add("coordinate_contract_violation")

    # ── bodies (connected components, construction-independent — §3.2) ───────
    exp_bodies = expected.get("bodies", 1)
    bodies = facts.get("bodies")
    bodies_ok = bodies == exp_bodies
    checks.append(_check("bodies", "pass" if bodies_ok else "fail", "required",
                         {"bodies": exp_bodies}, {"bodies": bodies}))
    if not bodies_ok:
        classes.add("shape_mismatch")

    # ── bbox ─────────────────────────────────────────────────────────────────
    bbox_tol = tol["bbox_mm"]
    exp_bbox = expected.get("bbox_mm")
    got_bbox = facts.get("bbox_mm")
    if exp_bbox and got_bbox:
        deltas = [abs(a - b) for a, b in zip(got_bbox, exp_bbox)]
        bbox_ok = all(d <= bbox_tol for d in deltas)
        checks.append(_check("bbox", "pass" if bbox_ok else "fail", "required",
                             {"per_axis_delta_mm": f"<= {bbox_tol}"},
                             {"expected_mm": exp_bbox, "actual_mm": got_bbox,
                              "delta_mm": deltas}))
        if not bbox_ok:
            classes.add("bbox_mismatch")
    else:
        checks.append(_check("bbox", "unmeasurable", "required", {}, {"bbox_mm": got_bbox}))
        classes.add("unmeasurable")

    # ── volume ───────────────────────────────────────────────────────────────
    vol_tol = tol["volume_rel"]
    exp_vol = expected.get("volume_mm3")
    got_vol = facts.get("volume_mm3")
    if exp_vol and got_vol is not None:
        rel = _rel(got_vol, exp_vol)
        vol_ok = rel <= vol_tol
        checks.append(_check("volume", "pass" if vol_ok else "fail", "required",
                             {"rel_diff": f"<= {vol_tol}"},
                             {"expected_mm3": exp_vol, "actual_mm3": got_vol, "rel_diff": rel}))
        if not vol_ok:
            classes.add("volume_mismatch")
    else:
        checks.append(_check("volume", "unmeasurable", "required", {}, {"volume_mm3": got_vol}))
        classes.add("unmeasurable")

    # ── surface deviation (§3.3) ─────────────────────────────────────────────
    if deviation is None:
        checks.append(_check("surface_deviation", "unmeasurable", "required",
                             {"frac_over_tol": f"<= {tol['frac_over_tol']}", "max_mm": f"<= {tol['max_mm']}"},
                             None))
        classes.add("unmeasurable")
    else:
        dev_ok = (deviation["frac_over_tol"] <= tol["frac_over_tol"]
                  and deviation["max_mm"] <= tol["max_mm"])
        checks.append(_check("surface_deviation", "pass" if dev_ok else "fail", "required",
                             {"frac_over_tol": f"<= {tol['frac_over_tol']}", "max_mm": f"<= {tol['max_mm']}"},
                             deviation))
        if not dev_ok:
            classes.add("shape_mismatch")

    # Diagnostic: deviation after aligning XY-center + z_min (§3.1). Never a
    # required check — it only helps read a coordinate_contract failure.
    if deviation_aligned is not None:
        checks.append(_check("deviation_after_align", "pass", "diagnostic",
                             {"note": "shape-only, placement removed"}, deviation_aligned))

    return _finalize(checks, classes)


def _finalize(checks: list[dict], classes: set[str]) -> dict:
    # unmeasurable = failure, always (§8 invariant 1).
    failed = any(c["state"] in ("fail", "unmeasurable") and c["level"] == "required"
                 for c in checks)
    primary = next((c for c in PRIORITY if c in classes), None)
    return {
        "checks": checks,
        "failure_classes": sorted(classes, key=lambda c: PRIORITY.index(c) if c in PRIORITY else 99),
        "primary_failure": primary,
        "verdict": "fail" if failed else "pass",
    }


# ── run-level grading (regrade a saved run for free — §2.8, §6.2) ────────────

import json
from pathlib import Path

from . import stats
from .schema import load_scenario

SAMPLES = 20000   # surface-deviation sample count per direction (§16.2)


def _deviation(ref_stl: Path, gen_stl: Path, tol: dict, seed: int):
    """(deviation, deviation_after_align) or (None, None) if either mesh can't
    be made watertight. Seed is the run's sampling seed (§6.3)."""
    from easycad_geom import mesh as gmesh
    from easycad_geom.compare import surface_deviation, deviation_after_align
    try:
        ref = gmesh.normalize(gmesh.load_mesh(ref_stl))
        gen = gmesh.normalize(gmesh.load_mesh(gen_stl))
        if not (ref.is_watertight and gen.is_watertight):
            return None, None
        dev = surface_deviation(gen, ref, tol_mm=tol["tol_mm"], samples=SAMPLES, seed=seed)
        aligned = deviation_after_align(gen, ref, tol_mm=tol["tol_mm"], samples=SAMPLES, seed=seed)
        return dev, aligned
    except Exception:  # noqa: BLE001
        return None, None


def _grade_turn_dir(sc, i: int, tdir: Path, seed: int) -> dict:
    """Grade one saved turn directory against its reference."""
    tol = sc.tolerances
    gen_json = tdir / "gen.json"
    gen_meta = json.loads(gen_json.read_text()) if gen_json.exists() else {}
    if gen_meta.get("error"):
        return grade_turn({"error": gen_meta["error"]}, {}, tol, None)

    facts_p = tdir / "facts.json"
    if not facts_p.exists():
        return grade_turn({"error": {"stage": "execute", "message": "no facts.json"}}, {}, tol, None)
    facts = json.loads(facts_p.read_text())
    if facts.get("error"):
        return grade_turn({"error": {"stage": "execute", "message": facts["error"]}}, {}, tol, None)

    expected = json.loads((sc.dir / "expected" / f"turn-{i}.json").read_text())
    ref_stl = sc.dir / "expected" / f"turn-{i}.stl"
    gen_stl = tdir / "out.stl"
    dev, aligned = (_deviation(ref_stl, gen_stl, tol, seed)
                    if (ref_stl.exists() and gen_stl.exists()) else (None, None))
    return grade_turn({"facts": facts}, expected, tol, dev, aligned)


def _open_result(verdict: str, primary, items) -> dict:
    return {"verdict": verdict, "primary_failure": primary,
            "turns": [{"turn": 1, "verdict": verdict, "primary_failure": primary,
                       "checks": items}]}


def _grade_open_attempt(sc, adir: Path, judge_run_id: str | None) -> dict:
    """Grade one open attempt from its cached vision verdict (§4, M1) — no judge
    call, so `grade` stays pure. Trust model: the FACT items (§8 invariants + the
    geometric checks) are RECOMPUTED from the current `facts.json` and never read
    from the cache, so a hand-edited fact `pass` can't slip an invalid model
    through; only the VISUAL verdicts are cache-trusted (they need the model), and
    only when the cache carries the manifest's current judge_run_id and matches the
    exact facts.json/out.stl it was judged on. A missing/unparseable/stale/wrong-id
    cache → 'unjudged'; a missing visual answer for a rubric line → fail-closed."""
    from .judge import compute_fact_items, sha256_or_none

    def unjudged(reason):
        return {"verdict": "unjudged", "primary_failure": reason, "turns": []}

    jp = adir / "judge.json"
    if not jp.exists():
        return {"verdict": "unjudged", "primary_failure": None, "turns": []}
    try:  # an untrusted artifact: never let a corrupt file abort the whole regrade
        j = json.loads(jp.read_text())
    except (ValueError, OSError):
        return unjudged("malformed_judge")
    if not isinstance(j, dict) or not isinstance(j.get("judge"), dict):
        return unjudged("malformed_judge")

    cache_id = j["judge"].get("judge_run_id")
    if not judge_run_id or not cache_id or cache_id != judge_run_id:
        return unjudged("stale_judge")

    tdir = adir / "turn-1"
    facts_p, stl_p = tdir / "facts.json", tdir / "out.stl"
    if not facts_p.exists() or not stl_p.exists():
        return _open_result("fail", "empty_result", [])   # no artifact → genuine fail
    try:
        facts = json.loads(facts_p.read_text())
    except (ValueError, OSError):
        return unjudged("malformed_judge")
    if not isinstance(facts, dict) or facts.get("error"):
        return _open_result("fail", "invalid_geometry", [])

    # The visual verdict was computed on a specific render → bind it to the exact
    # facts/STL it saw. A changed input (tampered or regenerated) makes it stale.
    inputs = j.get("inputs")
    if not isinstance(inputs, dict) or inputs.get("stl_sha") != sha256_or_none(stl_p) \
            or inputs.get("facts_sha") != sha256_or_none(facts_p):
        return unjudged("stale_judge")

    fact_items = compute_fact_items(facts, sc)   # deterministic, never from cache
    cached_visual = {it.get("text"): it for it in (j.get("items") or [])
                     if isinstance(it, dict) and it.get("kind") == "visual"}
    visual_items = []
    for line in sc.rubric:
        it = cached_visual.get(line)
        # A missing visual answer (render failure, or a stripped cache) fails closed.
        visual_items.append({"kind": "visual", "text": line,
                             "pass": bool(it.get("pass") is True) if isinstance(it, dict) else False,
                             "why": (it.get("why", "") if isinstance(it, dict) else "no visual answer")})

    items = fact_items + visual_items
    first_fail = next((x for x in items if not x["pass"]), None)
    verdict = "fail" if first_fail else "pass"
    primary = None if first_fail is None else (
        "fact_check_fail" if first_fail["kind"] == "fact" else "rubric_fail")
    return _open_result(verdict, primary, items)


def _grade_attempt(sc, adir: Path, seed: int) -> dict:
    """Grade every turn of an attempt; attempt passes iff all turns pass."""
    turns = []
    verdict = "pass"
    for i in range(1, len(sc.turns) + 1):
        tdir = adir / f"turn-{i}"
        if not tdir.exists():
            # A turn the attempt never reached (earlier turn failed) → fail.
            turns.append({"turn": i, "verdict": "fail", "primary_failure": "empty_result",
                          "failure_classes": ["empty_result"], "checks": []})
            verdict = "fail"
            continue
        g = _grade_turn_dir(sc, i, tdir, seed)
        g["turn"] = i
        turns.append(g)
        if g["verdict"] != "pass":
            verdict = "fail"
    primary = next((t["primary_failure"] for t in turns if t["verdict"] != "pass"), None)
    return {"verdict": verdict, "primary_failure": primary, "turns": turns}


def grade_run(run_dir: Path) -> dict:
    """Grade a whole run → verdict.json + results.jsonl + summary.json (§7)."""
    run_dir = Path(run_dir)
    manifest = json.loads((run_dir / "manifest.json").read_text()) if (run_dir / "manifest.json").exists() else {}
    seed = int(manifest.get("sampling_seed", 0))
    judge_run_id = (manifest.get("judge") or {}).get("judge_run_id")
    results_lines: list[str] = []
    scenario_results: list[dict] = []

    for sid in manifest.get("scenario_ids", []):
        sdir = run_dir / sid
        if (sdir / "skipped.json").exists():
            reason = json.loads((sdir / "skipped.json").read_text()).get("reason", "skipped")
            scenario_results.append({"id": sid, "verdict": reason, "spec": None,
                                     "primary_failure": None, "stability": None})
            continue
        sc = load_scenario(sid)
        attempts = sorted(sdir.glob("attempt-*"))
        graded = {}
        for adir in attempts:
            n = int(adir.name.split("-")[1])
            graded[n] = (_grade_open_attempt(sc, adir, judge_run_id) if sc.spec == "open"
                         else _grade_attempt(sc, adir, seed))
        first = graded.get(1)
        if first is None:
            # Selected but never attempted (e.g. a --max-cost partial run stopped
            # before reaching it). Surface it as not_run — never silently drop it,
            # or the pass_rate would be computed over an unadvertised subset.
            scenario_results.append({"id": sid, "spec": sc.spec, "verdict": "not_run",
                                     "primary_failure": None, "stability": None})
            continue
        (sdir / "verdict.json").write_text(json.dumps(graded, indent=2))
        stability = None
        if 2 in graded:
            stability = graded[2]["verdict"] == first["verdict"]
        scenario_results.append({
            "id": sid, "spec": sc.spec, "verdict": first["verdict"],
            "primary_failure": first["primary_failure"], "stability": stability,
        })
        for t in first["turns"]:
            results_lines.append(json.dumps({"scenario": sid, "turn": t["turn"],
                                             "verdict": t["verdict"],
                                             "primary_failure": t.get("primary_failure")}))

    (run_dir / "results.jsonl").write_text("\n".join(results_lines) + ("\n" if results_lines else ""))

    # pass_rate is computed over *attempted* complete scenarios only; not_run and
    # skipped scenarios are surfaced separately so a partial run can never inflate
    # (or deflate) the headline behind an unadvertised denominator.
    attempted = [r for r in scenario_results if r["spec"] == "complete" and r["verdict"] in ("pass", "fail")]
    passed = [r for r in attempted if r["verdict"] == "pass"]
    # Open scenarios grade via the vision judge (§4, M1), reported on their OWN
    # metric — never mixed into scenario_pass_rate, whose denominator is the
    # auto-graded complete set. 'unjudged' (no judge.json yet) is surfaced, not counted.
    open_att = [r for r in scenario_results if r["spec"] == "open" and r["verdict"] in ("pass", "fail")]
    open_passed = [r for r in open_att if r["verdict"] == "pass"]
    unjudged = [r["id"] for r in scenario_results if r["verdict"] == "unjudged"]
    skipped = [r for r in scenario_results if r["verdict"] not in ("pass", "fail") and r["spec"] is None]
    not_run = [r["id"] for r in scenario_results if r["verdict"] == "not_run"]
    stab = [r["stability"] for r in attempted if r["stability"] is not None]
    prim: dict[str, int] = {}
    for r in attempted:
        if r["verdict"] == "fail" and r["primary_failure"]:
            prim[r["primary_failure"]] = prim.get(r["primary_failure"], 0) + 1

    summary = {
        "run_id": manifest.get("run_id"),
        "backend": manifest.get("backend"),
        "mode": manifest.get("mode"),
        "model": (manifest.get("provenance") or {}).get("model_requested"),
        "status": manifest.get("status"),
        "cost_usd": manifest.get("cost_usd"),
        "artifact_integrity": manifest.get("artifact_integrity"),
        "product_metric_compliant": manifest.get("product_metric_compliant"),
        "scenario_pass_rate": {"k": len(passed), "n": len(attempted)},
        # EXPERIMENTAL automatic metric — NOT the SPEC's human `open_pass_rate`
        # (§5.4). Deliberately keyed distinctly so it can never masquerade as the
        # blind-review number; the human methodology remains canonical (§56).
        "open_pass_rate_judge": {"k": len(open_passed), "n": len(open_att)},
        "judge": manifest.get("judge") or None,
        "stability": {"same": sum(1 for s in stab if s), "n": len(stab)},
        "primary_failures": dict(sorted(prim.items(), key=lambda kv: -kv[1])),
        "skipped_unvalidated": [r["id"] for r in skipped],
        "unjudged": unjudged,
        "not_run": not_run,
        "scenarios": scenario_results,
    }
    (run_dir / "summary.json").write_text(json.dumps(summary, indent=2))
    return summary


def print_summary(summary: dict) -> None:
    sp = summary["scenario_pass_rate"]
    b = summary.get("backend", {}) or {}
    print()
    label = f"run {summary.get('run_id')} · {summary.get('mode')} · {b.get('name')}"
    if summary.get("model"):
        label += f" · {summary['model']}"
    print(label)
    if b.get("name") == "reference":
        print("  (reference backend — pipeline self-test, NOT a product measurement)")
    if summary.get("artifact_integrity") == "unverified-allowed":
        print("  ⚠ artifact integrity NOT enforced — NOT product-metric compliant")
    print(f"  scenario_pass_rate   {stats.format_rate(sp['k'], sp['n'])}")
    op = summary.get("open_pass_rate_judge") or {"k": 0, "n": 0}
    if op["n"]:
        jm = (summary.get("judge") or {}).get("model", "?")
        print(f"  open_pass_rate@judge {stats.format_rate(op['k'], op['n'])}  (judge: {jm})")
        print("  ⚠ EXPERIMENTAL auto metric — bench-SPEC §5.4 mandates human review; not the SPEC number")
    if summary.get("unjudged"):
        print(f"  unjudged             {len(summary['unjudged'])} open (run `bench judge`): "
              f"{', '.join(summary['unjudged'])}")
    st = summary["stability"]
    if st["n"]:
        print(f"  stability            {round(100 * st['same'] / st['n'])}%  (n={st['n']})")
    if summary["skipped_unvalidated"]:
        print(f"  skipped_unvalidated  {len(summary['skipped_unvalidated'])}: "
              f"{', '.join(summary['skipped_unvalidated'])}")
    if summary.get("not_run"):
        print(f"  not_run              {len(summary['not_run'])} (partial run): "
              f"{', '.join(summary['not_run'])}")
    if summary.get("status") == "partial":
        print("  ⚠ PARTIAL run — pass_rate is over attempted scenarios only")
    if summary["primary_failures"]:
        parts = " · ".join(f"{k} {v}" for k, v in summary["primary_failures"].items())
        print(f"  primary_failure:     {parts}")
    if summary.get("cost_usd"):
        print(f"                       total ${summary['cost_usd']:.2f}")


def cmd_grade(args) -> int:
    summary = grade_run(Path(args.run))
    print_summary(summary)
    return 0
