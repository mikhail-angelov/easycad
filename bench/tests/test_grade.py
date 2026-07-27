"""Grader taxonomy + the §3.3 defect table (bench-SPEC §5, §13, P0).

The harness is only worth its number if it *fails* bad models. These build the
scenario-001 reference and deliberately-broken variants and assert the verdict.
"""

import pytest

from bench.grade import classify_error, grade_turn
from bench.schema import DEFAULT_TOLERANCES
from easycad_geom.compare import surface_deviation

TOL = DEFAULT_TOLERANCES

REF = (
    "import cadquery as cq\n"
    "result = (cq.Workplane('XY').box(60, 40, 5, centered=(True, True, False))\n"
    "  .faces('>Z').workplane().rect(44, 24, forConstruction=True).vertices().hole(5.5))\n"
)


@pytest.fixture
def ref(build, load_mesh):
    facts, stl = build(REF)
    return facts, load_mesh(stl)


def _grade(gen_facts, gen_mesh, ref_facts, ref_mesh):
    dev = surface_deviation(gen_mesh, ref_mesh, tol_mm=TOL["tol_mm"], samples=8000)
    return grade_turn({"facts": gen_facts}, ref_facts, TOL, dev)


def test_identical_passes(ref, build, load_mesh):
    ref_facts, ref_mesh = ref
    gen_facts, stl = build(REF)
    g = _grade(gen_facts, load_mesh(stl), ref_facts, ref_mesh)
    assert g["verdict"] == "pass", g["failure_classes"]


def test_missing_holes_fail_shape(ref, build, load_mesh):
    ref_facts, ref_mesh = ref
    gen_facts, stl = build(
        "import cadquery as cq\n"
        "result = cq.Workplane('XY').box(60, 40, 5, centered=(True, True, False))\n"
    )
    g = _grade(gen_facts, load_mesh(stl), ref_facts, ref_mesh)
    assert g["verdict"] == "fail"
    # Missing material trips both volume (>3%) and surface deviation; volume
    # outranks shape in the priority order, so it is primary. Both must fire.
    assert g["primary_failure"] == "volume_mismatch"
    assert "shape_mismatch" in g["failure_classes"]
    dev = next(c for c in g["checks"] if c["check"] == "surface_deviation")
    assert dev["actual"]["frac_over_tol"] > 0.005      # §3.3: no holes ≈ 5.8%


def test_shifted_holes_fail_shape(ref, build, load_mesh):
    ref_facts, ref_mesh = ref
    # Holes present but the rectangle is shifted 2 mm in X.
    gen_facts, stl = build(
        "import cadquery as cq\n"
        "result = (cq.Workplane('XY').box(60, 40, 5, centered=(True, True, False))\n"
        "  .faces('>Z').workplane().center(2, 0)\n"
        "  .rect(44, 24, forConstruction=True).vertices().hole(5.5))\n"
    )
    g = _grade(gen_facts, load_mesh(stl), ref_facts, ref_mesh)
    assert g["verdict"] == "fail"
    assert "shape_mismatch" in g["failure_classes"]


def test_wrong_bbox_fail_bbox(ref, build, load_mesh):
    ref_facts, ref_mesh = ref
    gen_facts, stl = build(
        "import cadquery as cq\n"
        "result = (cq.Workplane('XY').box(64, 40, 5, centered=(True, True, False))\n"
        "  .faces('>Z').workplane().rect(44, 24, forConstruction=True).vertices().hole(5.5))\n"
    )
    g = _grade(gen_facts, load_mesh(stl), ref_facts, ref_mesh)
    assert g["verdict"] == "fail"
    # bbox outranks shape in the priority order (§5.2).
    assert g["primary_failure"] == "bbox_mismatch"


# ── pure-function taxonomy (fast, no geometry) ───────────────────────────────

def _facts(**over):
    base = {"valid": True, "watertight": True, "bodies": 1, "bbox_mm": [60, 40, 5],
            "volume_mm3": 11525.0,
            "coordinate_contract": {"ok": True, "z_min_mm": 0.0, "xy_center_mm": [0, 0]}}
    base.update(over)
    return base


EXP = {"bbox_mm": [60, 40, 5], "volume_mm3": 11525.0, "bodies": 1,
       "valid": True, "watertight": True}
GOOD_DEV = {"frac_over_tol": 0.0, "max_mm": 0.1, "p99_mm": 0.05, "tol_mm": 0.5, "samples": 10}


def test_invalid_geometry_primary():
    g = grade_turn({"facts": _facts(valid=False)}, EXP, TOL, GOOD_DEV)
    assert g["primary_failure"] == "invalid_geometry"


def test_not_watertight_primary():
    g = grade_turn({"facts": _facts(watertight=False)}, EXP, TOL, None)
    assert g["primary_failure"] == "not_watertight"


def test_coordinate_contract_primary():
    cc = {"ok": False, "z_min_mm": 12.0, "xy_center_mm": [0, 0]}
    g = grade_turn({"facts": _facts(coordinate_contract=cc)}, EXP, TOL, GOOD_DEV)
    assert g["primary_failure"] == "coordinate_contract_violation"


def test_unmeasurable_is_failure():
    # No deviation available → surface check unmeasurable → verdict fail (§8.1).
    g = grade_turn({"facts": _facts()}, EXP, TOL, None)
    assert g["verdict"] == "fail"
    assert "unmeasurable" in g["failure_classes"]


def test_generation_error_taxonomy():
    g = grade_turn({"error": {"stage": "generate", "message": "provider 500"}}, EXP, TOL, None)
    assert g["primary_failure"] == "generation_error"


def test_deviation_after_align_is_diagnostic_only():
    # A shape identical to the reference but shifted fails the coordinate
    # contract, yet deviation_after_align should read ~0 (right shape, wrong
    # place) and must NOT change the verdict on its own.
    cc = {"ok": False, "z_min_mm": 10.0, "xy_center_mm": [0, 0]}
    aligned = {"frac_over_tol": 0.0, "max_mm": 0.05, "p99_mm": 0.02,
               "tol_mm": 0.5, "samples": 10, "applied_shift_mm": [0, 0, -10]}
    g = grade_turn({"facts": _facts(coordinate_contract=cc)}, EXP, TOL, GOOD_DEV, aligned)
    diag = next(c for c in g["checks"] if c["check"] == "deviation_after_align")
    assert diag["level"] == "diagnostic"
    assert diag["state"] == "pass"           # diagnostics never fail the turn
    # Verdict is still a failure — but because of the contract, not the shape.
    assert g["primary_failure"] == "coordinate_contract_violation"


@pytest.mark.parametrize("stage,msg,expect", [
    ("generate", "anything", "generation_error"),
    ("execute", "Execution timed out after 120s", "timeout"),
    ("execute", "Code executed but no 'result' variable", "empty_result"),
    ("execute", "export: StdFail_NotDone", "occt_error"),
    ("execute", "TypeError: bad", "code_error"),
    ("execute", "Worker unavailable: connection refused", "generation_error"),
])
def test_classify_error(stage, msg, expect):
    assert classify_error(stage, msg) == expect
