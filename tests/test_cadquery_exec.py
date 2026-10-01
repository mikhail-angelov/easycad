"""Tests for the subprocess-isolated CadQuery executor.

Requires cadquery (run with the .venv-poc interpreter).
"""

import base64
import re
import struct

import pytest

from app import cadquery_exec
from app.cadquery_exec import append_geometry_block, execute, strip_geometry_block


def test_execute_simple_box():
    code = "import cadquery as cq\nresult = cq.Workplane('XY').box(50, 80, 30)\n"
    res = execute(code)
    assert res.success, res.error
    assert res.stl_base64
    # Valid binary STL is comfortably larger than a few hundred bytes.
    assert len(base64.b64decode(res.stl_base64)) > 200
    assert "Bounding box" in res.geometry_info
    assert "Size: 50.0 x 80.0 x 30.0 mm" in res.geometry_info
    assert res.geometry_info.startswith("# ── Geometry info")


def test_execute_exposes_tessellated_cad_faces_for_selection():
    res = execute("import cadquery as cq\nresult = cq.Workplane('XY').box(50, 80, 30)\n")
    assert res.success, res.error
    mesh = res.face_mesh
    assert mesh is not None
    assert len(mesh["faces"]) == 6
    assert len(mesh["positions"]) == 72  # 6 planar faces × 4 vertices × XYZ
    assert len(mesh["indices"]) == 36    # 6 planar faces × 2 triangles × 3 indices
    assert [face["label"] for face in mesh["faces"]] == list("ABCDEF")
    assert all(face["planar"] and face["count"] == 6 for face in mesh["faces"])
    assert all(face["center"] is not None and face["normal"] is not None for face in mesh["faces"])
    top = next(face for face in mesh["faces"] if face["normal"][2] > 0.9)
    assert top["size"] == pytest.approx([50, 80])


def test_face_mesh_uses_an_interior_reference_point_when_a_planar_face_has_a_hole():
    res = execute(
        "import cadquery as cq\n"
        "result = cq.Workplane('XY').circle(10).circle(5).extrude(3)\n"
    )
    assert res.success, res.error
    top = next(face for face in res.face_mesh["faces"] if face["planar"] and face["normal"][2] > 0.9)
    assert top["center"] is not None
    assert top["center"][0] ** 2 + top["center"][1] ** 2 > 25


def test_face_mesh_keeps_engraved_planar_surfaces_selectable():
    res = execute(
        "import cadquery as cq\n"
        "result = cq.Workplane('XY').box(50, 30, 5).faces('>Z').workplane()"
        ".text('A', 10, -1, combine='cut')\n"
    )
    assert res.success, res.error
    top_faces = [
        face for face in res.face_mesh["faces"]
        if face["planar"] and face["normal"][2] > 0.9
    ]
    assert top_faces
    assert all(face["center"] is not None for face in top_faces)


def test_execute_missing_result():
    res = execute("import cadquery as cq\nx = cq.Workplane('XY').box(1, 1, 1)\n")
    assert not res.success
    assert "no 'result'" in res.error


@pytest.mark.parametrize("result_expr", [
    "a.add(b)",
    "cq.Compound.makeCompound([a.val(), b.val()])",
])
def test_geometry_info_covers_every_exported_body(result_expr):
    code = (
        "import cadquery as cq\n"
        "a = cq.Workplane('XY').box(10, 20, 30, centered=False)\n"
        "b = cq.Workplane('XY').box(5, 10, 40, centered=False).translate((25, 0, 0))\n"
        f"result = {result_expr}\n"
    )
    res = execute(code)
    assert res.success, res.error

    # Check the exported geometry independently, not just a comment claiming it.
    raw = base64.b64decode(res.stl_base64)
    vertices = []
    for triangle in struct.iter_unpack("<12fH", raw[84:]):
        vertices.extend((triangle[3:6], triangle[6:9], triangle[9:12]))
    assert tuple(min(v[i] for v in vertices) for i in range(3)) == (0, 0, 0)
    assert tuple(max(v[i] for v in vertices) for i in range(3)) == (30, 20, 40)
    bbox_line = next(line for line in res.geometry_info.splitlines() if "Bounding box:" in line)
    bounds = tuple(float(n) for n in re.findall(r"-?\d+\.\d+", bbox_line))
    assert bounds == (0, 30, 0, 20, 0, 40)
    assert "Size: 30.0 x 20.0 x 40.0 mm" in res.geometry_info
    assert "Topology: 2 solid(s), 12 faces, 24 edges" in res.geometry_info


@pytest.mark.parametrize("model,size,solids", [
    (
        "cq.Workplane('XY').box(80, 50, 4).faces('>Z').workplane()"
        ".pushPoints([(-25, -15), (25, -15), (-25, 15), (25, 15)]).hole(5)",
        "80.0 x 50.0 x 4.0", 1,
    ),
    (
        "cq.Workplane('XY').circle(10).circle(5).extrude(3)"
        ".add(cq.Workplane('XY').circle(10).circle(5).extrude(3).translate((30, 0, 10)))",
        "50.0 x 20.0 x 13.0", 2,
    ),
    (
        "cq.Workplane('XY').box(40, 30, 20).faces('>Z').workplane()"
        ".rect(36, 26).cutBlind(-18)"
        ".add(cq.Workplane('XY').box(40, 30, 2).translate((60, 0, 0)))",
        "100.0 x 30.0 x 20.0", 2,
    ),
    ("cq.Workplane('XY').box(12, 15, 18).val()", "12.0 x 15.0 x 18.0", 1),
])
def test_geometry_info_for_different_part_families(model, size, solids):
    res = execute(f"import cadquery as cq\nresult = {model}\n")
    assert res.success, res.error
    assert f"Size: {size} mm" in res.geometry_info
    assert f"Topology: {solids} solid(s)," in res.geometry_info


def test_execute_syntax_error():
    res = execute("this is not python")
    assert not res.success
    assert res.error


def test_geometry_block_is_replaced_not_duplicated():
    code = "import cadquery as cq\nresult = cq.Workplane('XY').box(10, 10, 10)\n"
    once = append_geometry_block(code, "# ── Geometry info (auto-generated, do not edit) ──\n# x")
    twice = append_geometry_block(once, "# ── Geometry info (auto-generated, do not edit) ──\n# y")
    assert twice.count("# ── Geometry info") == 1
    assert strip_geometry_block(twice).endswith("box(10, 10, 10)")


def test_timeout_short(monkeypatch):
    monkeypatch.setattr(cadquery_exec, "TIMEOUT_SECONDS", 1)
    slow = "import time\ntime.sleep(5)\nresult = None\n"
    res = execute(slow)
    assert not res.success
    assert "timed out" in res.error.lower()
