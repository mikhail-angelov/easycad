"""CadQuery execution with a pluggable backend (SPEC12).

`execute(code)` runs the given CadQuery script and returns an `ExecResult`
(STL b64 + a refreshed geometry-info block). All failure modes — syntax
errors, missing `result`, OCP crashes, timeouts, transport errors — come back
as a populated `error` string rather than raising.

Two backends, selected by environment (see `_select_backend`):

* `LocalExecutor` (default) — runs the script in an in-process child
  (`app.cq_worker`), exactly as before SPEC12. Used for local/desktop runs.
  No worker, no Docker, no network hop.
* `RemoteExecutor` — POSTs the script to an isolated, hardened worker
  container over HTTP. Used for hosted/SaaS deployment, where untrusted
  LLM-generated code must run away from the API process, the LLM key, and
  user data.

The public `execute()` signature and `ExecResult` fields are unchanged, so all
call sites in `app/main.py` are untouched.
"""

import base64
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
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


def _result_from_worker_payload(code: str, out: dict) -> ExecResult:
    """Build an ExecResult from a worker payload {success, stl_base64?,
    geometry_info?, error?}. Shared by both backends so local and remote agree.
    """
    if not out.get("success"):
        return ExecResult(False, error=out.get("error") or "Unknown execution error.")
    info = out["geometry_info"]
    return ExecResult(
        success=True,
        stl_base64=out["stl_base64"],
        geometry_info=info,
        code_with_geometry=append_geometry_block(code, info),
    )


class LocalExecutor:
    """In-process execution — pre-SPEC12 behaviour, unchanged.

    Spawns `python -m app.cq_worker` in a child process so a CadQuery/OCP
    segfault or hang cannot take down the API server.
    """

    def execute(self, code: str) -> ExecResult:
        import tempfile

        # Level 0 guard is off by default locally (preserves pre-SPEC12
        # behaviour); opt in with EASYCAD_LOCAL_GUARD=1. The worker always runs
        # it — see app/code_guard.py.
        if os.getenv("EASYCAD_LOCAL_GUARD") == "1":
            from . import code_guard

            ok, reason = code_guard.check(code)
            if not ok:
                return ExecResult(False, error=f"Code rejected by guard: {reason}")

        # Read TIMEOUT_SECONDS as a live module global (tests monkeypatch it).
        timeout = TIMEOUT_SECONDS
        with tempfile.TemporaryDirectory() as tmp:
            stl_path = Path(tmp) / "model.stl"
            job = json.dumps({"code": code, "stl_path": str(stl_path)})
            try:
                proc = subprocess.run(
                    [sys.executable, "-m", "app.cq_worker"],
                    input=job,
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                    cwd=str(_ROOT),
                )
            except subprocess.TimeoutExpired:
                return ExecResult(False, error=f"Execution timed out after {timeout}s.")

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

            out["stl_base64"] = base64.b64encode(stl_path.read_bytes()).decode("ascii")
            return _result_from_worker_payload(code, out)


class RemoteExecutor:
    """Delegates execution to an isolated worker container over HTTP.

    The worker returns the same payload shape the local child emits (plus an
    already-base64 STL), so `_result_from_worker_payload` handles both. Only
    transport-level failures (worker down, 5xx, malformed body) are synthesised
    here into a failed ExecResult.
    """

    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")

    def execute(self, code: str) -> ExecResult:
        url = f"{self.base_url}/execute"
        body = json.dumps({"code": code}).encode("utf-8")
        req = urllib.request.Request(
            url, data=body, headers={"Content-Type": "application/json"}, method="POST"
        )
        # Allow slack over the worker's own execution timeout for the round-trip.
        timeout = TIMEOUT_SECONDS + 15
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                out = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return ExecResult(False, error=f"Worker error (HTTP {exc.code}): {exc.reason}")
        except urllib.error.URLError as exc:
            return ExecResult(False, error=f"Worker unavailable: {exc.reason}")
        except (TimeoutError, json.JSONDecodeError) as exc:
            return ExecResult(False, error=f"Worker response invalid: {exc}")
        return _result_from_worker_payload(code, out)


def _select_backend():
    """Pick the execution backend from the environment on each call (cheap).

    * `EASYCAD_EXECUTOR=local|remote` forces a backend when set.
    * Otherwise: remote iff `EASYCAD_WORKER_URL` is set, else local.

    Local is the default, preserving pre-SPEC12 behaviour when nothing is set.
    """
    mode = os.getenv("EASYCAD_EXECUTOR")
    url = os.getenv("EASYCAD_WORKER_URL")
    if mode == "local":
        return LocalExecutor()
    if mode == "remote" or (mode is None and url):
        if not url:
            # Asked for remote but told no URL — fail safe to local rather than crash.
            return LocalExecutor()
        return RemoteExecutor(url)
    return LocalExecutor()


def execute(code: str) -> ExecResult:
    """Execute CadQuery `code` via the configured backend and return the outcome."""
    return _select_backend().execute(code)
