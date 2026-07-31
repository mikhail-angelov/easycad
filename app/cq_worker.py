"""Isolated CadQuery execution worker — invoked as a subprocess.

Reads one JSON job from stdin: {"code": str, "export_path": str} (or the legacy
key "stl_path"). Executes the code, exports the `result` Workplane to that path —
the FORMAT is inferred from the file extension (`.stl`, `.step`) by
`cq.exporters.export` — and computes a geometry-info comment block. Writes a
single JSON line to stdout: {"success": bool, "geometry_info": str|None,
"error": str|None}; the parent reads the exported file itself.

Running in a child process means a CadQuery/OCP segfault or hang can't take
down the API server — the parent just observes a non-zero exit or timeout.
"""

import json
import sys


def get_geometry_info(result) -> str:
    """Build the auto-generated geometry-info comment block from a result."""
    try:
        bb = result.val().BoundingBox()
        lines = [
            "# ── Geometry info (auto-generated, do not edit) ──",
            f"# Bounding box: X: {bb.xmin:.1f}..{bb.xmax:.1f}, "
            f"Y: {bb.ymin:.1f}..{bb.ymax:.1f}, Z: {bb.zmin:.1f}..{bb.zmax:.1f}",
            f"# Size: {bb.xmax - bb.xmin:.1f} x {bb.ymax - bb.ymin:.1f} "
            f"x {bb.zmax - bb.zmin:.1f} mm",
        ]
        solid = result.val()
        n_faces = len(solid.Faces())
        n_edges = len(solid.Edges())
        n_solids = len(solid.Solids()) if hasattr(solid, "Solids") else 1
        lines.append(f"# Topology: {n_solids} solid(s), {n_faces} faces, {n_edges} edges")
        return "\n".join(lines)
    except Exception:
        return "# ── Geometry info: could not extract ──"


def _emit(payload: dict) -> None:
    sys.stdout.write(json.dumps(payload) + "\n")
    sys.stdout.flush()


def _describe(exc: Exception) -> str:
    """Human-readable error, including the exception type.

    Some CadQuery/generated-code failures carry an object (e.g. a Workplane) as
    the exception message, whose str() is an unhelpful `<... object at 0x...>`.
    Prefixing the type name keeps the message meaningful in those cases.
    """
    detail = str(exc).strip()
    name = type(exc).__name__
    if not detail or detail.startswith("<"):
        return name
    return f"{name}: {detail}"


def execute_job(code: str, export_path: str) -> dict:
    """Execute `code`, export the `result` Workplane to `export_path` (format
    inferred from the extension), and return {success, geometry_info, error}.

    This is the single execution core shared by every path: the fresh-subprocess
    runner (`main`, via `python -m cq_worker`) and the SPEC18 zygote child
    (`worker/zygote.py`, in-process after fork). Keeping one function guarantees
    local and worker geometry/STL logic cannot silently diverge.
    """
    namespace: dict = {}
    try:
        exec(code, namespace)
    except Exception as exc:  # noqa: BLE001 — surface any user-code error verbatim
        return {"success": False, "geometry_info": None, "error": f"Execution error: {_describe(exc)}"}

    result = namespace.get("result")
    if result is None:
        return {
            "success": False,
            "geometry_info": None,
            "error": "Code executed but no 'result' variable was defined.",
        }

    try:
        import cadquery as cq

        cq.exporters.export(result, export_path)
        info = get_geometry_info(result)
    except Exception as exc:  # noqa: BLE001
        return {"success": False, "geometry_info": None, "error": f"Export error: {_describe(exc)}"}

    return {"success": True, "geometry_info": info, "error": None}


def main() -> None:
    job = json.load(sys.stdin)
    code = job["code"]
    # Format is inferred from the extension; `stl_path` kept for back-compat.
    export_path = job.get("export_path") or job["stl_path"]
    _emit(execute_job(code, export_path))


if __name__ == "__main__":
    main()
