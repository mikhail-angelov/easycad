# SPEC18: Zygote-Fork CadQuery Execution

## Status

**Implemented behind a disabled flag** (`EASYCAD_WORKER_ZYGOTE`, off by default;
`docker-compose-prod.yml` keeps it commented out). The worker code, tests, and
metrics are in place and validated on the Linux worker image; what remains before
it becomes the hosted default is the HTTP performance acceptance — the measured
≥5× fresh-vs-zygote comparison and the sustained 10-RPS HTTP run on the target
host (see Testing Decisions).

Supersedes SPEC17 (the prewarmed one-shot runner pool) for the throughput goal.
Extends the hosted execution worker from SPEC12 without changing the public API,
CadQuery program format, or the per-request isolation guarantee. SPEC17 cannot
reach sustained multi-RPS throughput (see Problem Statement) and is marked
superseded.

Feasibility was validated by a spike on the real Linux worker image
(`easycad-worker`, `--memory=1g --cpus=1.0 --pids-limit=128`); results are in
`review.md` and summarised under Evidence below.

## Problem Statement

EasyCAD imports CadQuery/OCP on every execution. On the Linux worker that import
costs ~1.5 s and ~440 MB RSS; the actual model construction and STL/STEP export
are single-digit-to-tens of milliseconds. Every request — execute *and* export —
pays the import.

This makes throughput import-bound, not work-bound. To sustain 10 RPS with a
one-shot-process model (including SPEC17's prewarmed pool) the worker must finish
one ~1.5 s import every 0.1 s: ~15–24 runners importing in parallel continuously
(~15–24 CPU-seconds of import per wall-second) and 15–24 × 440 MB ≈ 7–10 GB
resident. On `cpus: "1.0"` / `mem_limit: 1g` this is impossible; a prewarmed pool
only improves p50 for isolated requests and drains instantly under load.

The import is pure, tenant-independent state. The goal is to pay it **once** and
reuse it across requests **without** reusing any executed-code state — preserving
the SPEC12 rule that no Python or CAD state crosses a request boundary.

## Solution

The hosted worker can optionally run in **zygote mode**. A single trusted
**zygote** process imports CadQuery/OCP once at startup and *never executes
generated code*. For each request it `fork()`s a **one-shot child**. The child
inherits the imported interpreter through copy-on-write, applies the existing
per-request resource limits, creates the same temporary scratch directory,
executes the guarded generated code, writes the wire result, and exits. It is
never reused.

Because the expensive import is inherited via CoW, a fork costs milliseconds
instead of ~1.5 s, and the ~440 MB import is *shared* across all concurrent
children rather than duplicated per runner. Isolation is unchanged from SPEC12:
each request still runs in a fresh, private process address space that dies after
one job. The only thing shared between requests is read-only imported OCP
code/data — which contains no key, no user data, and no prior request's state.

The `/execute` and `/export` endpoints, response schema, error semantics, Docker
isolation, and concurrency limit remain unchanged. Local execution stays
fresh-process and unchanged.

## Why not the alternatives

- **Fresh process per request (today):** correct and isolated, but import-bound;
  cannot reach the throughput target on the given CPU/memory budget.
- **SPEC17 prewarmed one-shot pool:** improves isolated p50 only; the pool refills
  no faster than the import rate, so sustained throughput is unchanged, and N
  separately imported runners duplicate the anonymous import heap. The spike
  measured 8 independent importer processes at ≈ 1.48 GB PSS vs the zygote's
  ≈ 0.55 GB (parent + 8 children) for the same concurrency — 63% less.
- **Persistent worker reused across requests with a state reset:** fastest to
  build, but it reuses *executed-code* state — Python globals, native OCP state,
  memory fragmentation, and any implant survive between tenants. This breaks the
  SPEC12 isolation guarantee and is explicitly rejected.
- **In-process `exec` / subinterpreters:** share one address space; an OCP C-level
  crash or memory corruption from one request takes down all others and offers no
  cross-request isolation. Rejected.

## User Stories

1. As a CAD builder, I want a simple successful generation to start promptly, so that the application does not feel stalled before geometry is created.
2. As a CAD builder, I want a simple STL or STEP export to avoid runtime startup delay, so that downloads feel responsive — the same zygote serves execute and export.
3. As a CAD builder, I want complex models to retain the current correctness and error messages, so that faster startup does not change modelling outcomes.
4. As a CAD builder, I want a request that times out or crashes to fail clearly, so that I can correct the model rather than receive a stale result.
5. As a tenant, I want my generated code to run in a process address space no prior tenant used, so that no Python or CAD *executed-code* state can cross request boundaries.
6. As a tenant, I want a failed or malicious request not to reduce the safety of later requests, so that the service remains trustworthy under abuse.
7. As an operator, I want the worker to keep its existing bounded concurrency, so that forking cannot turn a traffic spike into unbounded CPU or memory use.
8. As an operator, I want a worker to become ready only after the zygote has imported successfully, so that deployment readiness represents useful capacity.
9. As an operator, I want a crashed or timed-out child to be reaped and to not affect the zygote or concurrent children, so that transient failures do not lower capacity.
10. As an operator, I want to observe import time, per-request fork+exec time, request wait time, and resident memory, so that I can distinguish CPU saturation from slow CAD geometry.
11. As an operator, I want to disable zygote mode with configuration, so that rollout can be reversed without changing application clients.
12. As an operator, I want the existing memory, CPU, PID, filesystem, and network controls to apply to forked children, so that latency work does not weaken the threat model.
13. As a developer, I want the app-to-worker execution contract to remain stable, so that chat, variations, lazy STL restoration, and exports need no endpoint or frontend migration.
14. As a developer, I want local development to retain a simple fresh-process mode, so that the hosted optimization does not complicate the default workflow.

## Implementation Decisions

- The optimization applies only to the isolated hosted worker. The local executor
  (`app/cadquery_exec.py:LocalExecutor`) keeps spawning a fresh process per
  request, unchanged.
- Zygote mode is opt-in through one worker configuration flag
  (`EASYCAD_WORKER_ZYGOTE`), disabled by default until production-like latency and
  memory measurements pass. The pool/concurrency knob is the existing
  `EASYCAD_WORKER_CONCURRENCY`; no second capacity setting is introduced.
- A single zygote process imports CadQuery/OCP once and never executes generated
  code. It **must stay single-threaded**: the worker sets `OMP_NUM_THREADS=1`
  (and the other BLAS/TBB single-thread vars) before import so OCP starts no
  background native threads — the precondition for fork safety. Native threading
  for real CAD work is allowed only *inside* the forked child, after fork.
- Each request forks exactly one child. The child: closes inherited fds it should
  not hold, opens a fresh scratch `TemporaryDirectory`, applies the existing
  per-request `setrlimit` (CPU, FSIZE, NPROC, and AS if enabled) **immediately
  before executing generated code**, execs the guarded code, writes the wire
  payload, removes its scratch dir, and `os._exit()`s. It is never reused.
- Because limits are applied to an already-imported process, they are sized for
  the post-import baseline: `RLIMIT_CPU` now budgets user-code CPU only (not
  import), and `RLIMIT_AS` — if ever enabled — must include the ~440 MB import
  footprint or it kills the child instantly. `RLIMIT_AS` stays off by default, as
  today; the container cgroup remains the real memory cap.
- Generated code continues through the existing AST guard (`code_guard.check`) in
  the zygote before a child is forked. The container boundary remains
  authoritative: non-root user, read-only root filesystem, tmpfs scratch, no
  network egress, cgroup limits, no secrets.
- The zygote's control socket is authenticated: it accepts jobs only from its
  launching FastAPI parent (verified per-connection via `SO_PEERCRED`), so a
  guard-escaping child cannot use it to fork work outside the guard/concurrency
  path. The supervisor re-runs the AST guard before every fork (defence in depth),
  bounds untrusted inbound frames to `EASYCAD_WORKER_MAX_BODY_BYTES`, rejects
  unknown operations rather than executing them, and contains `os.fork()` failure
  (PID/memory exhaustion) as a per-request error without unwinding the loop.
- On shutdown (SIGTERM/SIGINT) the zygote kills and reaps every in-flight child
  before exiting, so a worker reload cannot orphan a running untrusted CAD
  process. A child's result frame is bounded by `EASYCAD_WORKER_MAX_RESULT_BYTES`,
  sized against the container memory budget (default 64 MB: worst case ≈
  2×cap×concurrency on top of the ~440 MB import, comfortably inside a 1 GB
  container) — an over-limit result kills the child and returns a bounded error
  instead of OOMing the supervisor. Operators with larger models must raise this
  and `mem_limit` together.
- The zygote enforces the wall-clock timeout on each child (`waitpid` with a
  deadline); on timeout it `SIGKILL`s the child and returns the existing timeout
  error. A crashed or killed child returns the same result shape and error
  category as today and is not retried automatically. The zygote always reaps to
  avoid zombies.
- A request forks a child only under the existing concurrency admission control
  (`EASYCAD_WORKER_CONCURRENCY` semaphore). Excess requests wait as they do today;
  no durable queue or new job API is introduced.
- Readiness distinguishes liveness from usable capacity: the worker reports ready
  only after the zygote import has completed. This is exposed on a **separate
  readiness signal**, leaving `/healthz` liveness semantics unchanged so Compose
  `depends_on`/healthcheck ordering is not altered.
- Zygote mode requires a **single zygote process per container**. Running uvicorn
  with `--workers N` would create N independent zygotes (N × import memory) and
  break the "pool size == concurrency" invariant. This is enforced, not just
  documented: at startup the worker takes an exclusive `flock`
  (`EASYCAD_WORKER_ZYGOTE_LOCK`), so a second FastAPI process fails fast with a
  clear error instead of silently starting a second zygote.
- The worker records import duration, per-request fork+exec duration, request wait
  duration, child failure counts, and resident memory. Neither generated code nor
  user-facing responses receive child identifiers.
- The zygote path must preserve the exact execution result contract: `success`,
  STL/STEP payload, `geometry_info`, and user-code failure behaviour are
  unchanged. `code_with_geometry` composition stays app-side and is unaffected.

## Testing Decisions

- Test at the worker HTTP seam: a successful simple model, user-code error, guard
  rejection, crash, timeout, and export retain the existing response contract in
  both fresh and zygote modes.
- Test isolation as externally observable behaviour: a first request that creates
  process-local Python/CAD state cannot make that state visible to a second
  request. This proves the one-shot child lifecycle, not a private assertion.
- Test concurrency correctness: N concurrent requests forked from one zygote
  return independent, uncorrupted geometry (the spike showed 8/8 byte-identical
  outputs — encode this as an automated check).
- Test recovery: after a child crash or timeout the zygote stays healthy and a
  later valid request succeeds; a child failure never kills the zygote or a
  sibling.
- Test that requests beyond configured concurrency do not fork extra children and
  that container/resource-limit failures remain contained.
- Fork-safety regression: assert the zygote has exactly one OS thread after import
  (`/proc/self/task`), so a future OCP/dependency bump that starts a background
  thread at import fails the test loudly rather than deadlocking in production.
- Performance test with the trivial box baseline on the worker image: zygote mode
  must reduce median isolated execution latency by at least 5× vs fresh worker
  mode, returning byte-equivalent geometry and a valid STL. Record p50, p95, fork
  time, import time, per-child Pss, and peak container memory.
- Sustained throughput acceptance test **through the worker HTTP seam** (not just
  fork+exec), confirming ≥10 RPS at bounded latency and that throughput is
  exec-bound, not import-bound. Harness: `spikes/spec18/http_throughput.py` drives
  real `uvicorn main:app` (zygote on) over HTTP. Measured on the worker image at
  `--cpus=1.0`: **53.7 RPS** (trivial box, p50 85 ms) and **22.7 RPS**
  (box+fillet+hole, p50 186 ms), 0 failures across 320 requests served from a
  single CadQuery import (`/statz` `import_seconds≈0.9`, `crashes_total=0`). Both
  clear the 10-RPS goal; latency — not RPS — rises with concurrency (the
  exec-bound signature). The automated ≥5× fresh-vs-zygote gate lives in
  `tests/test_spec18_zygote.py::test_zygote_at_least_5x_faster_than_fresh`.
- Run the existing application and worker integration tests (chat, variations,
  lazy STL restoration, STEP export) unchanged, because they share the contract.

## Out of Scope

- Reusing a child after it executes generated code, or resetting-and-reusing a
  persistent interpreter.
- Changing generated CadQuery syntax, replacing CadQuery/OCP, or a mesh-only kernel.
- Async job IDs, polling, SSE progress, durable queues, Redis, BullMQ, cross-node.
- Changing chat/repair policy, LLM concurrency, quotas, or rate limits.
- Changing local development execution behaviour.
- Persisting CAD sessions or execution artifacts.
- Multi-process uvicorn in the worker (single zygote per container is required).

## Evidence (spike, Linux worker image)

Validated on `easycad-worker`, `--memory=1g --cpus=1.0 --pids-limit=128`. Full
run in `review.md`.

- **Fork-safety:** after `import cadquery, OCP` with `OMP_NUM_THREADS=1`, the
  zygote has exactly **1 native OS thread** (`/proc/self/task` = 1). OCP starts no
  background threads at import → fork is safe. The spike's deadlock guard is
  itself proven by a selftest (a child that hangs holding the pipe open is
  detected within the timeout), so the `50/50 clean` result is trustworthy.
- **Latency:** fork + box + STL export **p50 ≈ 12.9 ms, p95 ≈ 15 ms**, 50/50
  successful, vs **~0.9–1.5 s** cold import (~70–120× on p50; import time varies
  run-to-run).
- **Concurrency correctness:** 8 children forked from one zygote ran
  concurrently, **8/8 exited clean, all STL outputs byte-identical**
  (single sha256, `distinct=1`) **and equal to an independent reference** run —
  no cross-child corruption. (Verified on STL, which is deterministic; STEP was
  avoided because its header carries a timestamp.)
- **Memory (CoW sharing), measured rigorously:** parent PSS and all 8 children
  sampled *simultaneously* while parked (a dedicated ready-pipe blocks until all
  8/8 signal, so the sample is not a sleep-timed guess). Zygote total (parent + 8)
  ≈ **555 MB** (parent PSS drops to ~273 MB once children share its pages). A real
  pool of 8 *independent* importer processes measured ≈ **1480 MB** PSS for the
  same concurrency — so the zygote uses **~63% less** and fits `mem_limit: 1g`
  where an 8-way pool needs ~1.5 g. (The pool cost is measured, not `N × RSS`:
  independent processes still share read-only `.so` pages via the page cache; the
  zygote's extra saving is the shared *anonymous* import heap that CoW provides and
  independent imports cannot.)
- **Throughput (preliminary, fork-only, single container):** driving one zygote at
  a fixed in-flight concurrency, a single core (`--cpus=1.0`) sustained **~56 RPS**
  (trivial box) and **~21 RPS** (box+fillet+hole), 0 failures. Raising concurrency
  raised latency, not RPS — confirming exec-bound behaviour. This brackets the goal
  (10 RPS needs < 1 core for these models) but excludes the HTTP/base64/guard hop
  and heavy models; those belong to the acceptance test above.

## Further Notes

- The gain removes import/startup overhead. Boolean operations, fillets,
  tessellation, and large STL/STEP serialization remain real CAD costs; size cores
  by measured exec p95, not by import.
- Single security decision: the zygote never executes generated code, and every
  request is a fresh CoW child that exits after one job. Reuse of *executed-code*
  state is never introduced.
- The one measured precondition is a single-threaded zygote at fork time. Keep the
  single-thread env vars and the fork-safety regression test; re-measure on any
  CadQuery/OCP upgrade.
- Roll out behind the flag: establish fresh-mode baseline on the target host,
  enable zygote with the smallest concurrency, compare latency, memory, and error
  rate, then make it the hosted default.
