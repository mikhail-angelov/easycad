"""`bench spec` — execute references, write `expected/` (bench-SPEC §4.2).

Expected facts are measured from the reference, so they can never disagree with
it. The reference becomes *trusted* only after human visual validation
(`bench validate`, §4.3) — building the numbers here does not make them true.
"""

from __future__ import annotations

import importlib.metadata as md
import json
from pathlib import Path

from easycad_geom.facts import TESSELLATION, compute_facts

from . import paths, render
from .cadexec import CadError, run_and_export
from .schema import Scenario, load_scenario, sha256_file


def _toolchain() -> dict:
    def ver(pkg):
        try:
            return md.version(pkg)
        except Exception:  # noqa: BLE001
            return "unknown"
    return {
        "cadquery": ver("cadquery"),
        "OCP": ver("cadquery-ocp") if ver("cadquery-ocp") != "unknown" else ver("OCP"),
        "trimesh": ver("trimesh"),
        "tessellation": TESSELLATION,
    }


def build_expected(scenario: Scenario, do_render: bool = True) -> list[Path]:
    """Build `expected/turn-N.*` for every turn. Returns the json paths."""
    exp = scenario.dir / "expected"
    exp.mkdir(exist_ok=True)
    written: list[Path] = []
    for i, turn in enumerate(scenario.turns, 1):
        ref_path = scenario.dir / turn.reference
        step = exp / f"turn-{i}.step"
        stl = exp / f"turn-{i}.stl"
        run_and_export(ref_path.read_text(), step, stl)
        facts = compute_facts(step, stl)
        doc = {
            "generated_by": "bench spec",
            "turn": i,
            "reference_sha256": sha256_file(ref_path),
            "toolchain": _toolchain(),
            "bbox_mm": facts.get("bbox_mm"),
            "volume_mm3": facts.get("volume_mm3"),
            "solids": facts.get("solids"),
            "bodies": facts.get("bodies"),
            "valid": facts.get("valid"),
            "watertight": facts.get("watertight"),
            "coordinate_contract": facts.get("coordinate_contract"),
            "artifacts": {
                "step": {"file": step.name, "sha256": sha256_file(step)},
                "stl": {"file": stl.name, "sha256": sha256_file(stl)},
            },
        }
        out = exp / f"turn-{i}.json"
        out.write_text(json.dumps(doc, indent=2))
        written.append(out)
        if do_render:
            render.render_stl(stl, exp / f"turn-{i}" / "renders")
    return written


def cmd_spec(args) -> int:
    if args.check:
        return _check_all(require_validation=getattr(args, "require_validation", False))
    ids = [args.scenario] if args.scenario else _complete_ids()
    for sid in ids:
        sc = load_scenario(sid)
        if not sc.is_complete:
            print(f"skip {sid}: open scenario, no reference to build")
            continue
        try:
            build_expected(sc, do_render=not args.no_render)
            print(f"✓ {sid}: built expected/ for {len(sc.turns)} turn(s) — pending_validation")
        except CadError as exc:
            print(f"✗ {sid}: reference failed — {exc}")
            return 1
    return 0


def _complete_ids() -> list[str]:
    from .schema import all_scenario_ids
    return [s for s in all_scenario_ids() if load_scenario(s).is_complete]


# Fact drift tolerances for `--check`: tessellation is fixed, so meshes are
# reproducible, but tiny float noise across OCCT builds is expected.
_ABS = 1e-3
_REL = 1e-4


def _close(a, b) -> bool:
    if a is None or b is None:
        return a == b
    if isinstance(a, list):
        return len(a) == len(b) and all(_close(x, y) for x, y in zip(a, b))
    if isinstance(a, (int, float)):
        return abs(a - b) <= _ABS + _REL * abs(b)
    return a == b


def _check_all(require_validation: bool = False) -> int:
    """CI gate: rebuild every complete scenario and fail on drift (§4.2).

    With `require_validation` (a release gate, not the default PR gate), also
    fail any complete scenario that lacks a current human acceptance — the §4.3
    enforcement otherwise lives in the `bench run` gate (skipped_unvalidated)."""
    from .schema import all_scenario_ids
    from .validate import is_validated
    ok = True
    for sid in all_scenario_ids():
        sc = load_scenario(sid)
        if not sc.is_complete:
            continue
        if require_validation and not is_validated(sc):
            print(f"✗ {sid}: no current validation.json — run `bench validate {sid} --accept` (§4.3)")
            ok = False
        exp = sc.dir / "expected"
        for i, turn in enumerate(sc.turns, 1):
            committed = exp / f"turn-{i}.json"
            if not committed.exists():
                print(f"✗ {sid} turn {i}: expected/turn-{i}.json missing — run `bench spec {sid}`")
                ok = False
                continue
            old = json.loads(committed.read_text())
            import tempfile
            with tempfile.TemporaryDirectory() as tmp:
                step = Path(tmp) / "check.step"
                stl = Path(tmp) / "check.stl"
                try:
                    run_and_export((sc.dir / turn.reference).read_text(), step, stl)
                    new = compute_facts(step, stl)
                except CadError as exc:
                    print(f"✗ {sid} turn {i}: reference no longer runs — {exc}")
                    ok = False
                    continue
            for key in ("bbox_mm", "volume_mm3", "solids", "bodies", "valid", "watertight"):
                if not _close(old.get(key), new.get(key)):
                    print(f"✗ {sid} turn {i}: {key} drifted {old.get(key)} → {new.get(key)}")
                    ok = False
            if old.get("reference_sha256") != sha256_file(sc.dir / turn.reference):
                print(f"✗ {sid} turn {i}: reference-{i}.py changed but expected/ not rebuilt")
                ok = False
            # The committed STEP/STL are trusted ground truth — the reference STL
            # is the mesh grading compares against. Verify each on disk still
            # matches the sha recorded in turn-N.json, so a tampered, corrupt or
            # missing artifact can't pass CI and then be graded against.
            artifacts = old.get("artifacts", {})
            for kind, fname in (("step", f"turn-{i}.step"), ("stl", f"turn-{i}.stl")):
                f = exp / fname
                want = (artifacts.get(kind) or {}).get("sha256")
                if not f.exists():
                    print(f"✗ {sid} turn {i}: committed {fname} is missing")
                    ok = False
                elif not want:
                    # The recorded sha is mandatory — without it there is nothing
                    # to verify against, so a missing hash fails closed (§6.1).
                    print(f"✗ {sid} turn {i}: artifacts.{kind}.sha256 missing from "
                          f"turn-{i}.json — rebuild with `bench spec {sid}`")
                    ok = False
                elif sha256_file(f) != want:
                    print(f"✗ {sid} turn {i}: {fname} sha256 ≠ expected/turn-{i}.json (tampered/corrupt)")
                    ok = False
        # Invariant 9 (§4.3): a reference edited *after* human acceptance must
        # not pass CI silently — the recorded validation hash must still match.
        if not _validation_in_sync(sc):
            ok = False
    if ok:
        # Be precise about what passed: without --require-validation, absent
        # validation.json is allowed (the run gate handles it), so don't imply
        # acceptance was checked when it wasn't.
        if require_validation:
            print("✓ bench spec --check: references + artifacts in sync and human-validated")
        else:
            print("✓ bench spec --check: references + artifacts in sync "
                  "(validation not required; pass --require-validation to enforce)")
    return 0 if ok else 1


def _validation_in_sync(sc) -> bool:
    """False if any turn was accepted in validation.json against a reference that
    has since changed (a silent post-acceptance edit). Absent validation.json is
    not a --check failure — the `bench run` gate handles unvalidated scenarios as
    skipped_unvalidated."""
    from .validate import _validation_path
    vp = _validation_path(sc)
    if not vp.exists():
        return True
    try:
        doc = json.loads(vp.read_text())
    except Exception:  # noqa: BLE001
        print(f"✗ {sc.id}: validation.json is not valid JSON")
        return False
    ok = True
    for entry in doc.get("per_turn", []):
        i = entry.get("turn")
        if not entry.get("accepted") or not (0 < i <= len(sc.turns)):
            continue
        current = sha256_file(sc.dir / sc.turns[i - 1].reference)
        if entry.get("reference_sha256") != current:
            print(f"✗ {sc.id} turn {i}: reference edited after acceptance — "
                  f"re-run `bench validate {sc.id} --accept` (§4.3)")
            ok = False
    return ok
