"""Resource-limited execution of one CadQuery script (SPEC12).

Each request runs the script in a FRESH child process wrapped with
`resource.setrlimit`, so that inside the single long-lived, shared worker
container each request is capped independently:

* `RLIMIT_CPU`    — kills infinite loops (per request).
* `RLIMIT_AS`     — caps virtual address space (memory bombs). Set generously:
                    OCP mmaps a lot of virtual memory at import, so too tight a
                    value would break CadQuery itself. The container `mem_limit`
                    (cgroup RSS) is the real memory cap; this is a backstop.
* `RLIMIT_NPROC`  — blocks fork bombs (container `pids_limit` is the main guard).
* `RLIMIT_FSIZE`  — caps the size of any file the code writes.

The scratch dir lives on the container's tmpfs and is wiped after each call,
so nothing persists between requests. The execution core itself is the shared
`cq_worker` module (copied from `app/cq_worker.py` at image build), so local
and worker runs use identical geometry-info / STL logic.

`run(code) -> dict` returns the worker wire payload:
`{success, stl_base64, geometry_info, error}`.
"""

import base64
import json
import os
import resource
import subprocess
import sys
import tempfile
from pathlib import Path

TIMEOUT_SECONDS = int(os.getenv("CADQUERY_WORKER_TIMEOUT_SECONDS", "120"))
CPU_SECONDS = int(os.getenv("EASYCAD_WORKER_CPU_SECONDS", str(TIMEOUT_SECONDS)))
# RLIMIT_AS caps VIRTUAL address space. OCP mmaps a lot at import, so a tight
# value breaks CadQuery; the container `mem_limit` cgroup is the real memory
# cap. Off by default (0); opt in only if you know your OCP build's footprint.
AS_MB = int(os.getenv("EASYCAD_WORKER_AS_MB", "0"))
# Keep at or below the container `pids_limit` (128 in docker-compose-prod.yml)
# so the documented per-request budget matches the effective cgroup cap.
NPROC = int(os.getenv("EASYCAD_WORKER_NPROC", "128"))
FSIZE_MB = int(os.getenv("EASYCAD_WORKER_FSIZE_MB", "256"))

_HERE = Path(__file__).resolve().parent


def _try_setrlimit(which: int, soft: int, hard: int) -> None:
    """Best-effort: a platform that can't honour one limit (e.g. RLIMIT_AS on
    macOS dev boxes) must not abort the whole exec. On Linux all succeed and the
    container cgroup limits (mem/pids/cpu) plus the wall-clock timeout apply
    regardless."""
    try:
        resource.setrlimit(which, (soft, hard))
    except (ValueError, OSError):
        pass


def _set_limits() -> None:  # runs in the child, after fork, before exec
    _try_setrlimit(resource.RLIMIT_CPU, CPU_SECONDS, CPU_SECONDS)
    fsize = FSIZE_MB * 1024 * 1024
    _try_setrlimit(resource.RLIMIT_FSIZE, fsize, fsize)
    _try_setrlimit(resource.RLIMIT_NPROC, NPROC, NPROC)
    if AS_MB > 0:
        as_bytes = AS_MB * 1024 * 1024
        _try_setrlimit(resource.RLIMIT_AS, as_bytes, as_bytes)


def _fail(msg: str) -> dict:
    return {"success": False, "stl_base64": None, "geometry_info": None, "error": msg}


def run(code: str) -> dict:
    """Execute `code` in a resource-limited child; return the wire payload."""
    with tempfile.TemporaryDirectory() as tmp:
        stl_path = Path(tmp) / "model.stl"
        job = json.dumps({"code": code, "stl_path": str(stl_path)})
        try:
            proc = subprocess.run(
                [sys.executable, "-m", "cq_worker"],
                input=job,
                capture_output=True,
                text=True,
                timeout=TIMEOUT_SECONDS,
                cwd=str(_HERE),
                preexec_fn=_set_limits,
            )
        except subprocess.TimeoutExpired:
            return _fail(f"Execution timed out after {TIMEOUT_SECONDS}s.")

        if proc.returncode != 0:
            detail = proc.stderr.strip() or f"worker exited with code {proc.returncode}"
            return _fail(f"Execution crashed: {detail[:800]}")

        line = proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else ""
        try:
            out = json.loads(line)
        except json.JSONDecodeError:
            return _fail(f"Malformed worker output: {proc.stdout[:500]!r}")

        if not out.get("success"):
            return _fail(out.get("error") or "Unknown execution error.")

        stl_b64 = base64.b64encode(stl_path.read_bytes()).decode("ascii")
        return {
            "success": True,
            "stl_base64": stl_b64,
            "geometry_info": out["geometry_info"],
            "error": None,
        }
