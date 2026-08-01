"""SPEC18 zygote-fork execution.

A single **zygote** process imports CadQuery/OCP exactly once and never executes
generated code. For each job it forks a one-shot child that inherits the import
via copy-on-write, applies the existing per-request resource limits, executes the
guarded code in a fresh scratch dir, writes the wire payload, and exits. The
child is never reused, so no executed-code state crosses a request boundary — the
SPEC12 isolation guarantee is preserved while the ~1.5s import leaves the hot
path (see `spikes/spec18/`).

Fork safety depends on the zygote being single-threaded at fork time, so it sets
`OMP_NUM_THREADS=1` (etc.) *before* importing CadQuery and starts no threads. It
therefore runs as its OWN process, distinct from the multithreaded uvicorn/asyncio
worker in `main.py`, which talks to it over an `AF_UNIX` socket.

This module has two halves:

* `ZygoteClient` — used inside the FastAPI process. Imports no CadQuery. Sends a
  framed job over the socket and returns the same wire payload the fresh path
  produces, mapping transport failures to a failed payload.
* `serve()` — the supervisor loop. Run as a subprocess: `python -m zygote serve
  <sock_path>`. Imports CadQuery, then multiplexes accept/fork/collect with
  `selectors` (no threads → stays fork-safe).

Wire framing: 4-byte big-endian length prefix + JSON body, on every channel.
"""

import json
import os
import selectors
import signal
import socket
import struct
import sys
import time

TIMEOUT_SECONDS = int(os.getenv("CADQUERY_WORKER_TIMEOUT_SECONDS", "120"))
# Cap the size of an inbound job frame (untrusted client input), reusing the
# worker's existing body bound. Result frames FROM our own forked children are
# trusted and can be large (base64 STL), so they are read uncapped.
MAX_FRAME_BYTES = int(os.getenv("EASYCAD_WORKER_MAX_BODY_BYTES", str(500_000)))
# Bound a result frame the long-lived supervisor buffers from a child, so a huge
# base64 STL/STEP fails the one request instead of OOMing the zygote. Size it
# against the CONTAINER budget, not the file limit: at peak a job costs roughly
# cap (buffered frame) + cap (re-encoded to send), and up to `concurrency` run at
# once, on top of the ~440 MB import. Default 64 MB keeps worst case
# (~2*64*concurrency) well inside a 1 GB container at the default concurrency of
# 2. Realistic STL/STEP are single-digit MB; operators with larger models must
# raise this AND the container mem_limit together.
MAX_RESULT_BYTES = int(os.getenv("EASYCAD_WORKER_MAX_RESULT_BYTES", str(64 * 1024 * 1024)))
_SINGLE_THREAD_ENV = (
    "OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
    "TBB_NUM_THREADS", "VECLIB_MAXIMUM_THREADS", "NUMEXPR_NUM_THREADS",
)


# ── Wire framing ─────────────────────────────────────────────────────────────

def send_msg(sock: socket.socket, obj: dict) -> None:
    data = json.dumps(obj).encode("utf-8")
    sock.sendall(struct.pack(">I", len(data)) + data)


def recv_msg(sock: socket.socket) -> dict:
    """Blocking read of one framed message (client side)."""
    header = _recv_exact(sock, 4)
    (n,) = struct.unpack(">I", header)
    return json.loads(_recv_exact(sock, n).decode("utf-8"))


def _recv_exact(sock: socket.socket, n: int) -> bytes:
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("socket closed before full message")
        buf += chunk
    return bytes(buf)


class _FrameTooLarge(ConnectionError):
    """A framed message exceeded its configured maximum size."""


class _FramedReader:
    """Incremental, non-blocking framed reader for the supervisor's `selectors`
    loop. `read()` consumes whatever is available and returns a full message dict
    once complete, else None; raises ConnectionError on EOF (peer closed) and
    `_FrameTooLarge` if a frame exceeds `max_bytes`.

    `max_bytes` bounds a single frame (and the buffer): for untrusted client
    frames it is `MAX_FRAME_BYTES`; for trusted-but-possibly-huge child result
    frames it is `MAX_RESULT_BYTES`. None disables the bound."""

    def __init__(self, sock: socket.socket, max_bytes: int | None = None):
        self.sock = sock
        self.max_bytes = max_bytes
        self.buf = bytearray()

    def read(self):
        chunk = self.sock.recv(65536)
        if not chunk:
            raise ConnectionError("peer closed")
        self.buf += chunk
        if self.max_bytes is not None and len(self.buf) > self.max_bytes + 4:
            raise _FrameTooLarge("frame exceeds maximum size")
        if len(self.buf) < 4:
            return None
        (n,) = struct.unpack(">I", self.buf[:4])
        if self.max_bytes is not None and n > self.max_bytes:
            raise _FrameTooLarge(f"frame length {n} exceeds maximum {self.max_bytes}")
        if len(self.buf) < 4 + n:
            return None
        payload = bytes(self.buf[4:4 + n])
        del self.buf[:4 + n]
        return json.loads(payload.decode("utf-8"))


# ── Client (FastAPI side, no CadQuery import) ────────────────────────────────

class ZygoteClient:
    def __init__(self, sock_path: str):
        self.sock_path = sock_path

    def _rpc(self, msg: dict, timeout: float) -> dict:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(timeout)
        try:
            s.connect(self.sock_path)
            send_msg(s, msg)
            return recv_msg(s)
        finally:
            s.close()

    def ping(self) -> bool:
        try:
            return self._rpc({"op": "ping"}, timeout=2.0).get("ok") is True
        except OSError:
            return False

    def stats(self) -> dict:
        try:
            return self._rpc({"op": "stats"}, timeout=2.0)
        except (OSError, ValueError):
            return {"ok": False}

    def run(self, code: str) -> dict:
        try:
            return self._rpc({"op": "execute", "code": code}, TIMEOUT_SECONDS + 15)
        except (OSError, ValueError) as exc:
            # A dead/unreachable zygote is an operational outage, not a model error:
            # tag it so the app raises the retryable W1 notice instead of looping
            # repairs or showing a generic failed step.
            return {"success": False, "stl_base64": None, "geometry_info": None,
                    "error": f"Zygote unavailable: {exc}", "code": "worker_unavailable"}

    def export(self, code: str, fmt: str) -> dict:
        try:
            return self._rpc({"op": "export", "code": code, "format": fmt},
                             TIMEOUT_SECONDS + 15)
        except (OSError, ValueError) as exc:
            return {"success": False, "data_base64": None,
                    "error": f"Zygote unavailable: {exc}"}


# ── Child: run one job, exactly once ─────────────────────────────────────────

def _execute_in_child(op: str, code: str, fmt, cq_worker) -> dict:
    """Run one job in this (forked) child and return the wire payload. The child
    already inherited the CadQuery import; it only needs a scratch dir."""
    import base64
    import shutil
    import tempfile

    tmp = tempfile.mkdtemp()  # on tmpfs; os._exit skips finalizers, so rm manually
    try:
        if op == "export":
            if fmt not in ("stl", "step"):
                return {"success": False, "data_base64": None,
                        "error": f"Unsupported format: {fmt}"}
            path = os.path.join(tmp, f"model.{fmt}")
            out = cq_worker.execute_job(code, path)
            if not out["success"]:
                return {"success": False, "data_base64": None, "error": out["error"]}
            with open(path, "rb") as f:
                data = base64.b64encode(f.read()).decode("ascii")
            return {"success": True, "data_base64": data, "error": None}

        # default: execute -> STL
        path = os.path.join(tmp, "model.stl")
        out = cq_worker.execute_job(code, path)
        if not out["success"]:
            return {"success": False, "stl_base64": None,
                    "geometry_info": None, "error": out["error"]}
        with open(path, "rb") as f:
            stl = base64.b64encode(f.read()).decode("ascii")
        return {"success": True, "stl_base64": stl,
                "geometry_info": out["geometry_info"], "error": None}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _crash_payload(op: str, detail: str) -> dict:
    # Match the fresh path's wording exactly (limits.run vs limits.export).
    verb = "Export" if op == "export" else "Execution"
    if op == "export":
        return {"success": False, "data_base64": None, "error": f"{verb} crashed: {detail}"}
    return {"success": False, "stl_base64": None, "geometry_info": None,
            "error": f"{verb} crashed: {detail}"}


def _timeout_payload(op: str) -> dict:
    verb = "Export" if op == "export" else "Execution"
    msg = f"{verb} timed out after {TIMEOUT_SECONDS}s."
    if op == "export":
        return {"success": False, "data_base64": None, "error": msg}
    # Carry a machine-readable code so the executor tags a real worker wall-clock
    # timeout as `execution_timeout` (W1 504 notice), not a generic failed step.
    return {"success": False, "stl_base64": None, "geometry_info": None,
            "error": msg, "code": "execution_timeout"}


def _guard_payload(op: str, reason: str) -> dict:
    msg = f"Code rejected by guard: {reason}"
    if op == "export":
        return {"success": False, "data_base64": None, "error": msg}
    return {"success": False, "stl_base64": None, "geometry_info": None, "error": msg}


def _oversize_payload(op: str) -> dict:
    verb = "Export" if op == "export" else "Execution"
    msg = f"{verb} produced a result larger than the worker limit."
    if op == "export":
        return {"success": False, "data_base64": None, "error": msg}
    return {"success": False, "stl_base64": None, "geometry_info": None, "error": msg}


# ── Supervisor loop ──────────────────────────────────────────────────────────

_WRITE_TIMEOUT = float(os.getenv("EASYCAD_WORKER_WRITE_TIMEOUT", str(TIMEOUT_SECONDS + 15)))
_MAX_DURATIONS = 256  # rolling window for fork+exec latency percentiles


def _rss_mb() -> float:
    try:
        with open("/proc/self/statm") as f:
            resident_pages = int(f.read().split()[1])
        return resident_pages * (os.sysconf("SC_PAGE_SIZE") / (1024 * 1024))
    except (OSError, ValueError, IndexError):
        return 0.0


_SO_PEERCRED = getattr(socket, "SO_PEERCRED", None)


class _Supervisor:
    def __init__(self, sock_path: str, cq_worker, limits, code_guard,
                 allowed_pid: int, import_seconds: float):
        self.sock_path = sock_path
        self.cq_worker = cq_worker
        self.limits = limits
        self.code_guard = code_guard
        self.allowed_pid = allowed_pid
        self.sel = selectors.DefaultSelector()
        self.jobs = {}          # result_sock -> job dict
        self.writes = {}        # conn -> {"buf", "off", "deadline"} (pending sends)
        self.live = set()       # every socket the zygote holds open (for child close)
        self.srv = None
        # SPEC18 operator metrics.
        self.m = {
            "import_seconds": round(import_seconds, 3),
            "jobs_total": 0, "user_errors_total": 0,
            "crashes_total": 0, "timeouts_total": 0,
            "durations_ms": [],  # rolling fork+exec durations
        }

    # ── child fd hygiene ─────────────────────────────────────────────────────
    def _close_all_but(self, keep: socket.socket) -> None:
        """In the forked child: drop every inherited socket except `keep`, so a
        child can neither accept new work nor see other requests' fds."""
        for s in list(self.live):
            if s is not keep:
                try:
                    s.close()
                except OSError:
                    pass
        for s in list(self.writes):
            if s is not keep:
                try:
                    s.close()
                except OSError:
                    pass
        for closer in (self.srv, self.sel):
            try:
                closer.close()
            except OSError:
                pass

    def _shutdown(self, signum=None, frame=None):
        """On SIGTERM/SIGINT: kill and reap every in-flight untrusted child so a
        worker reload/shutdown cannot orphan a running CAD process, then remove
        the socket and exit. Runs in the main thread; the process is ending."""
        for job in list(self.jobs.values()):
            try:
                os.kill(job["pid"], signal.SIGKILL)
            except ProcessLookupError:
                pass
        for job in list(self.jobs.values()):
            try:
                os.waitpid(job["pid"], 0)
            except ChildProcessError:
                pass
        try:
            if os.path.exists(self.sock_path):
                os.unlink(self.sock_path)
        except OSError:
            pass
        os._exit(0)

    # ── main loop ────────────────────────────────────────────────────────────
    def serve(self) -> None:
        if os.path.exists(self.sock_path):
            os.unlink(self.sock_path)
        self.srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.srv.bind(self.sock_path)
        self.srv.listen(128)
        self.srv.setblocking(False)
        self.sel.register(self.srv, selectors.EVENT_READ, ("listen", None))
        # Clean shutdown: reap children instead of orphaning them.
        signal.signal(signal.SIGTERM, self._shutdown)
        signal.signal(signal.SIGINT, self._shutdown)
        # Signal readiness on stdout — CadQuery is already imported at this point.
        print("READY", flush=True)

        while True:
            timeout = self._soonest_deadline()
            for key, _mask in self.sel.select(timeout):
                kind, data = key.data
                if kind == "listen":
                    self._accept()
                elif kind == "conn":
                    self._on_conn(key.fileobj, data)
                elif kind == "result":
                    self._on_result(key.fileobj, data)
                elif kind == "write":
                    self._on_writable(key.fileobj)
            self._reap_timeouts()

    def _soonest_deadline(self):
        deadlines = [j["deadline"] for j in self.jobs.values()]
        deadlines += [w["deadline"] for w in self.writes.values()]
        if not deadlines:
            return None
        return max(0.0, min(deadlines) - time.monotonic())

    def _authorized(self, conn: socket.socket) -> bool:
        """Only the trusted FastAPI parent may submit jobs. A generated-code
        child that escapes the guard runs elsewhere in the process tree, so its
        peer PID differs and it is refused — it cannot use this socket to fork
        work outside the guard/concurrency path."""
        if _SO_PEERCRED is None:
            return sys.platform != "linux"  # can't check off-Linux dev; zygote targets Linux
        try:
            raw = conn.getsockopt(socket.SOL_SOCKET, _SO_PEERCRED, struct.calcsize("3i"))
        except OSError:
            return False
        pid, _uid, _gid = struct.unpack("3i", raw)
        return pid == self.allowed_pid

    def _accept(self) -> None:
        conn, _ = self.srv.accept()
        if not self._authorized(conn):
            try:
                conn.close()
            except OSError:
                pass
            return
        conn.setblocking(False)
        self.live.add(conn)
        self.sel.register(conn, selectors.EVENT_READ,
                          ("conn", _FramedReader(conn, MAX_FRAME_BYTES)))

    def _on_conn(self, conn: socket.socket, reader: "_FramedReader") -> None:
        try:
            msg = reader.read()
        except (ConnectionError, OSError):
            self._drop(conn)
            return
        if msg is None:
            return  # need more bytes; selectors will call again
        self.sel.unregister(conn)
        op = msg.get("op")
        if op == "ping":
            self._begin_send(conn, {"ok": True})
            return
        if op == "stats":
            self._begin_send(conn, self._stats())
            return
        if op not in ("execute", "export"):
            # Unknown/malformed op must NOT fall through into execute.
            self._begin_send(conn, _crash_payload(op or "execute",
                                                  f"unknown operation {op!r}"))
            return
        self._fork_job(conn, op, msg)

    def _fork_job(self, conn: socket.socket, op: str, msg: dict) -> None:
        code = msg.get("code", "")
        # Supervisor-side guard (defence in depth): the FastAPI parent already
        # ran it, but re-check here so no unguarded code is ever forked, even if
        # a job reached the zygote by another path.
        ok, reason = self.code_guard.check(code)
        if not ok:
            self._begin_send(conn, _guard_payload(op, reason))
            return
        child_sock, zygote_sock = socket.socketpair()
        try:
            pid = os.fork()
        except OSError as exc:
            # PID/memory exhaustion must not unwind the sole serve loop; return a
            # contained failure and keep serving — later capacity stays usable.
            child_sock.close()
            zygote_sock.close()
            self._begin_send(conn, _crash_payload(op, f"cannot fork worker: {exc}"))
            return
        if pid == 0:
            # ── CHILD ── one job, then exit. Never returns.
            self._close_all_but(child_sock)
            zygote_sock.close()
            try:
                self.limits._set_limits()  # per-request rlimits, applied post-import
            except Exception:  # noqa: BLE001
                pass
            payload = _execute_in_child(op, msg.get("code", ""), msg.get("format"),
                                        self.cq_worker)
            try:
                send_msg(child_sock, payload)
            except OSError:
                pass
            child_sock.close()
            os._exit(0)
        # ── PARENT (zygote) ──
        child_sock.close()
        zygote_sock.setblocking(False)
        self.live.add(zygote_sock)
        job = {
            "pid": pid, "client": conn, "op": op,
            "started": time.monotonic(),
            "deadline": time.monotonic() + TIMEOUT_SECONDS,
            "reader": _FramedReader(zygote_sock, MAX_RESULT_BYTES),
        }
        self.jobs[zygote_sock] = job
        self.sel.register(zygote_sock, selectors.EVENT_READ, ("result", job))

    def _on_result(self, result_sock: socket.socket, job: dict) -> None:
        try:
            msg = job["reader"].read()
        except _FrameTooLarge:
            # An over-limit result must fail this one request, not OOM the
            # supervisor: kill the child, stop buffering, return a bounded error.
            try:
                os.kill(job["pid"], signal.SIGKILL)
            except ProcessLookupError:
                pass
            self._finish(result_sock, job)
            self.m["crashes_total"] += 1
            self._record_duration(job)
            self._begin_send(job["client"], _oversize_payload(job["op"]))
            return
        except (ConnectionError, OSError):
            # Child died without a full result (crash / rlimit kill).
            status = self._finish(result_sock, job)
            self.m["crashes_total"] += 1
            self._record_duration(job)
            self._begin_send(job["client"],
                             _crash_payload(job["op"], f"worker exited ({status})"))
            return
        if msg is None:
            return
        self._finish(result_sock, job)
        self.m["jobs_total"] += 1
        if not msg.get("success"):
            self.m["user_errors_total"] += 1
        self._record_duration(job)
        self._begin_send(job["client"], msg)

    def _reap_timeouts(self) -> None:
        now = time.monotonic()
        for result_sock, job in list(self.jobs.items()):
            if now >= job["deadline"]:
                try:
                    os.kill(job["pid"], signal.SIGKILL)
                except ProcessLookupError:
                    pass
                self._finish(result_sock, job)
                self.m["timeouts_total"] += 1
                self._record_duration(job)
                self._begin_send(job["client"], _timeout_payload(job["op"]))
        # Drop clients that will not drain their response within the window.
        for conn, w in list(self.writes.items()):
            if now >= w["deadline"]:
                self._drop(conn)

    def _finish(self, result_sock: socket.socket, job: dict) -> int:
        """Unregister + close the result socket, reap the child, return status."""
        try:
            self.sel.unregister(result_sock)
        except (KeyError, ValueError):
            pass
        self.live.discard(result_sock)
        try:
            result_sock.close()
        except OSError:
            pass
        self.jobs.pop(result_sock, None)
        try:
            _, status = os.waitpid(job["pid"], 0)
            return status
        except ChildProcessError:
            return 0

    def _record_duration(self, job: dict) -> None:
        dur_ms = (time.monotonic() - job["started"]) * 1000
        d = self.m["durations_ms"]
        d.append(round(dur_ms, 1))
        if len(d) > _MAX_DURATIONS:
            del d[0]

    # ── non-blocking, selector-managed response writes (P1) ──────────────────
    def _begin_send(self, conn: socket.socket, payload: dict) -> None:
        """Queue a framed response and flush as much as the socket accepts now.
        A slow/stalled client can never block the sole supervisor loop: the rest
        is drained on EVENT_WRITE, and a stuck write is dropped after a deadline."""
        data = json.dumps(payload).encode("utf-8")
        buf = struct.pack(">I", len(data)) + data
        state = {"buf": buf, "off": 0, "deadline": time.monotonic() + _WRITE_TIMEOUT}
        self.writes[conn] = state
        self.live.add(conn)
        self._flush(conn, state)

    def _flush(self, conn: socket.socket, state: dict) -> None:
        buf, off = state["buf"], state["off"]
        try:
            while off < len(buf):
                off += conn.send(buf[off:])
        except (BlockingIOError, InterruptedError):
            state["off"] = off
            try:  # ensure we're watching for writability
                self.sel.modify(conn, selectors.EVENT_WRITE, ("write", None))
            except (KeyError, ValueError):
                self.sel.register(conn, selectors.EVENT_WRITE, ("write", None))
            return
        except OSError:
            self._drop(conn)
            return
        # Fully sent.
        self._drop(conn)

    def _on_writable(self, conn: socket.socket) -> None:
        state = self.writes.get(conn)
        if state is None:
            self._drop(conn)
            return
        self._flush(conn, state)

    def _drop(self, conn: socket.socket) -> None:
        try:
            self.sel.unregister(conn)
        except (KeyError, ValueError):
            pass
        self.live.discard(conn)
        self.writes.pop(conn, None)
        try:
            conn.close()
        except OSError:
            pass

    # ── metrics ──────────────────────────────────────────────────────────────
    def _stats(self) -> dict:
        d = sorted(self.m["durations_ms"])
        p50 = d[len(d) // 2] if d else None
        p95 = d[max(0, int(len(d) * 0.95) - 1)] if d else None
        return {
            "ok": True,
            "mode": "zygote",
            "import_seconds": self.m["import_seconds"],
            "jobs_total": self.m["jobs_total"],
            "user_errors_total": self.m["user_errors_total"],
            "crashes_total": self.m["crashes_total"],
            "timeouts_total": self.m["timeouts_total"],
            "inflight": len(self.jobs),
            "fork_exec_p50_ms": p50,
            "fork_exec_p95_ms": p95,
            "rss_mb": round(_rss_mb(), 1),
        }


def serve(sock_path: str) -> None:
    # Pin native libs to one thread BEFORE importing CadQuery so the zygote is
    # single-threaded at fork time — the fork-safety precondition.
    for k in _SINGLE_THREAD_ENV:
        os.environ[k] = "1"
    os.environ["OMP_DYNAMIC"] = "FALSE"

    import cq_worker   # shared execution core (vendored into the worker image)
    import limits      # reuse the exact per-request rlimit setup
    import code_guard  # supervisor-side AST guard (defence in depth)

    # Only the launching FastAPI process may submit jobs (checked via SO_PEERCRED
    # on accept). Capture its PID now, before any fork.
    allowed_pid = os.getppid()

    # SPEC18 P0: import CadQuery/OCP ONCE, here in the parent, BEFORE accepting
    # or forking any job. Children inherit this via copy-on-write, so no request
    # pays the ~1.5s import. This must stay single-threaded (env above) to keep
    # forking safe — see spikes/spec18/fork_spike.py and the regression test.
    t0 = time.monotonic()
    import cadquery  # noqa: F401
    import OCP  # noqa: F401
    import_seconds = time.monotonic() - t0

    _Supervisor(sock_path, cq_worker, limits, code_guard,
                allowed_pid, import_seconds).serve()


def _main(argv) -> int:
    if len(argv) >= 3 and argv[1] == "serve":
        serve(argv[2])
        return 0
    sys.stderr.write("usage: python -m zygote serve <sock_path>\n")
    return 2


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv))
