"""Golden fixtures for the measurers (bench-SPEC §13, P0).

Known-answer geometry: if these drift, every downstream number is suspect.
"""

import math


def test_box_facts(build):
    facts, _ = build(
        "import cadquery as cq\n"
        "result = cq.Workplane('XY').box(10, 20, 30, centered=(True, True, False))\n"
    )
    assert facts["bbox_mm"] == [10.0, 20.0, 30.0]
    assert math.isclose(facts["volume_mm3"], 6000.0, rel_tol=1e-4)
    assert facts["solids"] == 1
    assert facts["bodies"] == 1
    assert facts["valid"] is True
    assert facts["watertight"] is True


def test_coordinate_contract_ok(build):
    facts, _ = build(
        "import cadquery as cq\n"
        "result = cq.Workplane('XY').box(10, 10, 10, centered=(True, True, False))\n"
    )
    cc = facts["coordinate_contract"]
    assert cc["ok"] is True
    assert math.isclose(cc["z_min_mm"], 0.0, abs_tol=1e-6)


def test_coordinate_contract_off_ground(build):
    # Sitting 5 mm above Z=0 → contract violated (z_min ≠ 0).
    facts, _ = build(
        "import cadquery as cq\n"
        "result = cq.Workplane('XY').box(10, 10, 10).translate((0, 0, 10))\n"
    )
    cc = facts["coordinate_contract"]
    assert cc["ok"] is False
    assert cc["z_min_mm"] > 1.0


def test_coordinate_contract_off_center(build):
    # Shifted 20 mm in X → xy center far from origin.
    facts, _ = build(
        "import cadquery as cq\n"
        "result = cq.Workplane('XY').box(10, 10, 10, centered=(True, True, False))"
        ".translate((20, 0, 0))\n"
    )
    assert facts["coordinate_contract"]["ok"] is False


def test_two_disconnected_bodies(build):
    # Two separated boxes → bodies == 2 (the disconnected-body signal, §3.2).
    facts, _ = build(
        "import cadquery as cq\n"
        "a = cq.Workplane('XY').box(10, 10, 10, centered=(True, True, False))\n"
        "b = cq.Workplane('XY').box(10, 10, 10, centered=(True, True, False))"
        ".translate((30, 0, 0))\n"
        "result = a.union(b, clean=False)\n"
    )
    assert facts["bodies"] == 2


def test_hollow_box_is_one_body(build):
    # Open-top box exports as an OCCT shell (solids==0) but is one connected
    # body — the fact grading relies on (§3.2).
    facts, _ = build(
        "import cadquery as cq\n"
        "result = cq.Workplane('XY').box(40, 30, 20, centered=(True, True, False))"
        ".faces('>Z').shell(-2)\n"
    )
    assert facts["bodies"] == 1
    assert facts["watertight"] is True
