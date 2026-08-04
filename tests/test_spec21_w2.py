"""SPEC21 W2 — daily crash report (file sink + lazy digest).

`execute`/`export_model` are monkeypatched to simulate worker failures; the crash
dir is redirected to a tmp path. Covers: record append + scrub + unwritable no-op,
record-exactly-once (chokepoint, not the exception handler), worker failure
labelled service:worker via op_error, export outage reaching the chokepoint, a
normal HTTP-200 failed generation NOT recorded, digest grouping, at-most-once
marker (no re-send after a simulated restart), count-based retention, and the
prod boot hard-fail without ADMIN_EMAIL.
"""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app.main as m
from app import crashlog
from app.cadquery_exec import ExecResult, ExportResult
from app.main import app

BOX = "import cadquery as cq\nresult = cq.Workplane('XY').box(10, 10, 10)\n"


async def _generate_box(*_args, **_kwargs):
    return BOX


@pytest.fixture(autouse=True)
def _crash_tmp(monkeypatch, tmp_path):
    monkeypatch.setenv("EASYCAD_CRASH_DIR", str(tmp_path))
    crashlog._reset_for_tests()
    m._report_day = ""  # clear the in-memory daily short-circuit
    yield


def _lines(tmp_path) -> list[dict]:
    files = list(Path(tmp_path).glob("crashes-*.jsonl"))
    if not files:
        return []
    return [json.loads(l) for f in files for l in f.read_text().splitlines() if l.strip()]


# ── record: append, scrub, unwritable no-op ───────────────────────────────────

def test_record_appends_scrubbed_line(tmp_path):
    crashlog.record({"kind": "error", "exc_message": "key sk-abcdef0123456789 leaked", "trace_id": "t"})
    rows = _lines(tmp_path)
    assert len(rows) == 1
    assert "sk-abcdef" not in rows[0]["exc_message"]
    assert "<redacted>" in rows[0]["exc_message"]


def test_record_noop_on_unwritable_dir(monkeypatch, tmp_path):
    # Point at a path under a FILE (can't mkdir) → record warns once and no-ops.
    bad = tmp_path / "afile"
    bad.write_text("x")
    monkeypatch.setenv("EASYCAD_CRASH_DIR", str(bad / "sub"))
    crashlog.record({"kind": "error"})  # must not raise
    assert not list((tmp_path).glob("**/crashes-*.jsonl"))


# ── chokepoint: exactly-once, service labelling, HTTP-200 not recorded ─────────

def test_unhandled_exception_recorded_exactly_once(tmp_path):
    @app.get("/_boom_w2")
    def _boom():
        raise RuntimeError("boom-w2")

    TestClient(app, raise_server_exceptions=False).get("/_boom_w2", headers={"x-real-ip": "2.0.0.1"})
    rows = [r for r in _lines(tmp_path) if r["path"] == "/_boom_w2"]
    assert len(rows) == 1
    assert rows[0]["kind"] == "error" and rows[0]["service"] == "app"
    assert rows[0]["exc_class"] == "RuntimeError"


def test_worker_failure_labelled_service_worker(monkeypatch, tmp_path):
    monkeypatch.setattr(m, "generate_code", _generate_box)
    monkeypatch.setattr(m, "execute",
                        lambda code: ExecResult(False, error="down", code="worker_unavailable"))
    r = TestClient(app).post("/api/chat", json={"prompt": "x", "auto_refine": False, "current_code": BOX},
                             headers={"x-real-ip": "2.0.0.2"})
    assert r.status_code == 503
    rows = [r for r in _lines(tmp_path) if r["path"] == "/api/chat"]
    assert len(rows) == 1
    assert rows[0]["service"] == "worker" and rows[0]["kind"] == "operational"
    assert rows[0]["code"] == "worker_unavailable"


def test_export_outage_reaches_chokepoint(monkeypatch, tmp_path):
    # Create a successful manual step, then an export-time worker outage must reach
    # the chokepoint as service:worker (export is now symmetric with execute).
    monkeypatch.setattr(m, "execute",
                        lambda code: ExecResult(True, stl_base64="AA==", geometry_info="# i"))
    client = TestClient(app)
    r = client.post("/api/execute-manual", json={"code": BOX}, headers={"x-real-ip": "2.0.0.3"})
    step_id = r.json()["step"]["id"]
    monkeypatch.setattr(m, "export_model", lambda code, fmt: ExportResult(code="worker_unavailable"))
    r2 = client.get(f"/api/export/{step_id}/step", headers={"x-real-ip": "2.0.0.3"})
    assert r2.status_code == 503
    rows = [r for r in _lines(tmp_path) if "/step" in r["path"]]
    assert len(rows) == 1 and rows[0]["service"] == "worker"


def test_http_200_failed_generation_not_recorded(monkeypatch, tmp_path):
    # A CadQuery that runs but errors (no operational code) is a product-flow 200,
    # NOT a crash — it must leave no crash line.
    monkeypatch.setattr(m, "generate_code", _generate_box)
    monkeypatch.setattr(m, "execute", lambda code: ExecResult(False, error="NameError: cq"))
    monkeypatch.setattr(m, "MAX_REPAIR", 0)
    r = TestClient(app).post("/api/chat", json={"prompt": "x", "auto_refine": False, "current_code": BOX},
                             headers={"x-real-ip": "2.0.0.4"})
    assert r.status_code == 200 and r.json()["step"]["success"] is False
    assert _lines(tmp_path) == []


# ── digest, marker, retention ─────────────────────────────────────────────────

def test_build_digest_groups_by_signature(tmp_path):
    import time
    date = time.strftime("%Y-%m-%d", time.gmtime())
    tb = 'File "/a.py", line 1, in f\n'
    for _ in range(3):
        crashlog.record({"kind": "error", "service": "app", "exc_class": "ValueError",
                         "traceback_tail": tb, "exc_message": "bad", "trace_id": "t"})
    crashlog.record({"kind": "operational", "service": "worker", "code": "worker_unavailable"})
    n, subject, body = crashlog.build_digest(date)
    assert n == 4
    assert "4 crashes" in subject
    assert "3×" in body and "ValueError" in body
    assert "app=3" in body and "worker=1" in body


def test_digest_streams_large_file(tmp_path):
    # A bad day can produce a large JSONL; build_digest must aggregate it streaming
    # (bounded by distinct signatures) and still count/group correctly.
    import time
    date = time.strftime("%Y-%m-%d", time.gmtime())
    tb = 'File "/a.py", line 9, in g\n'
    for _ in range(5000):
        crashlog.record({"kind": "error", "service": "app", "exc_class": "KeyError",
                         "traceback_tail": tb, "exc_message": "boom", "trace_id": "t"})
    n, subject, body = crashlog.build_digest(date)
    assert n == 5000 and "5000×" in body and "KeyError" in body


def test_zero_crash_day_is_heartbeat(tmp_path):
    n, subject, body = crashlog.build_digest("1999-01-01")
    assert n == 0 and "0 crashes" in subject and "quiet" in body.lower()


def test_claim_report_at_most_once(tmp_path):
    assert crashlog.claim_report("2030-01-01") is True
    # A simulated restart (module guard cleared) with the marker present: the
    # atomic claim fails, so no re-send. At-most-once.
    assert crashlog.claim_report("2030-01-01") is False


def test_retention_keeps_newest_three(tmp_path):
    for d in ("2030-01-01", "2030-01-02", "2030-01-03", "2030-01-04", "2030-01-05"):
        (tmp_path / f"crashes-{d}.jsonl").write_text("{}\n")
    (tmp_path / "reports").mkdir()
    for d in ("2030-01-01", "2030-01-02", "2030-01-03", "2030-01-04"):
        (tmp_path / "reports" / f"{d}.sent").write_text("")
    crashlog.apply_retention(keep=3)
    crashes = sorted(p.name for p in tmp_path.glob("crashes-*.jsonl"))
    assert crashes == ["crashes-2030-01-03.jsonl", "crashes-2030-01-04.jsonl", "crashes-2030-01-05.jsonl"]
    # Markers pruned INDEPENDENTLY by their own count (newest 3), not vs crash dates.
    markers = sorted(p.name for p in (tmp_path / "reports").glob("*.sent"))
    assert markers == ["2030-01-02.sent", "2030-01-03.sent", "2030-01-04.sent"]


def test_marker_retention_bounded_without_crash_files(tmp_path):
    # A healthy app writes no crash file but one heartbeat marker per day — markers
    # must stay bounded (the old coupling to the oldest crash date grew forever).
    (tmp_path / "reports").mkdir()
    for d in ("2030-02-01", "2030-02-02", "2030-02-03", "2030-02-04", "2030-02-05"):
        (tmp_path / "reports" / f"{d}.sent").write_text("")
    crashlog.apply_retention(keep=3)
    assert not list(tmp_path.glob("crashes-*.jsonl"))  # none exist
    markers = sorted(p.name for p in (tmp_path / "reports").glob("*.sent"))
    assert markers == ["2030-02-03.sent", "2030-02-04.sent", "2030-02-05.sent"]


def test_daily_report_sends_once_then_short_circuits(monkeypatch, tmp_path):
    sent = []
    monkeypatch.setattr(m, "ADMIN_EMAIL", "ops@example.com")
    monkeypatch.setattr(m, "send_mail", lambda to, subj, body: sent.append((to, subj)))
    m._report_day = ""
    m._maybe_send_daily_report()
    m._maybe_send_daily_report()  # in-memory short-circuit
    assert len(sent) == 1
    # Simulated restart: clear the in-memory guard; the on-disk marker still blocks.
    m._report_day = ""
    m._maybe_send_daily_report()
    assert len(sent) == 1


def test_transient_claim_error_retries_next_request(monkeypatch, tmp_path):
    # A transient FS error (claim_report → None) must NOT mark the day done, so a
    # later request retries instead of losing the report until the next UTC day.
    monkeypatch.setattr(m, "ADMIN_EMAIL", "ops@example.com")
    sent = []
    monkeypatch.setattr(m, "send_mail", lambda *a, **k: sent.append(a))
    outcomes = [None, True]  # first request errors transiently, second wins the claim
    monkeypatch.setattr(m.crashlog, "claim_report", lambda date: outcomes.pop(0))
    m._report_day = ""
    m._maybe_send_daily_report()
    assert sent == [] and m._report_day == ""  # not marked → will retry
    m._maybe_send_daily_report()
    assert len(sent) == 1  # retry sent it


def test_crash_report_disabled_skips_mail_but_still_claims(monkeypatch, tmp_path):
    # EASYCAD_CRASH_REPORT=0 suppresses the email; the claim + retention still run
    # (so the marker is written and at-most-once still holds).
    sent = []
    monkeypatch.setattr(m, "ADMIN_EMAIL", "ops@example.com")
    monkeypatch.setattr(m, "CRASH_REPORT", False)
    monkeypatch.setattr(m, "send_mail", lambda *a, **k: sent.append(a))
    m._report_day = ""
    m._maybe_send_daily_report()
    assert sent == []  # mail suppressed
    today = __import__("time").strftime("%Y-%m-%d", __import__("time").gmtime())
    assert (tmp_path / "reports" / f"{today}.sent").exists()  # day still claimed


# ── prod boot hard-fail without ADMIN_EMAIL ───────────────────────────────────

def test_prod_boot_requires_admin_email(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "a-strong-secret")
    monkeypatch.setattr(m, "SECURE_COOKIES", True)
    monkeypatch.setattr(m, "ADMIN_EMAIL", "")
    with pytest.raises(RuntimeError, match="ADMIN_EMAIL"):
        m._check_required_env()
    monkeypatch.setattr(m, "ADMIN_EMAIL", "ops@example.com")
    m._check_required_env()  # now boots
