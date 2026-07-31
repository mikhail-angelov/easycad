"""SPEC18 spike part 3: sustained throughput under a concurrency cap.

Parts 1-2 proved fork-safety, correctness and memory. They do NOT prove the
10-RPS goal — that is a throughput question. This driver answers it directly:
one zygote (single import), a fixed number C of in-flight forked children at all
times, N requests total, measure achieved RPS = N / wall and per-request latency.

This is a *preliminary, single-container* throughput measurement (fork+exec+STL,
no HTTP/network hop). It is NOT the full SPEC18 acceptance test, which must run
through the worker HTTP seam with a realistic model-size distribution. Its job is
to show the worker is exec-bound (not import-bound) and to bracket the core count
needed for 10 RPS.

Run (Linux): docker run --rm -i --cpus=1.0 easycad-worker:spike python - < fork_throughput.py
Vary cores with --cpus to see the scaling.
"""
import os
import sys
import time
import statistics
import resource

for _k in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
           "TBB_NUM_THREADS", "VECLIB_MAXIMUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ[_k] = "1"
os.environ["OMP_DYNAMIC"] = "FALSE"

import cadquery as cq  # noqa: E402
import OCP  # noqa: E402,F401

TRIVIAL = 'import cadquery as cq\nresult = cq.Workplane("XY").box(10, 10, 10)\n'
MODERATE = (
    "import cadquery as cq\n"
    "result = (cq.Workplane('XY').box(20, 20, 10)"
    ".edges('|Z').fillet(2).faces('>Z').workplane().hole(6))\n"
)


def run_and_exit(src, out_path):
    """Child body: per-request limits, exec, export STL, exit by status."""
    try:
        try:
            resource.setrlimit(resource.RLIMIT_CPU, (30, 30))
        except (ValueError, OSError):
            pass
        ns = {}
        exec(compile(src, "<user>", "exec"), ns)
        cq.exporters.export(ns["result"], out_path)
    except BaseException:  # noqa: BLE001
        os._exit(1)
    os._exit(0)


def throughput(src, n, c, tag):
    """Keep exactly c children in flight until n have completed. Returns
    (rps, latencies, fails)."""
    running = {}   # pid -> launch_time
    latencies = []
    fails = 0
    launched = 0

    def launch(i):
        pid = os.fork()
        if pid == 0:
            run_and_exit(src, f"/tmp/tp_{i}.stl")
            os._exit(0)  # unreachable
        running[pid] = time.perf_counter()

    t0 = time.perf_counter()
    while launched < min(c, n):
        launch(launched)
        launched += 1
    done = 0
    while done < n:
        pid, status = os.waitpid(-1, 0)
        latencies.append(time.perf_counter() - running.pop(pid))
        if status != 0:
            fails += 1
        done += 1
        if launched < n:
            launch(launched)
            launched += 1
    wall = time.perf_counter() - t0
    rps = n / wall
    latencies.sort()
    p50 = statistics.median(latencies) * 1000
    p95 = latencies[int(len(latencies) * 0.95) - 1] * 1000
    print(f"[{tag}] N={n} C={c} fails={fails}  wall={wall:.2f}s  "
          f"=> {rps:.1f} RPS   lat p50={p50:.0f}ms p95={p95:.0f}ms")
    return rps, latencies, fails


def main():
    cpus = os.cpu_count()
    print(f"zygote pid={os.getpid()} cpu_count(visible)={cpus}\n")
    # Warm any lazy first-use init once so timings reflect steady state.
    throughput(MODERATE, 4, 2, "warmup")
    print()
    for c in (2, 4, 8):
        throughput(TRIVIAL, 200, c, f"trivial-box  C={c}")
    print()
    for c in (2, 4, 8):
        throughput(MODERATE, 120, c, f"box+fillet+hole C={c}")
    print("\nNote: --cpus limits real parallelism; RPS above ~1 core's worth of "
          "CPU is bounded by the cgroup, not by fork. Scale cores for 10 RPS of "
          "the observed per-request CPU cost.")


if __name__ == "__main__":
    main()
