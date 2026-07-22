"""Subprocess-isolated CadQuery execution.

`execute(code)` runs the given CadQuery script in a child process
(`app.cq_worker`), captures the exported STL, and returns the result plus a
refreshed geometry-info block appended to the code. All failure modes —
syntax errors, missing `result`, OCP crashes, timeouts — come back as a
populated `error` string rather than raising.
"""

import base64
import json
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

TIMEOUT_SECONDS = int(os.getenv("CADQUERY_WORKER_TIMEOUT_SECONDS", "120"))

# Matches the auto-generated geometry-info block so it can be stripped/replaced.
_GEOMETRY_RE = re.compile(r"\n*# ── Geometry info.*?(?=\n[^#]|\Z)", re.DOTALL)

_ROOT = Path(__file__).resolve().parent.parent


@dataclass
class ExecResult:
    success: bool
    stl_base64: str | None = None
    geometry_info: str | None = None
    code_with_geometry: str | None = None
    error: str | None = None


def strip_geometry_block(code: str) -> str:
    """Remove any existing geometry-info comment block from the code."""
    return _GEOMETRY_RE.sub("", code).rstrip()


def append_geometry_block(code: str, info: str) -> str:
    """Replace the code's geometry-info block with a freshly computed one."""
    return strip_geometry_block(code) + "\n\n" + info + "\n"


def execute(code: str) -> ExecResult:
    """Execute CadQuery `code` in an isolated worker and return the outcome."""
    with tempfile.TemporaryDirectory() as tmp:
        stl_path = Path(tmp) / "model.stl"
        job = json.dumps({"code": code, "stl_path": str(stl_path)})
        try:
            proc = subprocess.run(
                [sys.executable, "-m", "app.cq_worker"],
                input=job,
                capture_output=True,
                text=True,
                timeout=TIMEOUT_SECONDS,
                cwd=str(_ROOT),
            )
        except subprocess.TimeoutExpired:
            return ExecResult(False, error=f"Execution timed out after {TIMEOUT_SECONDS}s.")

        if proc.returncode != 0:
            detail = proc.stderr.strip() or f"worker exited with code {proc.returncode}"
            # Truncate — OCP crash dumps can be huge.
            return ExecResult(False, error=f"Execution crashed: {detail[:800]}")

        line = proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else ""
        try:
            out = json.loads(line)
        except json.JSONDecodeError:
            return ExecResult(False, error=f"Malformed worker output: {proc.stdout[:500]!r}")

        if not out.get("success"):
            return ExecResult(False, error=out.get("error") or "Unknown execution error.")

        stl_bytes = stl_path.read_bytes()
        info = out["geometry_info"]
        return ExecResult(
            success=True,
            stl_base64=base64.b64encode(stl_bytes).decode("ascii"),
            geometry_info=info,
            code_with_geometry=append_geometry_block(code, info),
        )
