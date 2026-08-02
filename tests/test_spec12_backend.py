"""SPEC12: pluggable execution backend selection + worker code guard.

These tests need neither cadquery nor a running worker — they exercise backend
selection (which class `execute` dispatches to) and the standalone AST guard.
"""

import pytest

from app import cadquery_exec, code_guard
from app.cadquery_exec import LocalExecutor, RemoteExecutor, _select_backend


class _FakeResp:
    def __init__(self, body: bytes):
        self._body = body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


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


@pytest.mark.parametrize("body", [b"[]", b"null", b'"oops"', b"1"])
def test_remote_executor_non_object_response_is_coded(monkeypatch, body):
    # Valid JSON that isn't an object must NOT raise AttributeError from a later
    # `.get()` — it degrades to a coded worker_unavailable failure (W1).
    backend = RemoteExecutor("http://worker:8853")
    monkeypatch.setattr(cadquery_exec.urllib.request, "urlopen",
                        lambda *a, **k: _FakeResp(body))
    res = backend.execute("code")
    assert res.success is False
    assert res.code == "worker_unavailable"


def test_remote_export_non_object_response_is_worker_unavailable(monkeypatch):
    # A non-object body is a malformed/broken worker → an OPERATIONAL outage
    # (SPEC21 W2), so export is now symmetric with execute: coded, not silent None.
    backend = RemoteExecutor("http://worker:8853")
    monkeypatch.setattr(cadquery_exec.urllib.request, "urlopen",
                        lambda *a, **k: _FakeResp(b"[]"))
    result = backend.export("code", "stl")
    assert result.data is None and result.code == "worker_unavailable"


@pytest.mark.parametrize("body", [
    b'{"success": true}',                                   # success but no fields
    b'{"success": true, "stl_base64": 123, "geometry_info": "x"}',  # stl wrong type
    b'{"success": true, "stl_base64": "AA==", "geometry_info": 5}',  # geom wrong type
    b'{}',                                                  # empty object
    b'{"foo": 1}',                                          # unrelated keys
    b'{"success": false}',                                  # failure without error text
    b'{"success": "true", "stl_base64": "AA==", "geometry_info": "x"}',  # string discriminator
    b'{"success": "false", "error": "x"}',                 # truthy string discriminator
    b'{"success": null, "error": "x"}',                    # null discriminator
    b'{"success": 1, "stl_base64": "AA==", "geometry_info": "x"}',  # numeric discriminator
    b'{"error": "x"}',                                      # missing discriminator
    b'{"success": false, "error": "x", "code": []}',       # unhashable code → would 500
    b'{"success": false, "error": "x", "code": 5}',        # non-string code
])
def test_remote_executor_malformed_object_is_coded(monkeypatch, body):
    # A structurally malformed object must not 500 (KeyError/TypeError) nor return an
    # untagged failure — it degrades to a coded worker_unavailable notice (W1).
    backend = RemoteExecutor("http://worker:8853")
    monkeypatch.setattr(cadquery_exec.urllib.request, "urlopen",
                        lambda *a, **k: _FakeResp(body))
    res = backend.execute("code")
    assert res.success is False
    assert res.code == "worker_unavailable"


def test_remote_executor_wellformed_success_passes(monkeypatch):
    backend = RemoteExecutor("http://worker:8853")
    monkeypatch.setattr(
        cadquery_exec.urllib.request, "urlopen",
        lambda *a, **k: _FakeResp(b'{"success": true, "stl_base64": "AA==", "geometry_info": "# info"}'),
    )
    res = backend.execute("result = None")
    assert res.success is True and res.geometry_info == "# info"


def test_remote_executor_wellformed_model_failure_stays_ordinary(monkeypatch):
    # A real model error (well-formed failure) must NOT be mistagged as an
    # operational worker_unavailable — it stays a plain in-chat failed step.
    backend = RemoteExecutor("http://worker:8853")
    monkeypatch.setattr(
        cadquery_exec.urllib.request, "urlopen",
        lambda *a, **k: _FakeResp(b'{"success": false, "error": "NameError: cq"}'),
    )
    res = backend.execute("code")
    assert res.success is False and res.code is None
    assert "NameError" in res.error


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


# ── Local-mode guard opt-in (EASYCAD_LOCAL_GUARD) ─────────────────────────────

def test_local_guard_off_by_default(monkeypatch):
    # Without the flag, LocalExecutor does not gate on the guard (a forbidden
    # import fails at exec time, not with a guard message).
    monkeypatch.delenv("EASYCAD_LOCAL_GUARD", raising=False)
    res = LocalExecutor().execute("import os\nresult = os.getcwd()\n")
    assert not res.success
    assert "guard" not in (res.error or "").lower()


def test_local_guard_blocks_when_enabled(monkeypatch):
    monkeypatch.setenv("EASYCAD_LOCAL_GUARD", "1")
    res = LocalExecutor().execute("import os\nresult = os.getcwd()\n")
    assert not res.success
    assert "rejected by guard" in (res.error or "").lower()


def test_local_guard_allows_valid_code_when_enabled(monkeypatch):
    monkeypatch.setenv("EASYCAD_LOCAL_GUARD", "1")
    res = LocalExecutor().execute("import cadquery as cq\nresult = cq.Workplane('XY').box(10, 10, 10)\n")
    assert res.success, res.error
