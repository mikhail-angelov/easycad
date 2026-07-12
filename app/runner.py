from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Dict, Optional

from PIL import Image, ImageDraw

from .expressions import evaluate_expression
from .models import CADProject
from .feature_compiler import CompilerError, compile_feature_graph


ROOT = Path(__file__).resolve().parent.parent
WORKER_SCRIPT = ROOT / "worker" / "cadquery_worker.py"
WORKER_TIMEOUT_SECONDS = float(os.environ.get("CADQUERY_WORKER_TIMEOUT_SECONDS", "35"))


class RunnerError(RuntimeError):
    def __init__(self, stage: str, message: str, detail: Optional[dict] = None):
        super().__init__(message)
        self.stage = stage
        self.detail = detail or {}


def concrete_parameters(project: CADProject, overrides: Dict[str, object]) -> Dict[str, object]:
    values: Dict[str, object] = {}
    numeric_values: Dict[str, float] = {}
    for key, param in project.parameters.items():
        if param.type == "number":
            value = overrides.get(key, param.value)
            if value is None:
                raise RunnerError("parameters", f"Parameter '{key}' has no value")
            value = float(value)
            if param.min is not None and value < param.min:
                raise RunnerError("parameters", f"Parameter '{key}' is below minimum {param.min}")
            if param.max is not None and value > param.max:
                raise RunnerError("parameters", f"Parameter '{key}' is above maximum {param.max}")
            values[key] = value
            numeric_values[key] = value
        elif param.type in {"text", "choice"}:
            value = overrides.get(key, param.value or "")
            value = str(value)
            if param.type == "text" and len(value) > 80:
                raise RunnerError("parameters", f"Parameter '{key}' is longer than 80 characters")
            if param.type == "choice" and param.options and value not in param.options:
                raise RunnerError("parameters", f"Parameter '{key}' must be one of {', '.join(param.options)}")
            values[key] = value

    pending = {
        key: param.expression
        for key, param in project.parameters.items()
        if param.type == "expression" and param.expression
    }
    while pending:
        progressed = False
        for key, expr in list(pending.items()):
            try:
                evaluated = evaluate_expression(expr, numeric_values)
            except Exception:
                continue
            values[key] = evaluated
            numeric_values[key] = evaluated
            del pending[key]
            progressed = True
        if not progressed:
            raise RunnerError("parameters", "Could not resolve derived parameters")
    return values


def run_project(
    project: CADProject,
    overrides: Dict[str, object],
    fmt: str = "stl",
    render_views: bool = False,
) -> Dict[str, object]:
    if fmt not in {"stl", "step"}:
        raise RunnerError("export", "Format must be stl or step")
    try:
        source = compile_feature_graph(project.feature_graph, project.parameters)
    except CompilerError as exc:
        raise RunnerError("feature_compiler", str(exc), {"operation_id": exc.operation_id}) from exc
    params = concrete_parameters(project, overrides)

    with tempfile.TemporaryDirectory(prefix="easycad-job-") as tmp:
        job_dir = Path(tmp)
        job_dir.chmod(0o777)
        (job_dir / "job.json").write_text(
            json.dumps(
                {
                    "parameters": params,
                    "source": source,
                    "format": fmt,
                    "feature_graph": project.feature_graph.model_dump(),
                    "render_views": render_views,
                }
            ),
            encoding="utf-8",
        )
        (job_dir / "job.json").chmod(0o666)
        started = time.time()
        try:
            completed = _run_local(job_dir)
        except subprocess.TimeoutExpired as exc:
            raise RunnerError(
                "worker_timeout",
                f"Worker exceeded {WORKER_TIMEOUT_SECONDS:g} second timeout",
                {"timeout_seconds": WORKER_TIMEOUT_SECONDS, "cmd": exc.cmd},
            ) from exc
        duration_ms = int((time.time() - started) * 1000)
        result_path = job_dir / "result.json"
        if completed.returncode != 0:
            detail = {"returncode": completed.returncode}
            if result_path.exists():
                detail.update(json.loads(result_path.read_text(encoding="utf-8")))
            message = (
                detail.get("message")
                or completed.stderr.strip()
                or completed.stdout.strip()
                or "Worker failed"
            )
            raise RunnerError(detail.get("stage", "cadquery_execution"), str(message)[:1000], detail)

        if not result_path.exists():
            raise RunnerError("worker", "Worker did not produce result.json")
        result = json.loads(result_path.read_text(encoding="utf-8"))
        result["duration_ms"] = result.get("duration_ms") or duration_ms
        artifact = job_dir / ("model.step" if fmt == "step" else "preview.stl")
        if result.get("status") != "success":
            raise RunnerError(result.get("stage", "cadquery_execution"), result.get("message", "Worker error"), result)
        if not artifact.exists():
            raise RunnerError("export", f"Worker did not produce {artifact.name}")
        result["artifact_bytes"] = artifact.read_bytes()
        if render_views:
            render_artifacts = {}
            for view in ("front", "top", "right", "isometric"):
                svg_path = job_dir / f"render_{view}.svg"
                render_path = job_dir / f"render_{view}.png"
                if not svg_path.exists():
                    raise RunnerError("render", f"Worker did not produce {svg_path.name}")
                _rasterize_svg(svg_path, render_path)
                render_artifacts[view] = render_path.read_bytes()
            result["render_artifacts"] = render_artifacts
        return result


def _run_local(job_dir: Path) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env.setdefault("XDG_CACHE_HOME", str(ROOT / ".cache"))
    return subprocess.run(
        [sys.executable, str(WORKER_SCRIPT), str(job_dir)],
        text=True,
        capture_output=True,
        timeout=WORKER_TIMEOUT_SECONDS,
        env=env,
    )


def _rasterize_svg(svg_path: Path, png_path: Path) -> None:
    svg_text = svg_path.read_text(encoding="utf-8")
    width_match = re.search(r'width="([0-9.]+)"', svg_text)
    height_match = re.search(r'height="([0-9.]+)"', svg_text)
    transform_match = re.search(
        r'transform="scale\(([-0-9.eE]+),\s*([-0-9.eE]+)\)\s+translate\(([-0-9.eE]+),([-0-9.eE]+)\)"',
        svg_text,
    )
    if not width_match or not height_match or not transform_match:
        raise RunnerError("render", "CadQuery SVG is missing dimensions or projection transform")
    width = int(float(width_match.group(1)))
    height = int(float(height_match.group(1)))
    scale_x, scale_y, translate_x, translate_y = map(float, transform_match.groups())
    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)
    path_count = 0
    for path_data in re.findall(r'<path d="([^"]*)"', svg_text):
        points = []
        for _, x_text, y_text in re.findall(r'([ML])\s*([-0-9.eE]+),([-0-9.eE]+)', path_data):
            x = scale_x * (float(x_text) + translate_x)
            y = scale_y * (float(y_text) + translate_y)
            points.append((x, y))
        if len(points) >= 2:
            draw.line(points, fill=(25, 30, 35), width=2)
            path_count += 1
    if path_count == 0:
        raise RunnerError("render", "CadQuery SVG contains no drawable projection paths")
    image.save(png_path, format="PNG")
