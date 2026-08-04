"""SPEC19 W1: graceful degradation — operational failures surface as coded,
localized "try again" notices instead of raw 5xx / error strings.

The LLM generator is stubbed and `execute` is monkeypatched to simulate the
operational failures a real worker can hit (wall-clock timeout, transport down)
without needing a worker or a slow model.
"""

import threading

from fastapi.testclient import TestClient

import app.main as m
from app.main import app
from app.cadquery_exec import ExecResult

BOX = "import cadquery as cq\nresult = cq.Workplane('XY').box(10, 10, 10)\n"


def _stub_llm(monkeypatch, code: str = BOX):
    async def fake_generate(base_code, prompt, provider, model=None, temperature=0.2,
                            api_key=None, skills=None, feedback=None):
        return code

    monkeypatch.setattr(m, "generate_code", fake_generate)


def _chat(client: TestClient, prompt="add a hole", ip="9.9.9.9"):
    return client.post(
        "/api/chat",
        json={"prompt": prompt, "auto_refine": False, "current_code": BOX},
        headers={"x-real-ip": ip},
    )


# ── Operational exec failures → coded notice, not a chat error ─────────────────

def test_chat_worker_timeout_returns_execution_timeout(monkeypatch):
    _stub_llm(monkeypatch)
    monkeypatch.setattr(
        m, "execute",
        lambda code: ExecResult(False, error="Worker timed out", code="execution_timeout"),
    )
    r = _chat(TestClient(app), ip="60.0.0.1")
    assert r.status_code == 504
    assert r.json()["detail"]["code"] == "execution_timeout"


def test_chat_worker_transport_returns_worker_unavailable(monkeypatch):
    _stub_llm(monkeypatch)
    monkeypatch.setattr(
        m, "execute",
        lambda code: ExecResult(False, error="Worker unavailable", code="worker_unavailable"),
    )
    r = _chat(TestClient(app), ip="60.0.0.2")
    assert r.status_code == 503
    assert r.json()["detail"]["code"] == "worker_unavailable"


def test_ordinary_model_error_stays_a_chat_step(monkeypatch):
    # A plain CadQuery error (no operational `code`) must NOT become a coded notice
    # — it stays an in-chat failed step the user can iterate on.
    _stub_llm(monkeypatch)
    monkeypatch.setattr(
        m, "execute",
        lambda code: ExecResult(False, error="NameError: cq is not defined"),
    )
    # No repair attempts so the single failure is the outcome.
    monkeypatch.setattr(m, "MAX_REPAIR", 0)
    r = _chat(TestClient(app), ip="60.0.0.3")
    assert r.status_code == 200
    step = r.json()["step"]
    assert step["success"] is False and "NameError" in step["error"]


def test_operational_failure_skips_repair_loop(monkeypatch):
    # An operational failure must raise immediately rather than burn repair attempts
    # (each of which is another LLM call).
    _stub_llm(monkeypatch)
    monkeypatch.setattr(m, "MAX_REPAIR", 3)
    calls = {"n": 0}

    def fake_exec(code):
        calls["n"] += 1
        return ExecResult(False, error="down", code="worker_unavailable")

    monkeypatch.setattr(m, "execute", fake_exec)
    r = _chat(TestClient(app), ip="60.0.0.4")
    assert r.status_code == 503
    assert calls["n"] == 1  # raised on the first execute, no repairs


def test_execute_manual_operational_failure_is_coded(monkeypatch):
    monkeypatch.setattr(
        m, "execute",
        lambda code: ExecResult(False, error="down", code="worker_unavailable"),
    )
    r = TestClient(app).post(
        "/api/execute-manual", json={"code": BOX}, headers={"x-real-ip": "60.0.0.5"}
    )
    assert r.status_code == 503
    assert r.json()["detail"]["code"] == "worker_unavailable"


def test_busy_slot_is_coded_server_busy(monkeypatch):
    monkeypatch.setattr(m, "_gen_semaphore", threading.BoundedSemaphore(1))
    assert m._gen_semaphore.acquire(blocking=False) is True
    try:
        r = _chat(TestClient(app), ip="60.0.0.6")
        assert r.status_code == 503
        assert r.json()["detail"]["code"] == "server_busy"
    finally:
        m._gen_semaphore.release()


# ── W4: legal pages ──────────────────────────────────────────────────────────

def _static_present() -> bool:
    return (m.STATIC_DIR / "terms.html").exists() and (m.STATIC_DIR / "privacy.html").exists()


def test_terms_and_privacy_reachable():
    if not _static_present():
        import pytest
        pytest.skip("frontend build not present (run `npm run build`)")
    client = TestClient(app)
    for path in ("/terms", "/privacy"):
        r = client.get(path)
        assert r.status_code == 200
        assert "text/html" in r.headers["content-type"]


def test_legal_pages_linked_from_landing():
    if not (m.STATIC_DIR / "landing.html").exists():
        import pytest
        pytest.skip("frontend build not present")
    body = TestClient(app).get("/").text
    assert 'href="/terms"' in body and 'href="/privacy"' in body


def test_legal_routes_404_when_shared_assets_missing(monkeypatch, tmp_path):
    # A partial/stale build (HTML present but legal.{css,js} absent) must 404
    # cleanly instead of raising FileNotFoundError → 500 mid-request.
    (tmp_path / "terms.html").write_text("<!--@LEGAL_CSS@--><!--@LEGAL_JS@-->", encoding="utf-8")
    monkeypatch.setattr(m, "STATIC_DIR", tmp_path)
    assert TestClient(app).get("/terms").status_code == 404


def test_admin_template_uses_canonical_failure_and_signups():
    html = (m.TEMPLATES_DIR / "admin.html").read_text(encoding="utf-8")
    assert "gen_exec_fail" in html        # real failure metric is rendered
    assert "c.gen_fail" not in html       # the dead, never-emitted metric is gone
    assert "Execution failed" in html     # honest label — not the misleading "Gen failed"
    assert "'Gen failed'" not in html
    assert "s.signups" in html            # SPEC19 W2 signup count is rendered


def test_legal_pages_inline_shared_css_and_js():
    # The shared CSS/JS are stored once and injected server-side: the served page
    # must have its placeholders replaced by real <style>/<script>, and the RU
    # toggle strings must be present.
    if not _static_present():
        import pytest
        pytest.skip("frontend build not present")
    body = TestClient(app).get("/privacy").text
    assert "<!--@LEGAL_CSS@-->" not in body and "<!--@LEGAL_JS@-->" not in body
    assert "--accent: #C24A2A" in body          # from legal.css
    assert "window.LEGAL_I18N_RU" in body       # page-specific RU strings
    assert "applyLang" in body                  # from legal.js


# ── Real worker wall-clock timeout carries the operational code ───────────────

def test_worker_timeout_payload_maps_to_execution_timeout():
    # A worker that hits its OWN wall-clock returns a successful HTTP body with
    # {"success": false, "code": "execution_timeout"}. The executor boundary must
    # preserve that code so the API raises the W1 504 notice (not a generic step).
    from app.cadquery_exec import _result_from_worker_payload

    payload = {"success": False, "stl_base64": None, "geometry_info": None,
               "error": "Execution timed out after 120s.", "code": "execution_timeout"}
    res = _result_from_worker_payload(payload)
    assert res.success is False
    assert res.code == "execution_timeout"


def test_worker_ordinary_failure_has_no_operational_code():
    from app.cadquery_exec import _result_from_worker_payload

    payload = {"success": False, "error": "NameError: cq"}
    res = _result_from_worker_payload(payload)
    assert res.success is False and res.code is None


# ── /api/variations honours operational-error mapping (W1) ────────────────────

def _variations(client, count=2, ip="70.0.0.1"):
    return client.post(
        "/api/variations",
        json={"prompt": "make variants", "auto_refine": False,
              "current_code": BOX, "count": count},
        headers={"x-real-ip": ip},
    )


def test_variations_operational_failure_is_coded_when_empty(monkeypatch):
    _stub_llm(monkeypatch)
    monkeypatch.setattr(
        m, "execute",
        lambda code: ExecResult(False, error="down", code="worker_unavailable"),
    )
    r = _variations(TestClient(app), ip="70.0.0.1")
    assert r.status_code == 503
    assert r.json()["detail"]["code"] == "worker_unavailable"


def test_variations_keeps_partial_batch_on_later_operational_failure(monkeypatch):
    _stub_llm(monkeypatch)
    calls = {"n": 0}

    def fake_exec(code):
        calls["n"] += 1
        if calls["n"] == 1:
            return ExecResult(True, stl_base64="AA==", geometry_info="# info")
        return ExecResult(False, error="down", code="worker_unavailable")

    monkeypatch.setattr(m, "execute", fake_exec)
    r = _variations(TestClient(app), count=3, ip="70.0.0.2")
    # First candidate succeeded, so a later worker outage must not discard it:
    # return the partial batch (one good candidate), not a coded 5xx.
    assert r.status_code == 200
    cands = r.json()["candidates"]
    assert len(cands) == 1 and cands[0]["success"] is True


# ── Admin worker stats: malformed payload never 500s, output is sanitized ─────

class _FakeResp:
    def __init__(self, body: bytes):
        self._body = body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_worker_statz_malformed_non_dict_degrades(monkeypatch):
    # A worker /statz that returns a JSON list/string/number must not 500 the admin
    # endpoint (the old `data["reachable"] = True` raised TypeError).
    monkeypatch.setenv("EASYCAD_WORKER_URL", "http://worker:9000")
    monkeypatch.setattr(m.urllib.request, "urlopen",
                        lambda *a, **k: _FakeResp(b'["not", "a", "dict"]'))
    out = m._worker_statz()
    assert out["reachable"] is False and "malformed" in out["reason"]


def test_worker_statz_sanitizes_to_known_numeric_fields():
    # Unknown keys and non-numeric metric values are dropped so nothing untrusted
    # reaches the admin DOM (which renders these via innerHTML).
    raw = {
        "mode": "zygote", "jobs_total": 42, "rss_mb": 128.5,
        "evil": "<script>alert(1)</script>",          # unknown key → dropped
        "jobs_total_str": "99",                        # unknown key → dropped
        "crashes_total": "<img onerror=x>",            # known key, non-numeric → dropped
    }
    out = m._sanitize_worker_statz(raw)
    assert out == {"reachable": True, "mode": "zygote", "jobs_total": 42, "rss_mb": 128.5}


def test_worker_statz_reachable_when_object(monkeypatch):
    monkeypatch.setenv("EASYCAD_WORKER_URL", "http://worker:9000")
    monkeypatch.setattr(m.urllib.request, "urlopen",
                        lambda *a, **k: _FakeResp(b'{"mode": "fresh", "jobs_total": 7}'))
    out = m._worker_statz()
    assert out == {"reachable": True, "mode": "fresh", "jobs_total": 7}
