"""SPEC18 fork-zygote feasibility spike — part 1: fork-safety + latency.

Decide whether we can import CadQuery/OCP ONCE in a parent and then fork a
one-shot child per request (reusing the import via CoW) instead of paying the
~1.5s import every request.

Two hard questions:
  1. Is the parent single-threaded after importing OCP? (fork of a multithreaded
     process risks deadlocks — locks held by threads that don't survive fork.)
  2. Does fork -> exec cadquery -> export -> exit work, how fast, vs cold import?

Run (Linux target): docker run --rm -i easycad-worker:spike python - < fork_spike.py
NOTE: production target is Linux; macOS fork semantics differ (CoreFoundation).
"""
import os
import sys
import time
import select
import signal
import statistics
import resource

# Pin native libs to a single thread BEFORE importing OCP so the parent stays
# fork-safe. Threads for real work are started inside the child, after fork.
# HARD assignment (not setdefault) so a pre-set multithread value can't win.
for _k in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
           "TBB_NUM_THREADS", "VECLIB_MAXIMUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ[_k] = "1"
os.environ["OMP_DYNAMIC"] = "FALSE"  # libgomp rejects "1"; must be TRUE/FALSE


def rss_mb():
    val = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return val / (1024 * 1024) if sys.platform == "darwin" else val / 1024


def thread_census(label):
    import threading
    names = [t.name for t in threading.enumerate()]
    os_tasks = None
    try:
        os_tasks = len(os.listdir(f"/proc/{os.getpid()}/task"))
    except FileNotFoundError:
        pass
    print(f"[{label}] python threads={len(names)} {names} "
          f"os_tasks={os_tasks} rss={rss_mb():.0f}MB")
    return len(names), os_tasks


BOX_SRC = 'import cadquery as cq\nresult = cq.Workplane("XY").box(10, 10, 10)\n'


def child_entry(src, out_path, pipe_w):
    """Runs in the forked child: limits, exec user code, export STL, signal."""
    try:
        # Per-request limits, applied AFTER import (already inherited via CoW).
        try:
            resource.setrlimit(resource.RLIMIT_CPU, (30, 30))
        except (ValueError, OSError):
            pass
        ns = {}
        exec(compile(src, "<user>", "exec"), ns)
        import cadquery as cq
        cq.exporters.export(ns["result"], out_path)
        os.write(pipe_w, b"OK")
    except BaseException as e:  # noqa: BLE001
        try:
            os.write(pipe_w, ("ERR:" + repr(e))[:200].encode())
        except OSError:
            pass
        os._exit(1)
    os._exit(0)


def fork_once(src, out_path, timeout=30.0):
    """Fork a one-shot child; return (ok, detail, secs).

    Deadlock-safe: the parent waits for the child's readiness byte with a
    bounded `select`, so a child that hangs (e.g. a real fork deadlock) is
    detected and killed instead of blocking the parent forever.
    """
    r, w = os.pipe()
    t0 = time.perf_counter()
    pid = os.fork()
    if pid == 0:
        os.close(r)
        child_entry(src, out_path, w)
        os._exit(0)  # unreachable
    # parent
    os.close(w)
    try:
        # (A) Bounded wait for the child's signal — this is the deadlock guard.
        ready, _, _ = select.select([r], [], [], timeout)
        if not ready:
            os.kill(pid, signal.SIGKILL)
            os.waitpid(pid, 0)
            return False, "TIMEOUT/DEADLOCK (no signal)", time.perf_counter() - t0
        msg = os.read(r, 256)
    finally:
        os.close(r)
    # (B) Bounded reap — child may signal OK then hang before exit.
    deadline = t0 + timeout
    while time.perf_counter() < deadline:
        wpid, wstatus = os.waitpid(pid, os.WNOHANG)
        if wpid == pid:
            dt = time.perf_counter() - t0
            if wstatus != 0:
                return False, f"exit={wstatus} msg={msg!r}", dt
            return msg == b"OK", msg.decode(errors="replace"), dt
        time.sleep(0.0005)
    os.kill(pid, signal.SIGKILL)
    os.waitpid(pid, 0)
    return False, f"REAP-TIMEOUT msg={msg!r}", time.perf_counter() - t0


def _selftest_deadlock_detector():
    """Prove the deadlock guard actually fires: fork a child that hangs while
    still holding the write end open (as a real deadlocked child would — it
    never reaches its os.write/os._exit). The bounded select must time out
    (not-ready), not block forever. NOTE: the child must NOT close `w`; closing
    it would send EOF and make select return readable, which is not a hang."""
    r, w = os.pipe()
    t0 = time.perf_counter()
    pid = os.fork()
    if pid == 0:
        os.close(r)
        # keep `w` OPEN and hang -> no data, no EOF, faithfully simulates a
        # child deadlocked before it could signal.
        while True:
            time.sleep(1)
    os.close(w)  # parent drops its write end; child still holds one open
    ready, _, _ = select.select([r], [], [], 1.0)
    os.close(r)
    detected = not ready
    os.kill(pid, signal.SIGKILL)
    os.waitpid(pid, 0)
    print(f"[selftest] deadlock guard fired within {time.perf_counter()-t0:.2f}s: "
          f"{'YES' if detected else 'NO'}")
    return detected


def main():
    print(f"platform={sys.platform} python={sys.version.split()[0]}")
    print(f"env OMP_NUM_THREADS={os.environ.get('OMP_NUM_THREADS')} "
          f"OMP_DYNAMIC={os.environ.get('OMP_DYNAMIC')}")
    thread_census("pre-import")

    t0 = time.perf_counter()
    import cadquery  # noqa: F401
    import OCP  # noqa: F401
    import_secs = time.perf_counter() - t0
    print(f"[import] cadquery+OCP took {import_secs:.2f}s")
    py_threads, os_tasks = thread_census("post-import")

    fork_safe = py_threads == 1 and (os_tasks is None or os_tasks == 1)
    print(f"\n>>> FORK-SAFETY GATE: parent single-threaded? "
          f"{'YES' if fork_safe else 'NO'} "
          f"(py_threads={py_threads}, os_tasks={os_tasks})")
    if os_tasks is None:
        print("    WARNING: /proc unavailable (macOS) — native-thread count NOT "
              "verified; run on Linux for the authoritative gate.")

    # Prove the guard works before trusting 'fails=0' below.
    _selftest_deadlock_detector()

    scratch = "/tmp/fork_spike_out"
    os.makedirs(scratch, exist_ok=True)
    ok, detail, _ = fork_once(BOX_SRC, f"{scratch}/warm.stl")
    print(f"[warmup fork] ok={ok} detail={detail}")
    if not ok:
        print("Warmup fork failed — aborting timing batch.")
        return

    N = 50
    times, fails = [], 0
    for i in range(N):
        ok, detail, dt = fork_once(BOX_SRC, f"{scratch}/box_{i}.stl")
        times.append(dt)
        if not ok:
            fails += 1
            print(f"  fork {i} FAILED: {detail}")
    times.sort()
    p50 = statistics.median(times)
    p95 = times[int(len(times) * 0.95) - 1]
    print(f"\n[fork batch] N={N} fails={fails}")
    print(f"  per-request fork+box+STL: p50={p50*1000:.1f}ms p95={p95*1000:.1f}ms "
          f"min={times[0]*1000:.1f}ms max={times[-1]*1000:.1f}ms")
    print(f"  vs cold import baseline: {import_secs:.2f}s => ~{import_secs/p50:.0f}x on p50")


if __name__ == "__main__":
    main()
