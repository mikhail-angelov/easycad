"""Tests for the subprocess-isolated CadQuery executor.

Requires cadquery (run with the .venv-poc interpreter).
"""

from app import cadquery_exec
from app.cadquery_exec import append_geometry_block, execute, strip_geometry_block


def test_execute_simple_box():
    code = "import cadquery as cq\nresult = cq.Workplane('XY').box(50, 80, 30)\n"
    res = execute(code)
    assert res.success, res.error
    assert res.stl_base64
    # Valid binary STL is comfortably larger than a few hundred bytes.
    import base64

    assert len(base64.b64decode(res.stl_base64)) > 200
    assert "Bounding box" in res.geometry_info
    assert "Size: 50.0 x 80.0 x 30.0 mm" in res.geometry_info
    assert res.code_with_geometry.startswith("import cadquery as cq")
    assert "# ── Geometry info" in res.code_with_geometry


def test_execute_missing_result():
    res = execute("import cadquery as cq\nx = cq.Workplane('XY').box(1, 1, 1)\n")
    assert not res.success
    assert "no 'result'" in res.error


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
