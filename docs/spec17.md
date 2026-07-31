# SPEC17: Prewarmed One-Shot CadQuery Runner Pool

## Status

**Superseded by SPEC18 (`docs/spec18.md`).** SPEC18 is implemented behind a
disabled flag (`EASYCAD_WORKER_ZYGOTE`, off by default) and awaits HTTP
performance acceptance before becoming the hosted default. The prewarmed one-shot
pool improves only isolated p50 latency; it cannot reach sustained throughput (N
separately imported runners cost N× memory and refill no faster than the import
rate). SPEC18's zygote-fork model — import CadQuery once, fork a one-shot CoW
child per request — delivers the same isolation with far less memory and no
per-request import. This document is retained for historical context and its
review findings, which SPEC18 carries forward.

---

Proposed implementation specification. Extends the hosted execution worker from
SPEC12. It reduces execution-start latency without changing the public API,
CadQuery program format, or the security boundary for untrusted code.

## Problem Statement

EasyCAD starts a new Python process for every CadQuery execution. This is the
right isolation boundary, but it makes even a trivial model pay the cost of
starting Python and importing CadQuery/OCP. A local measurement of a 10 mm box
recorded a 2.35 s median for the current isolated path after its first run;
the model construction and STL export in an already imported process were
approximately 0.9 ms and 0.6 ms respectively.

Users experience this startup cost as slow generation and export even when the
generated geometry itself is simple. Reusing a process *after* it has executed
untrusted Python is not acceptable: Python module globals, native-library
state, resource exhaustion, and a compromised interpreter could leak between
users or requests.

## Solution

The hosted worker can optionally maintain a bounded pool of **prewarmed,
one-shot CadQuery runners**. A runner imports CadQuery/OCP before it accepts
work. It receives exactly one guarded execution request, applies the existing
resource limits, creates the same temporary scratch directory and wire result,
then exits whether the request succeeds, fails, or times out.

A trusted supervisor owns the pool and replaces a consumed or failed runner in
the background. The next request is assigned an already-ready runner. The
execution endpoint, response schema, error semantics, Docker isolation, and
concurrency limit remain unchanged. Local execution remains fresh-process and
unchanged.

## User Stories

1. As a CAD builder, I want a simple successful generation to start promptly, so that the application does not feel stalled before geometry is created.
2. As a CAD builder, I want a simple STEP or STL export to avoid unnecessary runtime startup delay, so that downloads feel responsive.
3. As a CAD builder, I want complex models to retain the current correctness and error messages, so that faster startup does not change modelling outcomes.
4. As a CAD builder, I want a request that times out or crashes to fail clearly, so that I can correct the model rather than receive a stale result.
5. As a tenant, I want my generated code to run in a process that no prior tenant used, so that no Python or CAD state can cross request boundaries.
6. As a tenant, I want a failed or malicious request not to reduce the safety of later requests, so that the service remains trustworthy under abuse.
7. As an operator, I want the worker to keep its existing bounded concurrency, so that prewarming cannot turn traffic spikes into unbounded CPU or memory use.
8. As an operator, I want a worker to become ready only after its configured runners have imported successfully, so that deployment readiness represents useful capacity.
9. As an operator, I want a consumed, crashed, or timed-out runner to be replenished automatically, so that transient failures do not permanently lower capacity.
10. As an operator, I want to observe ready capacity, runner warmup failures, request wait time, and execution time, so that I can distinguish pool starvation from slow CAD geometry.
11. As an operator, I want to disable prewarming with configuration, so that rollout can be reversed without changing application clients.
12. As an operator, I want the existing memory, CPU, PID, filesystem, and network controls to apply to warmed runners, so that latency work does not weaken the threat model.
13. As a developer, I want the app-to-worker execution contract to remain stable, so that chat, variations, lazy STL restoration, and exports need no endpoint or frontend migration.
14. As a developer, I want local development to retain a simple fresh-process mode, so that the hosted optimization does not complicate the default workflow.

## Implementation Decisions

- The optimization applies only to the isolated hosted worker. The local executor keeps spawning a fresh process for every request.
- Prewarming is opt-in through one worker configuration flag. It is disabled by default until production-like memory and latency measurements pass. The pool size is the existing worker concurrency setting; no second capacity setting is introduced.
- A trusted supervisor process owns a fixed number of runner processes. It never executes generated code itself and does not expose its control channel to the network.
- Each runner starts, imports CadQuery/OCP, signals readiness, accepts one job over an internal authenticated-by-inheritance pipe, and exits after returning one result. It is never returned to the idle pool.
- The runner sets the existing per-request resource limits immediately before executing generated code. Each job still receives a fresh temporary directory; the directory is removed before process exit.
- Generated code continues through the existing AST guard before it is sent to a runner. The existing container boundary remains authoritative: non-root user, read-only root filesystem, tmpfs scratch, no network egress, cgroup limits, and no secrets.
- A request obtains an idle runner only under the existing worker concurrency admission control. If all runners are busy or being replenished, the existing request waiting behaviour applies; no durable queue or new job API is introduced.
- When a runner exits normally, crashes, exceeds a timeout, violates the protocol, or cannot warm, the supervisor removes it from ready capacity and starts one replacement. A failed execution returns the same result shape and error category as today; it is not retried automatically.
- Readiness distinguishes process liveness from usable capacity. The worker reports ready only when the configured pool has been warmed, and exposes degraded readiness when a replacement cannot be started.
- The worker records pool mode, ready/busy/warming counts, runner warmup duration, runner failures, request wait duration, and execution duration. These are operator metrics; neither generated code nor user-facing responses receive runner identifiers.
- The prewarmed path must preserve the exact execution result contract: success flag, STL payload, geometry information, code composition, and user-code failure behaviour are unchanged.
- Deployment configuration must reserve enough memory for all idle imported runners plus active request scratch space. The pool may not be enabled with a concurrency value that exceeds the measured container memory budget.

## Testing Decisions

- Test at the worker HTTP seam: a successful simple model, user-code error, guard rejection, crash, timeout, and export retain the existing response contract in both fresh and prewarmed modes.
- Test isolation as externally observable behaviour: a first request that creates process-local Python state cannot make that state visible to a second request. This proves the one-shot lifecycle rather than relying on a private implementation assertion.
- Test recovery through externally observable capacity: after a runner crash or timeout, a later valid request succeeds once the pool has replenished; the worker never reports full readiness before replacement capacity exists.
- Test that requests beyond configured concurrency do not execute extra generated-code processes concurrently and that container/resource-limit failures remain contained.
- Add a reproducible performance test with the same trivial box used for the baseline. On identical hardware and image, prewarmed mode must reduce median isolated execution latency by at least 5x relative to fresh mode, while returning byte-equivalent geometry facts and a valid STL. Record p50, p95, runner warmup time, RSS per idle runner, and peak container memory.
- Run the existing application and worker integration tests, including chat, variations, lazy STL restoration, and STEP export, because they all share the execution contract.
- Do not unit-test process bookkeeping when an endpoint-level lifecycle test can validate the same outcome.

## Out of Scope

- Reusing a runner after it executes generated code.
- Changing generated CadQuery syntax, replacing CadQuery/OCP, or introducing a mesh-only kernel.
- Asynchronous job IDs, polling, SSE progress, durable queues, Redis, BullMQ, or cross-node dispatch.
- Changing the chat/repair policy, LLM concurrency policy, user quotas, or rate limits.
- Changing local development execution behaviour.
- Persisting CAD sessions or execution artifacts.

## Further Notes

- The performance gain targets startup/import overhead only. Boolean operations,
  fillets, tessellation, and large STL serialization remain real CAD costs and
  need their own measurements.
- The one-shot rule is the central security decision. A conventional persistent
  worker pool would be faster to implement but would invalidate the current
  request-isolation guarantee.
- Roll out behind the configuration flag: establish fresh-mode baseline on the
  target host, measure idle-runner memory, enable with the smallest pool, then
  compare latency and error rate before making it the hosted default.
