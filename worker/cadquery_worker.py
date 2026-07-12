#!/usr/bin/env python3
from __future__ import annotations

import json
import math
import sys
import time
import traceback
from pathlib import Path


def main() -> int:
    job_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path.cwd()
    job_path = job_dir / "job.json"
    started = time.time()
    source = ""
    try:
        import cadquery as cq
    except Exception as exc:
        _write_error(job_dir, "worker_import", type(exc).__name__, str(exc), started)
        return 2

    try:
        job = json.loads(job_path.read_text(encoding="utf-8"))
        parameters = job["parameters"]
        source = job["source"]
        fmt = job.get("format", "stl")
        namespace = {
            "cq": cq,
            "PARAMETERS": parameters,
            "math": math,
            "int": int,
            "__builtins__": {},
        }
        exec(compile(source, "generated_model.py", "exec"), namespace, namespace)
        result = namespace.get("result")
        if result is None:
            raise ValueError("Generated source did not assign result")

        shape = _shape_from_result(result)
        bbox = shape.BoundingBox()
        volume = float(shape.Volume())
        if volume <= 0:
            raise ValueError("Generated model has zero volume")

        if fmt == "step":
            artifact = job_dir / "model.step"
            cq.exporters.export(result, str(artifact))
        else:
            artifact = job_dir / "preview.stl"
            cq.exporters.export(result, str(artifact), tolerance=0.05, angularTolerance=0.1)

        render_views = bool(job.get("render_views"))
        if render_views:
            render_stl = artifact if fmt != "step" else job_dir / "render_input.stl"
            if fmt == "step":
                cq.exporters.export(result, str(render_stl), tolerance=0.05, angularTolerance=0.1)
            _render_views(cq, shape, job_dir)

        feature_measurements = _measure_features(
            namespace,
            job.get("feature_graph", {}).get("operations", []),
            parameters,
        )
        payload = {
            "status": "success",
            "duration_ms": int((time.time() - started) * 1000),
            "bounding_box": {"x": bbox.xlen, "y": bbox.ylen, "z": bbox.zlen},
            "volume_mm3": volume,
            "solid_count": len(shape.Solids()) if hasattr(shape, "Solids") else 1,
            "feature_measurements": feature_measurements,
            "warnings": [],
            "render_views": ["front", "top", "right", "isometric"] if render_views else [],
        }
        (job_dir / "result.json").write_text(json.dumps(payload), encoding="utf-8")
        return 0
    except Exception as exc:
        _write_error(
            job_dir,
            "cadquery_execution",
            type(exc).__name__,
            str(exc),
            started,
            operation_id=_operation_id_from_traceback(source, exc.__traceback__),
        )
        return 1


def _shape_from_result(result):
    if hasattr(result, "val"):
        return result.val()
    return result


def _render_views(cq, shape, job_dir: Path) -> None:
    views = {
        "front": (0, -1, 0),
        "top": (0, 0, 1),
        "right": (1, 0, 0),
        "isometric": (1, -1, 1),
    }
    for name, direction in views.items():
        svg = cq.exporters.getSVG(
            shape,
            {
                "width": 512,
                "height": 512,
                "marginLeft": 16,
                "marginTop": 16,
                "projectionDir": direction,
                "showAxes": False,
                "strokeWidth": 0.8,
            },
        )
        (job_dir / f"render_{name}.svg").write_text(svg, encoding="utf-8")


def _measure_features(namespace, operations, parameters):
    predecessors = namespace.get("FEATURE_PREDECESSORS", {})
    measurements = {}
    for operation in operations:
        operation_id = operation.get("id") if isinstance(operation, dict) else None
        if not operation_id or operation_id not in namespace:
            continue
        try:
            shape = _shape_from_result(namespace[operation_id])
            bbox = shape.BoundingBox()
            volume = float(shape.Volume())
            predecessor_id = predecessors.get(operation_id)
            predecessor_volume = None
            predecessor_cylinders = 0
            predecessor_centers = []
            if predecessor_id and predecessor_id in namespace:
                predecessor_shape = _shape_from_result(namespace[predecessor_id])
                predecessor_volume = float(predecessor_shape.Volume())
                predecessor_cylinders = _count_face_type(predecessor_shape, "CYLINDER")
                predecessor_centers = _face_centers(predecessor_shape, "CYLINDER")
            cylinder_count = _count_face_type(shape, "CYLINDER")
            cylinder_centers = _face_centers(shape, "CYLINDER")
            new_cylinder_centers = _new_points(cylinder_centers, predecessor_centers)
            new_cylinder_diameters = _new_cylinder_diameters(
                shape,
                new_cylinder_centers,
                operation,
            )
            measurement = {
                "operation_id": operation_id,
                "type": operation.get("type", "feature"),
                "operation": operation.get("operation", "add"),
                "volume_mm3": volume,
                "volume_delta_mm3": volume - predecessor_volume if predecessor_volume is not None else volume,
                "bounding_box": {"x": bbox.xlen, "y": bbox.ylen, "z": bbox.zlen},
                "solid_count": len(shape.Solids()) if hasattr(shape, "Solids") else 1,
                "cylindrical_face_count": cylinder_count,
                "cylindrical_faces_delta": cylinder_count - predecessor_cylinders,
                "new_cylindrical_centers": new_cylinder_centers,
                "measured_cylinder_diameters": new_cylinder_diameters,
                "measured_cylinder_diameter": (
                    sum(new_cylinder_diameters) / len(new_cylinder_diameters)
                    if new_cylinder_diameters
                    else None
                ),
            }
            if operation.get("pattern"):
                measurement["pattern"] = operation["pattern"]
                count = operation["pattern"].get("count")
                measurement["expected_instance_count"] = int(parameters[count] if isinstance(count, str) else count)
                measurement.update(_measure_linear_pattern(operation, parameters, new_cylinder_centers))
            if operation.get("profile"):
                measurement["profile"] = operation["profile"]
            measurements[operation_id] = measurement
        except Exception as exc:
            measurements[operation_id] = {
                "operation_id": operation_id,
                "measurement_error": str(exc)[:500],
            }
    return measurements


def _count_face_type(shape, geometry_type):
    return sum(1 for face in shape.Faces() if face.geomType() == geometry_type)


def _face_centers(shape, geometry_type):
    return [list(face.Center().toTuple()) for face in shape.Faces() if face.geomType() == geometry_type]


def _new_points(points, previous_points, tolerance=1e-5):
    return [
        point
        for point in points
        if not any(sum((point[index] - old[index]) ** 2 for index in range(3)) ** 0.5 <= tolerance for old in previous_points)
    ]


def _measure_linear_pattern(operation, parameters, centers):
    pattern = operation.get("pattern", {})
    if pattern.get("type") != "linear" or not centers:
        return {}
    placement = operation.get("placement") or {}
    plane = placement.get("plane") or "XY"
    local_axis = str(pattern.get("axis") or "X").upper()
    axis_indices = {
        ("XY", "X"): 0,
        ("XY", "Y"): 1,
        ("XZ", "X"): 0,
        ("XZ", "Y"): 2,
        ("YZ", "X"): 1,
        ("YZ", "Y"): 2,
    }
    axis_index = axis_indices.get((plane, local_axis))
    if axis_index is None:
        return {}
    positions = sorted(center[axis_index] for center in centers)
    pitch_values = [positions[index + 1] - positions[index] for index in range(len(positions) - 1)]
    origin = placement.get("origin") or [0, 0, 0]
    origin_value = _resolve_value(origin[axis_index], parameters) if len(origin) == 3 else 0.0
    return {
        "measured_instance_positions": positions,
        "measured_pitch": sum(pitch_values) / len(pitch_values) if pitch_values else None,
        "measured_start_margin": positions[0] - float(origin_value),
    }


def _resolve_value(value, parameters):
    return parameters[value] if isinstance(value, str) else value


def _new_cylinder_diameters(shape, new_centers, operation):
    if not new_centers:
        return []
    plane = (operation.get("placement") or {}).get("plane") or "XY"
    normal_axis = {"XY": "zlen", "XZ": "ylen", "YZ": "xlen"}.get(plane)
    if normal_axis is None:
        return []
    diameters = []
    for face in shape.Faces():
        if face.geomType() != "CYLINDER":
            continue
        center = list(face.Center().toTuple())
        if not any(sum((center[index] - point[index]) ** 2 for index in range(3)) ** 0.5 <= 1e-5 for point in new_centers):
            continue
        axis_length = getattr(face.BoundingBox(), normal_axis)
        if axis_length > 1e-9:
            diameters.append(float(face.Area()) / (3.141592653589793 * axis_length))
    return diameters


def _write_error(
    job_dir: Path,
    stage: str,
    error_type: str,
    message: str,
    started: float,
    operation_id: str | None = None,
) -> None:
    payload = {
        "status": "error",
        "stage": stage,
        "error_type": error_type,
        "message": message,
        "traceback": _sanitize_traceback(traceback.format_exc()),
        "duration_ms": int((time.time() - started) * 1000),
    }
    if operation_id:
        payload["operation_id"] = operation_id
    (job_dir / "result.json").write_text(json.dumps(payload), encoding="utf-8")


def _operation_id_from_traceback(source: str, traceback_object) -> str | None:
    generated_line = None
    for frame in traceback.extract_tb(traceback_object):
        if frame.filename == "generated_model.py":
            generated_line = frame.lineno
    if generated_line is None:
        return None
    lines = source.splitlines()
    for line in reversed(lines[:generated_line]):
        stripped = line.strip()
        if stripped.startswith("# feature:"):
            return stripped.removeprefix("# feature:").strip() or None
    return None


def _sanitize_traceback(text: str) -> str:
    lines = []
    for line in text.splitlines():
        if "generated_model.py" in line or "cadquery_worker.py" in line or line.startswith(("Traceback", "ValueError", "TypeError", "ImportError", "ModuleNotFoundError")):
            lines.append(line)
    return "\n".join(lines)[-4000:]


if __name__ == "__main__":
    raise SystemExit(main())
