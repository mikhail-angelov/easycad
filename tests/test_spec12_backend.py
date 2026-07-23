"""SPEC12: pluggable execution backend selection + worker code guard.

These tests need neither cadquery nor a running worker — they exercise backend
selection (which class `execute` dispatches to) and the standalone AST guard.
"""

import sys
from pathlib import Path

from app import cadquery_exec
from app.cadquery_exec import LocalExecutor, RemoteExecutor, _select_backend

# The worker guard is a standalone module under worker/.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "worker"))
import code_guard  # noqa: E402


# ── Backend selection ────────────────────────────────────────────────────────

def test_default_is_local(monkeypatch):
    monkeypatch.delenv("EASYCAD_WORKER_URL", raising=False)
    monkeypatch.delenv("EASYCAD_EXECUTOR", raising=False)
    assert isinstance(_select_backend(), LocalExecutor)


def test_worker_url_selects_remote(monkeypatch):
    monkeypatch.delenv("EASYCAD_EXECUTOR", raising=False)
    monkeypatch.setenv("EASYCAD_WORKER_URL", "http://worker:8853")
    backend = _select_backend()
    assert isinstance(backend, RemoteExecutor)
    assert backend.base_url == "http://worker:8853"


def test_explicit_local_overrides_url(monkeypatch):
    monkeypatch.setenv("EASYCAD_WORKER_URL", "http://worker:8853")
    monkeypatch.setenv("EASYCAD_EXECUTOR", "local")
    assert isinstance(_select_backend(), LocalExecutor)


def test_remote_without_url_falls_back_to_local(monkeypatch):
    monkeypatch.delenv("EASYCAD_WORKER_URL", raising=False)
    monkeypatch.setenv("EASYCAD_EXECUTOR", "remote")
    assert isinstance(_select_backend(), LocalExecutor)


def test_remote_executor_maps_worker_down_to_error(monkeypatch):
    # Point at an unroutable port; execute() must return a failed ExecResult,
    # never raise.
    backend = RemoteExecutor("http://127.0.0.1:1")
    monkeypatch.setattr(cadquery_exec, "TIMEOUT_SECONDS", 1)
    res = backend.execute("import cadquery as cq\nresult = cq.Workplane('XY').box(1,1,1)\n")
    assert not res.success
    assert "worker" in res.error.lower()


# ── Level 0 code guard ───────────────────────────────────────────────────────

def test_guard_allows_normal_cadquery():
    ok, _ = code_guard.check(
        "import cadquery as cq\nimport math\n"
        "result = cq.Workplane('XY').box(50, 80, 30).edges('|Z').fillet(3)\n"
    )
    assert ok


def test_guard_blocks_os_import():
    ok, reason = code_guard.check("import os\nresult = os.getcwd()\n")
    assert not ok
    assert "os" in reason


def test_guard_blocks_dunder_escape():
    ok, reason = code_guard.check("result = ().__class__.__bases__[0].__subclasses__()\n")
    assert not ok
    assert "dunder" in reason


def test_guard_blocks_eval_and_open():
    assert not code_guard.check("result = eval('1+1')\n")[0]
    assert not code_guard.check("result = open('/etc/passwd').read()\n")[0]


def test_guard_rejects_syntax_error():
    ok, reason = code_guard.check("result = (1 +")  # genuinely unparseable
    assert not ok
    assert "syntax" in reason.lower()
