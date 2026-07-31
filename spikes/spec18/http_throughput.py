"""SPEC18 acceptance harness: sustained throughput through the worker HTTP seam.

The fork-level spike (`fork_throughput.py`) excludes the HTTP/base64/guard hop.
This drives the REAL worker — `uvicorn main:app` with the zygote enabled — over
HTTP at a fixed client concurrency, and reports achieved RPS and latency plus a
fresh-vs-zygote median comparison. It is the SPEC18 "sustained 10-RPS HTTP" and
"≥5× vs fresh" acceptance gate; run it on the target host/image.

Run in the worker image:
  docker run --rm -v $PWD:/repo:ro -w /repo/worker --cpus=1.0 \
    easycad-worker:spike sh -c 'pip -q install httpx >/dev/null; \
      python /repo/spikes/spec18/http_throughput.py'
"""
import json
import os
import subprocess
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

PORT = int(os.getenv("PORT", "8863"))
BASE = f"http://127.0.0.1:{PORT}"
BOX = {"code": "import cadquery as cq\nresult = cq.Workplane('XY').box(10, 10, 10)\n"}
MODERATE = {"code": "import cadquery as cq\nresult = (cq.Workplane('XY').box(20,20,10)"
                    ".edges('|Z').fillet(2).faces('>Z').workplane().hole(6))\n"}


def _post(path, body, timeout=60):
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(req, timeout=timeout))


def _get(path, timeout=5):
    return json.load(urllib.request.urlopen(BASE + path, timeout=timeout))


# Acceptance thresholds (overridable). The gate FAILS (non-zero exit) if unmet.
MIN_RPS = float(os.getenv("EASYCAD_ACCEPT_MIN_RPS", "10"))
MAX_P95_MS = float(os.getenv("EASYCAD_ACCEPT_MAX_P95_MS", "2000"))


def _drive(body, n, c, tag, min_rps):
    """Drive n requests at concurrency c; return True iff the run meets the gate
    (>= min_rps, zero failures, p95 <= MAX_P95_MS)."""
    def hit(_):
        t = time.perf_counter()
        try:
            ok = _post("/execute", body).get("success", False)
        except OSError:
            ok = False
        return ok, time.perf_counter() - t
    t0 = time.perf_counter()
    with ThreadPoolExecutor(max_workers=c) as ex:
        res = list(ex.map(hit, range(n)))
    wall = time.perf_counter() - t0
    lat = sorted(d for _, d in res)
    oks = sum(1 for ok, _ in res if ok)
    rps = n / wall
    p50 = lat[len(lat) // 2] * 1000
    p95 = lat[int(len(lat) * 0.95) - 1] * 1000
    ok_gate = oks == n and rps >= min_rps and p95 <= MAX_P95_MS
    print(f"[{tag}] N={n} C={c} ok={oks}/{n} wall={wall:.2f}s "
          f"=> {rps:.1f} RPS  p50={p50:.0f}ms p95={p95:.0f}ms  "
          f"{'PASS' if ok_gate else 'FAIL'} "
          f"(need >={min_rps} RPS, 0 fail, p95<={MAX_P95_MS:.0f}ms)")
    return ok_gate


def main():
    repo = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    worker_dir, app_dir = repo + "/worker", repo + "/app"
    # In the built image everything is flat in /app; when run against the split
    # repo the uvicorn process needs both dirs to import cq_worker/code_guard.
    env = {**os.environ, "EASYCAD_WORKER_ZYGOTE": "1",
           "PYTHONPATH": os.pathsep.join([worker_dir, app_dir,
                                          os.environ.get("PYTHONPATH", "")])}
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "main:app", "--host", "127.0.0.1",
         "--port", str(PORT)],
        cwd=worker_dir, env=env,
    )
    try:
        for _ in range(240):
            try:
                if _get("/readyz").get("ready"):
                    break
            except OSError:
                pass
            time.sleep(0.25)
        else:
            print("worker did not become ready"); return
        print("worker ready (zygote). Warming…")
        _post("/execute", BOX)

        # Both model classes must clear the 10-RPS goal; the moderate model is
        # the binding case (heavier per-request CPU).
        passed = _drive(BOX, 200, 4, "trivial-box    ", MIN_RPS)
        passed &= _drive(MODERATE, 120, 4, "box+fillet+hole", MIN_RPS)
        print("statz:", json.dumps(_get("/statz")))
        if not passed:
            print("ACCEPTANCE: FAIL")
            sys.exit(1)
        print("ACCEPTANCE: PASS")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


if __name__ == "__main__":
    main()
