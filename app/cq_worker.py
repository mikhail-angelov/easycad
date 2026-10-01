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
import math
import sys


def _planar_size(vertices, normal: tuple[float, float, float]) -> list[float]:
    """Return the extents of a tessellated planar face along its own axes."""
    reference = (0.0, 0.0, 1.0) if abs(normal[2]) < 0.9 else (0.0, 1.0, 0.0)
    u = (
        reference[1] * normal[2] - reference[2] * normal[1],
        reference[2] * normal[0] - reference[0] * normal[2],
        reference[0] * normal[1] - reference[1] * normal[0],
    )
    length = math.sqrt(sum(value * value for value in u))
    u = tuple(value / length for value in u)
    v = (
        normal[1] * u[2] - normal[2] * u[1],
        normal[2] * u[0] - normal[0] * u[2],
        normal[0] * u[1] - normal[1] * u[0],
    )
    projected = [
        (sum(a * b for a, b in zip(vertex.toTuple(), u)), sum(a * b for a, b in zip(vertex.toTuple(), v)))
        for vertex in vertices
    ]
    return [max(axis) - min(axis) for axis in zip(*projected)]


def get_face_mesh(shape) -> dict | None:
    """Return a bounded, CAD-coordinate display mesh grouped by source face.

    It is intentionally built before STL serialization: an STL triangle index
    has no stable relationship to a BRep face. Selection is optional UI data;
    failure or a very large model must not turn a valid export into a failure.
    """
    faces = shape.Faces()
    if len(faces) > 500:
        return None
    positions: list[float] = []
    indices: list[int] = []
    records: list[dict] = []
    for face_id, face in enumerate(faces):
        vertices, triangles = face.tessellate(0.1, 0.1)
        if not triangles or len(indices) + len(triangles) * 3 > 50_000:
            return None
        offset = len(positions) // 3
        start = len(indices)
        positions.extend(coordinate for vertex in vertices for coordinate in vertex.toTuple())
        indices.extend(offset + vertex for triangle in triangles for vertex in triangle)

        # The centroid of a tessellated triangle lies within the trimmed face,
        # unlike the area centre of a face with holes or a concave face.
        tri = triangles[0]
        anchor = (vertices[tri[0]] + vertices[tri[1]] + vertices[tri[2]]) / 3
        planar = face.geomType() == "PLANE"
        normal = face.normalAt(anchor).toTuple() if planar else None
        label, number = "", face_id + 1
        while number:
            number, digit = divmod(number - 1, 26)
            label = chr(65 + digit) + label
        records.append({
            "id": face_id,
            "label": label,
            "start": start,
            "count": len(indices) - start,
            "planar": planar,
            "anchor": anchor.toTuple(),
            # Kept as `center` for the client contract, but this is a verified
            # interior reference point rather than the geometric area centre.
            "center": anchor.toTuple() if planar else None,
            "normal": normal,
            "size": _planar_size(vertices, normal) if normal else None,
        })
    return {"positions": positions, "indices": indices, "faces": records}


def get_geometry_info(shape) -> str:
    """Measure the complete shape passed to the exporter, not one stack item."""
    try:
        bb = shape.BoundingBox()
        lines = [
            "# ── Geometry info (auto-generated, do not edit) ──",
            f"# Bounding box: X: {bb.xmin:.1f}..{bb.xmax:.1f}, "
            f"Y: {bb.ymin:.1f}..{bb.ymax:.1f}, Z: {bb.zmin:.1f}..{bb.zmax:.1f}",
            f"# Size: {bb.xmax - bb.xmin:.1f} x {bb.ymax - bb.ymin:.1f} "
            f"x {bb.zmax - bb.zmin:.1f} mm",
        ]
        n_faces = len(shape.Faces())
        n_edges = len(shape.Edges())
        n_solids = len(shape.Solids())
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

        # CadQuery exports every item of a Workplane/iterable as a compound.
        # Materialise it once so export and feedback describe the same geometry.
        shape = result if isinstance(result, cq.Shape) else cq.Compound.makeCompound(result)
        cq.exporters.export(shape, export_path)
        info = get_geometry_info(shape)
    except Exception as exc:  # noqa: BLE001
        return {"success": False, "geometry_info": None, "error": f"Export error: {_describe(exc)}"}

    face_mesh = None
    if export_path.lower().endswith(".stl"):
        try:
            face_mesh = get_face_mesh(shape)
        except Exception:  # selection is optional; STL remains the fallback
            pass
    return {"success": True, "geometry_info": info, "face_mesh": face_mesh, "error": None}


def main() -> None:
    job = json.load(sys.stdin)
    code = job["code"]
    # Format is inferred from the extension; `stl_path` kept for back-compat.
    export_path = job.get("export_path") or job["stl_path"]
    _emit(execute_job(code, export_path))


if __name__ == "__main__":
    main()
