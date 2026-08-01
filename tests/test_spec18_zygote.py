"""SPEC18: zygote-fork execution.

Two tiers:

* Always-on unit tests — the wire framing and the client's transport-failure
  mapping. These need neither CadQuery nor a running zygote.
* Gated integration tests — start a real zygote side-car and drive it. They need
  CadQuery and a fork-safe platform, so they run only on Linux with CadQuery
  importable (i.e. in the worker image / CI), matching where the zygote actually
  runs. On macOS dev boxes forking an OCP-imported process is unsafe, so they skip.
"""

import contextlib
import importlib.util
import os
import socket
import struct
import subprocess
import sys
import time
from pathlib import Path

import pytest

# In the built worker image the shared modules are vendored flat into one dir;
# in the repo they're split (worker/ has main/limits/zygote; app/ has
# cq_worker/code_guard). Put BOTH on the path so top-level imports resolve like
# they do in the image.
_REPO = Path(__file__).resolve().parent.parent
_WORKER = _REPO / "worker"
_APP = _REPO / "app"
# Insert app first, then worker, so worker ends up at sys.path[0]: both
# directories contain a `main.py`, and the worker one must win (app/main.py is a
# package module using relative imports and must only be imported as `app.main`).
for _p in (str(_APP), str(_WORKER)):
    if _p not in sys.path:
        sys.path.insert(0, _p)
_PYTHONPATH = os.pathsep.join([str(_WORKER), str(_APP)])

import zygote  # noqa: E402

_HAS_CADQUERY = importlib.util.find_spec("cadquery") is not None
_integration = pytest.mark.skipif(
    not (_HAS_CADQUERY and sys.platform == "linux"),
    reason="zygote fork path needs CadQuery on Linux (runs in the worker image/CI)",
)

BOX = "import cadquery as cq\nresult = cq.Workplane('XY').box(10, 10, 10)\n"


# ── Unit: wire framing ───────────────────────────────────────────────────────

def test_framing_roundtrip():
    a, b = socket.socketpair()
    zygote.send_msg(a, {"op": "execute", "code": BOX})
    assert zygote.recv_msg(b) == {"op": "execute", "code": BOX}
    a.close(); b.close()


def test_run_outage_is_tagged_worker_unavailable():
    # A dead/unreachable zygote socket is an operational outage, not a model error:
    # the payload must carry code="worker_unavailable" so the app raises the W1
    # retryable notice instead of looping repairs / showing a generic failed step.
    client = zygote.ZygoteClient("/nonexistent/easycad-zygote.sock")
    out = client.run(BOX)
    assert out["success"] is False
    assert out["code"] == "worker_unavailable"


def test_framed_reader_handles_partial_and_multiple():
    a, b = socket.socketpair()
    reader = zygote._FramedReader(b)
    payload = zygote.json.dumps({"k": "v" * 1000}).encode()
    frame = struct.pack(">I", len(payload)) + payload
    # Send the frame in two chunks: reader must return None then the full dict.
    a.sendall(frame[:10])
    assert reader.read() is None
    a.sendall(frame[10:])
    assert reader.read() == {"k": "v" * 1000}
    a.close(); b.close()


def test_framed_reader_raises_on_eof():
    a, b = socket.socketpair()
    a.close()  # peer gone → EOF
    with pytest.raises(ConnectionError):
        zygote._FramedReader(b).read()
    b.close()


def test_framed_reader_rejects_oversized_frame():
    """An untrusted client can't advertise a huge length to force unbounded
    buffering (review Standards P2)."""
    a, b = socket.socketpair()
    reader = zygote._FramedReader(b, max_bytes=100)
    a.sendall(struct.pack(">I", 1000))  # advertise 1000 > cap 100
    with pytest.raises(ConnectionError):
        reader.read()
    a.close(); b.close()


# ── Unit: client maps a dead zygote to a populated failure (never raises) ─────

def test_client_run_maps_unavailable_to_error():
    client = zygote.ZygoteClient("/tmp/easycad-zygote-does-not-exist.sock")
    out = client.run(BOX)
    assert out["success"] is False
    assert "zygote" in out["error"].lower()
    assert out["stl_base64"] is None and out["geometry_info"] is None


def test_client_export_maps_unavailable_to_error():
    client = zygote.ZygoteClient("/tmp/easycad-zygote-does-not-exist.sock")
    out = client.export(BOX, "step")
    assert out["success"] is False
    assert "zygote" in out["error"].lower()
    assert out["data_base64"] is None


def test_client_ping_false_when_down():
    assert zygote.ZygoteClient("/tmp/nope.sock").ping() is False


# ── Unit: single-zygote-per-container invariant (review round 3) ─────────────

def test_single_instance_lock_rejects_second_holder(tmp_path):
    """A second worker process (e.g. uvicorn --workers 2) must fail fast rather
    than start a second zygote. flock is per open-file-description, so a second
    acquire on the same path — even in-process — is refused."""
    import main as worker_main
    lock = str(tmp_path / "z.lock")
    worker_main._acquire_single_instance_lock(lock)  # first holder: ok
    with pytest.raises(RuntimeError, match="single worker process"):
        worker_main._acquire_single_instance_lock(lock)  # second: refused
    if worker_main._zygote_lock_fd is not None:
        os.close(worker_main._zygote_lock_fd)
        worker_main._zygote_lock_fd = None


# ── Integration: a real zygote side-car ──────────────────────────────────────

@contextlib.contextmanager
def _run_zygote(sock, env_extra=None):
    """Start `python -m zygote serve <sock>`, wait for warmup, yield a client."""
    env = {**os.environ, "PYTHONPATH": _PYTHONPATH, **(env_extra or {})}
    proc = subprocess.Popen([sys.executable, "-m", "zygote", "serve", sock],
                            cwd=str(_WORKER), env=env)
    client = zygote.ZygoteClient(sock)
    client._proc_pid = proc.pid  # for the fork-safety regression test
    try:
        for _ in range(300):  # up to ~60s for the CadQuery import
            if proc.poll() is not None:
                raise RuntimeError("zygote exited during warmup")
            if client.ping():
                break
            time.sleep(0.2)
        else:
            raise RuntimeError("zygote did not become ready")
        yield client
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


@pytest.fixture
def zygote_client(tmp_path):
    with _run_zygote(str(tmp_path / "zygote.sock")) as client:
        yield client


@_integration
def test_execute_box_success(zygote_client):
    out = zygote_client.run(BOX)
    assert out["success"] is True, out.get("error")
    assert out["stl_base64"] and out["geometry_info"]
    assert "Geometry info" in out["geometry_info"]


@_integration
def test_execute_missing_result_is_user_error(zygote_client):
    out = zygote_client.run("import cadquery as cq\nx = 1\n")
    assert out["success"] is False
    assert "result" in out["error"].lower()
    assert out["stl_base64"] is None


@_integration
def test_export_step_success(zygote_client):
    out = zygote_client.export(BOX, "step")
    assert out["success"] is True, out.get("error")
    assert out["data_base64"]


@_integration
def test_export_rejects_unknown_format(zygote_client):
    out = zygote_client.export(BOX, "obj")
    assert out["success"] is False
    assert "format" in out["error"].lower()


@_integration
def test_concurrent_requests_are_byte_identical(zygote_client):
    """8 concurrent one-shot children must return the same STL — proves no
    cross-request state corruption (each child is a private CoW address space)."""
    from concurrent.futures import ThreadPoolExecutor

    model = ("import cadquery as cq\n"
             "result = (cq.Workplane('XY').box(20,20,10)"
             ".edges('|Z').fillet(2).faces('>Z').workplane().hole(6))\n")
    with ThreadPoolExecutor(max_workers=8) as ex:
        outs = list(ex.map(lambda _: zygote_client.run(model), range(8)))
    assert all(o["success"] for o in outs), [o.get("error") for o in outs]
    assert len({o["stl_base64"] for o in outs}) == 1


@_integration
def test_zygote_parent_single_threaded_after_import(zygote_client):
    """Fork-safety regression + P0 guard: once ready (ping ok) the zygote has
    imported CadQuery/OCP, and it must still be a single OS thread — otherwise
    forking risks deadlock. A dependency bump that starts an import-time thread
    fails here loudly. Reading /proc/<pid>/task counts NATIVE threads, not just
    Python's view."""
    pid = zygote_client._proc_pid
    # A real execute confirms OCP is imported and usable in the parent's CoW base.
    assert zygote_client.run(BOX)["success"]
    n = len(os.listdir(f"/proc/{pid}/task"))
    assert n == 1, f"zygote has {n} OS threads; fork is unsafe"


@_integration
def test_execute_latency_is_amortized(zygote_client):
    """P0 guard: with CadQuery preloaded in the zygote, a warm request must be
    far faster than a cold import (~1.5s). If the import were paid per request
    (the bug the review caught), this would exceed a second."""
    zygote_client.run(BOX)  # discard the very first (any lazy first-use init)
    t0 = time.perf_counter()
    out = zygote_client.run(BOX)
    dt = time.perf_counter() - t0
    assert out["success"], out.get("error")
    assert dt < 1.0, f"warm execute took {dt:.2f}s — import not amortized?"


@_integration
def test_stats_exposes_metrics(zygote_client):
    zygote_client.run(BOX)
    s = zygote_client.stats()
    assert s["ok"] and s["mode"] == "zygote"
    assert s["import_seconds"] > 0        # CadQuery was imported in the parent
    assert s["jobs_total"] >= 1
    assert s["fork_exec_p50_ms"] is not None


@_integration
def test_isolation_state_does_not_leak_between_requests(zygote_client):
    """A first request that mutates process-global state cannot make it visible
    to a second request — the one-shot child lifecycle, observed externally."""
    # First request stashes a module global on the `math` module.
    r1 = zygote_client.run(
        "import cadquery as cq, math\nmath._leaked = 42\n"
        "result = cq.Workplane('XY').box(1,1,1)\n"
    )
    assert r1["success"], r1.get("error")
    # Second request would raise AttributeError if the global leaked across.
    r2 = zygote_client.run(
        "import cadquery as cq, math\nassert not hasattr(math, '_leaked')\n"
        "result = cq.Workplane('XY').box(1,1,1)\n"
    )
    assert r2["success"] is True, r2.get("error")


@_integration
def test_timeout_then_recovery(tmp_path):
    """A runaway request times out with the export-vs-execute-correct message,
    and — since each child is one-shot — a later valid request still succeeds."""
    # Wall-clock timeout (3s) well below the CPU rlimit (60s) so the supervisor's
    # timeout path fires deterministically rather than racing RLIMIT_CPU.
    with _run_zygote(str(tmp_path / "z.sock"),
                     {"CADQUERY_WORKER_TIMEOUT_SECONDS": "3",
                      "EASYCAD_WORKER_CPU_SECONDS": "60"}) as client:
        slow = client.run("import cadquery as cq\nwhile True:\n    pass\n"
                          "result = cq.Workplane('XY').box(1,1,1)\n")
        assert slow["success"] is False
        assert "timed out" in slow["error"].lower()
        assert "Execution" in slow["error"]  # execute op, not "Export"
        # Zygote unharmed: next request works.
        ok = client.run(BOX)
        assert ok["success"] is True, ok.get("error")


@_integration
def test_export_timeout_message_says_export(tmp_path):
    """Contract parity (review P2): export failures read 'Export …', not
    'Execution …', matching limits.export."""
    with _run_zygote(str(tmp_path / "z.sock"),
                     {"CADQUERY_WORKER_TIMEOUT_SECONDS": "3",
                      "EASYCAD_WORKER_CPU_SECONDS": "60"}) as client:
        out = client.export("import cadquery as cq\nwhile True:\n    pass\n", "step")
        assert out["success"] is False
        assert out["error"].startswith("Export timed out")


# ── HTTP seam (FastAPI TestClient): guard + routing through the real app ──────

@_integration
def test_http_seam_guard_and_execute(tmp_path, monkeypatch):
    """Drive the actual worker HTTP app in zygote mode: guard rejection happens
    before any fork, and a valid request routes through the zygote to a result."""
    monkeypatch.setenv("EASYCAD_WORKER_ZYGOTE", "1")
    monkeypatch.setenv("EASYCAD_WORKER_ZYGOTE_SOCK", str(tmp_path / "http.sock"))
    # main launches its own zygote subprocess; ensure it can import the shared
    # modules split across worker/ + app/ in the repo (flat in the image).
    monkeypatch.setenv("PYTHONPATH", _PYTHONPATH)
    import importlib
    import main as worker_main  # top-level module from worker/ (on sys.path)
    worker_main = importlib.reload(worker_main)  # re-read env
    from fastapi.testclient import TestClient

    with TestClient(worker_main.app) as c:  # lifespan starts the real zygote
        assert c.get("/readyz").json()["ready"] is True
        bad = c.post("/execute", json={"code": "import os\nresult=os.getcwd()\n"}).json()
        assert bad["success"] is False and "guard" in bad["error"].lower()
        good = c.post("/execute", json={"code": BOX}).json()
        assert good["success"] is True and good["stl_base64"]
        stats = c.get("/statz").json()
        assert stats["jobs_total"] >= 1
        assert "request_wait_p50_ms" in stats  # SPEC18 request-wait metric


# ── Integration: zygote enforces its own boundary (review round 2) ───────────

@_integration
def test_foreign_process_cannot_submit_jobs(zygote_client):
    """Control-socket bypass guard (Standards P1): only the launching parent may
    submit. A different process (here a subprocess, so a different PID than the
    zygote's parent) is refused at accept via SO_PEERCRED."""
    prog = (
        "import socket, struct, json, sys\n"
        f"s = socket.socket(socket.AF_UNIX); s.connect({zygote_client.sock_path!r})\n"
        "b = json.dumps({'op': 'ping'}).encode()\n"
        "s.sendall(struct.pack('>I', len(b)) + b)\n"
        "s.settimeout(5)\n"
        "try:\n"
        "    data = s.recv(4)\n"
        "except OSError:\n"
        "    data = b''\n"
        "sys.stdout.write('EMPTY' if not data else 'GOTDATA')\n"
    )
    r = subprocess.run([sys.executable, "-c", prog], capture_output=True,
                       text=True, timeout=30)
    assert r.stdout.strip() == "EMPTY", f"foreign client got a response: {r.stdout!r}"
    # The legitimate client (this process == the zygote's parent) still works.
    assert zygote_client.ping() is True


@_integration
def test_unknown_op_is_rejected_not_executed(zygote_client):
    """A malformed/unknown op must return an error, not fall into execute."""
    s = socket.socket(socket.AF_UNIX)
    s.connect(zygote_client.sock_path)
    body = zygote.json.dumps({"op": "bogus"}).encode()
    s.sendall(struct.pack(">I", len(body)) + body)
    resp = zygote.recv_msg(s)
    s.close()
    assert resp["success"] is False and "unknown operation" in resp["error"]


@_integration
def test_oversized_job_frame_dropped(zygote_client):
    """Server drops a connection that advertises an over-limit frame."""
    s = socket.socket(socket.AF_UNIX)
    s.connect(zygote_client.sock_path)
    s.sendall(struct.pack(">I", zygote.MAX_FRAME_BYTES + 1) + b"x" * 16)
    s.settimeout(5)
    with pytest.raises((ConnectionError, OSError)):
        zygote.recv_msg(s)  # server closed without sending a frame
    s.close()


@_integration
def test_fresh_vs_zygote_parity(zygote_client, monkeypatch):
    """The zygote must return byte-identical geometry to the fresh subprocess
    path for the same code (review Spec P2 parity)."""
    monkeypatch.setenv("PYTHONPATH", _PYTHONPATH)  # so limits.run finds cq_worker
    import limits
    fresh = limits.run(BOX)
    zyg = zygote_client.run(BOX)
    assert fresh["success"] and zyg["success"], (fresh.get("error"), zyg.get("error"))
    assert fresh["geometry_info"] == zyg["geometry_info"]
    assert fresh["stl_base64"] == zyg["stl_base64"]


# ── Integration: full HTTP seam, both modes (review round 3) ─────────────────

@contextlib.contextmanager
def _http_worker(monkeypatch, tmp_path, mode, **env):
    """Reload worker `main` in fresh or zygote mode and yield a TestClient with
    its lifespan active (zygote mode starts the real side-car)."""
    monkeypatch.setenv("PYTHONPATH", _PYTHONPATH)  # subprocesses find cq_worker
    monkeypatch.setenv("EASYCAD_WORKER_ZYGOTE_SOCK", str(tmp_path / "z.sock"))
    monkeypatch.setenv("EASYCAD_WORKER_ZYGOTE_LOCK", str(tmp_path / "z.lock"))
    if mode == "zygote":
        monkeypatch.setenv("EASYCAD_WORKER_ZYGOTE", "1")
    else:
        monkeypatch.delenv("EASYCAD_WORKER_ZYGOTE", raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    import importlib
    import main as worker_main
    worker_main = importlib.reload(worker_main)
    from fastapi.testclient import TestClient
    with TestClient(worker_main.app) as c:
        yield c


@_integration
@pytest.mark.parametrize("mode", ["fresh", "zygote"])
def test_http_contract_parity(monkeypatch, tmp_path, mode):
    """Same HTTP contract in both modes: success, user error, guard rejection,
    export, readiness."""
    with _http_worker(monkeypatch, tmp_path, mode) as c:
        good = c.post("/execute", json={"code": BOX}).json()
        assert good["success"] and good["stl_base64"] and good["geometry_info"]

        ue = c.post("/execute", json={"code": "import cadquery as cq\nx = 1\n"}).json()
        assert ue["success"] is False and "result" in ue["error"].lower()

        gr = c.post("/execute", json={"code": "import os\nresult = os.getcwd()\n"}).json()
        assert gr["success"] is False and "guard" in gr["error"].lower()

        ex = c.post("/export", json={"code": BOX, "format": "step"}).json()
        assert ex["success"] and ex["data_base64"]

        assert c.get("/readyz").json()["ready"] is True


@_integration
def test_http_timeout_and_recovery(monkeypatch, tmp_path):
    """A runaway request times out over HTTP; the worker recovers for the next."""
    with _http_worker(monkeypatch, tmp_path, "zygote",
                      CADQUERY_WORKER_TIMEOUT_SECONDS="3",
                      EASYCAD_WORKER_CPU_SECONDS="60") as c:
        slow = c.post("/execute",
                      json={"code": "import cadquery as cq\nwhile True:\n    pass\n"}).json()
        assert slow["success"] is False and "timed out" in slow["error"].lower()
        assert c.post("/execute", json={"code": BOX}).json()["success"] is True


@_integration
def test_http_concurrent_admission(monkeypatch, tmp_path):
    """Concurrent requests beyond the concurrency cap all complete (admission
    serialises them, no deadlock) and the request-wait metric is recorded."""
    from concurrent.futures import ThreadPoolExecutor

    with _http_worker(monkeypatch, tmp_path, "zygote") as c:
        def hit(_):
            return c.post("/execute", json={"code": BOX}).json()["success"]
        with ThreadPoolExecutor(max_workers=6) as ex:
            results = list(ex.map(hit, range(12)))
        assert all(results)
        assert "request_wait_p50_ms" in c.get("/statz").json()


@_integration
def test_zygote_at_least_5x_faster_than_fresh(zygote_client, monkeypatch):
    """SPEC18 performance gate: warm zygote median must be >=5x the fresh median
    (fresh re-imports CadQuery per request)."""
    import statistics
    monkeypatch.setenv("PYTHONPATH", _PYTHONPATH)
    import limits

    fresh = []
    for _ in range(3):
        t = time.perf_counter()
        r = limits.run(BOX)
        fresh.append(time.perf_counter() - t)
        assert r["success"], r.get("error")

    zygote_client.run(BOX)  # discard first (any lazy first-use init)
    warm = []
    for _ in range(5):
        t = time.perf_counter()
        r = zygote_client.run(BOX)
        warm.append(time.perf_counter() - t)
        assert r["success"], r.get("error")

    fm, zm = statistics.median(fresh), statistics.median(warm)
    assert fm / zm >= 5.0, f"fresh {fm:.2f}s vs zygote {zm:.3f}s = {fm/zm:.1f}x (<5x)"


@_integration
def test_oversized_result_frame_is_contained(tmp_path):
    """A result larger than EASYCAD_WORKER_MAX_RESULT_BYTES fails the one request
    with a bounded error instead of OOMing the long-lived supervisor."""
    with _run_zygote(str(tmp_path / "z.sock"),
                     {"EASYCAD_WORKER_MAX_RESULT_BYTES": "100"}) as client:
        out = client.run(BOX)  # even a tiny box STL exceeds 100 bytes
        assert out["success"] is False
        assert "larger than the worker limit" in out["error"]


# ── Integration: shutdown + concurrent containment (review round 4) ──────────

@_integration
def test_shutdown_reaps_inflight_children(tmp_path):
    """On worker shutdown the zygote must kill and reap in-flight untrusted CAD
    children, not orphan them (Standards P1)."""
    import threading

    sock = str(tmp_path / "z.sock")
    proc = subprocess.Popen([sys.executable, "-m", "zygote", "serve", sock],
                            cwd=str(_WORKER),
                            env={**os.environ, "PYTHONPATH": _PYTHONPATH})
    client = zygote.ZygoteClient(sock)
    for _ in range(300):
        if proc.poll() is not None:
            raise RuntimeError("zygote exited during warmup")
        if client.ping():
            break
        time.sleep(0.2)

    # Start a long-running request so a child is in flight, then find that child
    # (its parent PID is the zygote).
    threading.Thread(
        target=lambda: client.run("import cadquery as cq\nwhile True:\n    pass\n"),
        daemon=True).start()

    def zygote_children():
        kids = []
        for p in os.listdir("/proc"):
            if not p.isdigit():
                continue
            try:
                if open(f"/proc/{p}/stat").read().split()[3] == str(proc.pid):
                    kids.append(int(p))
            except OSError:
                pass
        return kids

    kids = []
    for _ in range(60):
        kids = zygote_children()
        if kids:
            break
        time.sleep(0.1)
    assert kids, "no in-flight child appeared to test shutdown reaping"

    proc.terminate()  # SIGTERM -> zygote._shutdown kills+reaps children
    proc.wait(timeout=10)
    time.sleep(0.5)
    for pid in kids:
        assert not os.path.exists(f"/proc/{pid}"), f"child {pid} orphaned after shutdown"


@_integration
def test_concurrent_oversized_results_contained(tmp_path):
    """Concurrent over-limit results each fail with a bounded error and the
    long-lived supervisor survives (Spec P1 result-cap safety)."""
    from concurrent.futures import ThreadPoolExecutor

    with _run_zygote(str(tmp_path / "z.sock"),
                     {"EASYCAD_WORKER_MAX_RESULT_BYTES": "500"}) as client:
        with ThreadPoolExecutor(max_workers=6) as ex:
            outs = list(ex.map(lambda _: client.run(BOX), range(12)))
        assert all(o["success"] is False and "larger than the worker limit" in o["error"]
                   for o in outs), [o.get("error") for o in outs]
        # Supervisor still alive and responsive after the burst.
        assert client.ping() is True
