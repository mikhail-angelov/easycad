"""SPEC18 spike part 2: concurrent forks, byte-identity, CoW memory sharing.

Validates the two remaining claims for zygote-fork vs the SPEC17 prewarm pool:
  1. N children forked from one imported parent run concurrently and CORRECTLY —
     each produces a byte-identical model (sha256 of a deterministic STL), so no
     child corrupts another's state.
  2. The ~440MB OCP import is SHARED across children via copy-on-write, so N
     concurrent children cost far less than N independently imported runners.

Memory is measured rigorously:
  * We sample Pss (proportional set size) of the parent AND every child at the
    SAME moment (all children parked, alive) — Pss divides shared pages among
    all current sharers, so summing gives the true resident-unique total.
  * For the pool comparison we don't estimate N*RSS. We actually spawn N
    INDEPENDENT importer processes (fresh `import OCP` each), park them, and sum
    their Pss the same way. Both scenarios then account for file-backed .so
    sharing; the zygote's extra win is the shared *anonymous* import heap (CoW),
    which independent processes cannot share.

Run (Linux): docker run --rm -i easycad-worker:spike python - < fork_concurrent.py
"""
import os
import sys
import time
import select
import hashlib
import subprocess

for _k in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
           "TBB_NUM_THREADS", "VECLIB_MAXIMUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ[_k] = "1"
os.environ["OMP_DYNAMIC"] = "FALSE"

import cadquery as cq  # noqa: E402
import OCP  # noqa: E402,F401

K = 8

# STL (deterministic, no timestamp header) so byte-identity is a real check;
# a slightly heavy model forces boolean + fillet + hole work, not a bare box.
SRC = """
import cadquery as cq
result = (cq.Workplane("XY").box(20, 20, 10)
          .edges("|Z").fillet(2)
          .faces(">Z").workplane().hole(6))
"""


def smaps_pss_rss(pid):
    """Return (Rss_MB, Pss_MB) from /proc/<pid>/smaps_rollup, or (None, None)."""
    rss = pss = 0
    try:
        with open(f"/proc/{pid}/smaps_rollup") as f:
            for line in f:
                if line.startswith("Rss:"):
                    rss = int(line.split()[1])
                elif line.startswith("Pss:"):
                    pss = int(line.split()[1])
    except OSError:
        return None, None
    return rss / 1024, pss / 1024


def reference_stl_hash():
    """Compute the expected STL hash in a FRESH isolated interpreter, so the
    concurrent children are compared against an independent ground truth."""
    prog = (
        "import os\n"
        + "".join(f"os.environ['{k}']='1'\n" for k in ("OMP_NUM_THREADS",))
        + "import cadquery as cq\n"
        + "r=(cq.Workplane('XY').box(20,20,10).edges('|Z').fillet(2)"
          ".faces('>Z').workplane().hole(6))\n"
        "cq.exporters.export(r,'/tmp/ref.stl')\n"
    )
    subprocess.run([sys.executable, "-c", prog], check=True)
    return hashlib.sha256(open("/tmp/ref.stl", "rb").read()).hexdigest()


def child(idx, out_path, ready_w, release_r):
    try:
        ns = {}
        exec(compile(SRC, "<user>", "exec"), ns)
        cq.exporters.export(ns["result"], out_path)
        os.write(ready_w, b"x")       # signal ready on the dedicated ready-pipe
        os.read(release_r, 1)         # park so parent can sample smaps while alive
    except BaseException as e:  # noqa: BLE001
        os.write(2, f"child {idx} ERR {e!r}\n".encode())
        os._exit(1)
    os._exit(0)


def wait_for_ready(ready_r, k, timeout=60.0):
    """Bounded wait until exactly k children have signalled ready. Returns the
    count actually received (== k on success); never blocks past `timeout`."""
    got = 0
    deadline = time.perf_counter() + timeout
    while got < k:
        remaining = deadline - time.perf_counter()
        if remaining <= 0:
            break
        r, _, _ = select.select([ready_r], [], [], remaining)
        if not r:
            break
        got += len(os.read(ready_r, k - got))
    return got


def measure_pool(k):
    """Spawn k INDEPENDENT importer processes, park them, sum their Pss."""
    prog = (
        "import os,sys\n"
        + "".join(f"os.environ['{v}']='1'\n" for v in ("OMP_NUM_THREADS",))
        + "import cadquery,OCP\n"
        "sys.stdout.write('ready\\n');sys.stdout.flush()\n"
        "sys.stdin.read(1)\n"
    )
    procs = [subprocess.Popen([sys.executable, "-c", prog],
                              stdin=subprocess.PIPE, stdout=subprocess.PIPE)
             for _ in range(k)]
    for p in procs:  # wait until each finished importing
        p.stdout.readline()
    total = 0.0
    live = 0
    for p in procs:
        _, pss = smaps_pss_rss(p.pid)
        if pss is not None:
            total += pss
            live += 1
    for p in procs:  # release + reap
        try:
            p.stdin.write(b"x")
            p.stdin.close()
        except OSError:
            pass
        p.wait()
    return total, live


def main():
    print(f"parent pid={os.getpid()} K={K}")
    ref_hash = reference_stl_hash()
    print(f"reference STL sha256={ref_hash[:16]}…")

    ready_r, ready_w = os.pipe()
    release_r, release_w = os.pipe()
    pids = []
    for i in range(K):
        pid = os.fork()
        if pid == 0:
            os.close(ready_r)      # child only writes ready, reads release
            os.close(release_w)
            child(i, f"/tmp/cc_{i}.stl", ready_w, release_r)
            os._exit(0)
        pids.append(pid)
    os.close(ready_w)              # parent only reads ready, writes release
    os.close(release_r)

    # Bounded wait until ALL K children signalled ready — no sleep guess.
    got = wait_for_ready(ready_r, K)
    os.close(ready_r)
    if got != K:
        print(f"ABORT: only {got}/{K} children became ready within timeout")
        return
    print(f"all {K}/{K} children ready; sampling parent + children PSS together")

    # Sample smaps of parent AND every child at the same moment.
    samples = {"parent": smaps_pss_rss("self")}
    for pid in pids:
        samples[pid] = smaps_pss_rss(pid)
    live = [pid for pid in pids if samples[pid][0] is not None]
    zygote_pss = sum(samples[pid][1] for pid in live) + samples["parent"][1]
    zygote_rss = sum(samples[pid][0] for pid in live) + samples["parent"][0]
    print(f"\n[zygote, {len(live)+1} procs live simultaneously]")
    print(f"  parent: Rss={samples['parent'][0]:.0f} Pss={samples['parent'][1]:.0f}MB")
    print(f"  sum Rss (parent+children) = {zygote_rss:.0f}MB")
    print(f"  sum Pss (parent+children) = {zygote_pss:.0f}MB   <-- true resident cost")

    # Release children, reap, verify byte-identity.
    for _ in pids:
        try:
            os.write(release_w, b"x")
        except OSError:
            pass
    os.close(release_w)
    clean = 0
    for pid in pids:
        _, st = os.waitpid(pid, 0)
        if st == 0:
            clean += 1
    hashes = {}
    for i in range(K):
        p = f"/tmp/cc_{i}.stl"
        if os.path.exists(p):
            hashes[i] = hashlib.sha256(open(p, "rb").read()).hexdigest()
    distinct = set(hashes.values())
    all_match_ref = distinct == {ref_hash}
    print(f"\n[correctness] {clean}/{K} children exited clean; "
          f"{len(hashes)} STL files; distinct sha256={len(distinct)}")
    print(f"  byte-identical AND equal to independent reference? "
          f"{'YES' if all_match_ref else 'NO'}  ({sorted(h[:12] for h in distinct)})")

    # Rigorous pool comparison: measure real independent importers.
    print(f"\n[measuring pool of {K} independent importers …]")
    pool_pss, pool_live = measure_pool(K)
    print(f"  pool sum Pss ({pool_live} independent procs) = {pool_pss:.0f}MB")
    print(f"\n[verdict] zygote {zygote_pss:.0f}MB vs pool {pool_pss:.0f}MB for "
          f"{K}-way concurrency => zygote saves ~{pool_pss - zygote_pss:.0f}MB "
          f"({(1 - zygote_pss/max(pool_pss,1))*100:.0f}% less).")


if __name__ == "__main__":
    main()
