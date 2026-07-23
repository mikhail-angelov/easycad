# SPEC12: Pluggable Execution Backend — Local Subprocess & Isolated Worker Container

## Status

Proposed implementation specification. Extends SPEC11 (`spec11.md`) without
changing the chat/step UX or any LLM behaviour. Adds a second, opt-in
execution backend for hosted (SaaS) deployment. **The local single-process
mode is preserved unchanged and remains the default.**

## Goal

Let the same application run in two deployment shapes from one codebase:

1. **Local (default, unchanged):** the app executes CadQuery in an in-process
   subprocess exactly as today (`app/cq_worker.py`). No worker, no Docker, no
   network hop. `make run` behaves identically to SPEC11.
2. **Hosted (opt-in):** CadQuery execution is delegated over HTTP to a
   separate, hardened **worker container** that runs untrusted LLM-generated
   code with all isolation Docker Compose can provide. The app container holds
   the LLM key and user data; the worker holds neither and has no network
   egress.

The switch between the two is a single environment variable. Nothing in the
request flow, endpoints, or frontend changes.

## Core principle: local stays as-is, worker is purely additive

- `execute(code) -> ExecResult` in `app/cadquery_exec.py` remains the **single
  public entry point**. Its signature and `ExecResult` fields (`success`,
  `stl_base64`, `geometry_info`, `code_with_geometry`, `error`) do not change.
  All eight call sites in `app/main.py` stay byte-for-byte the same.
- The current subprocess implementation becomes the **local backend**, selected
  by default. When no worker is configured, behaviour is identical to SPEC11.
- The worker lives in a **separate top-level folder `worker/` with its own
  Dockerfile**, its own dependencies, and its own FastAPI app. It is not
  imported by the app process. Removing the `worker/` folder must not affect
  local mode.

## Architecture

```
LOCAL (default, unchanged)                 HOSTED (opt-in, EASYCAD_WORKER_URL set)
──────────────────────────                 ───────────────────────────────────────
┌────────────────────────┐                 ┌───────────────┐      ┌──────────────────┐
│ app process            │                 │ app container │ HTTP │ worker container │
│  main.py               │                 │  main.py      │─────▶│  /execute        │
│  cadquery_exec.execute │                 │  execute()    │      │  code_guard      │
│    └─ LocalExecutor    │                 │   └Remote     │      │  setrlimit child │
│        subprocess      │                 │  LLM key,     │      │  cq_worker core  │
│        cq_worker       │                 │  user data    │      │  no net, no key  │
└────────────────────────┘                 └───────────────┘      └──────────────────┘
```

## Executor abstraction (app side)

`app/cadquery_exec.py` gains an internal backend selection; the public
`execute()` delegates to it.

```
Executor (protocol):
    execute(code: str) -> ExecResult

LocalExecutor:   # current behaviour, verbatim
    spawns `python -m app.cq_worker` via subprocess, TIMEOUT_SECONDS, etc.

RemoteExecutor:  # new
    POST {"code": code} to f"{EASYCAD_WORKER_URL}/execute"
    parse the JSON body into ExecResult (same fields)
    map transport/timeout failures to ExecResult(success=False, error=...)
```

`execute()` builds the backend once at import/startup:

- If `EASYCAD_WORKER_URL` is **unset** → `LocalExecutor` (default, local mode).
- If `EASYCAD_WORKER_URL` is **set** → `RemoteExecutor` pointing at it.
- Optional explicit override `EASYCAD_EXECUTOR=local|remote` wins if present
  (useful for tests).

No call site changes. The geometry-info block handling
(`append_geometry_block`, `strip_geometry_block`) stays in the app for both
backends: the worker returns `geometry_info` text (as `cq_worker` does today)
and the app composes `code_with_geometry`. This keeps the wire contract minimal
and identical in shape to the current stdin/stdout job.

## ExecResult wire contract (app ↔ worker)

`POST /execute` request:
```json
{ "code": "import cadquery as cq\n..." }
```

Response (HTTP 200 for all *execution* outcomes, including user-code errors):
```json
{
  "success": true,
  "stl_base64": "…",          // present iff success
  "geometry_info": "# ── Geometry info…",  // present iff success
  "error": null                // populated string iff !success
}
```

- User-code failures (syntax error, missing `result`, OCP crash, timeout,
  guard rejection) are **200 with `success:false`** — same philosophy as the
  current `ExecResult`: failures come back populated, not raised.
- Only transport-level problems (worker down, 5xx, malformed body) produce a
  `RemoteExecutor` synthesised `ExecResult(success=False, error=...)`.
- The worker returns STL as base64 (parity with `cq_worker`'s STL-to-file →
  the worker reads the file and base64-encodes before responding).

## Worker service (`worker/` folder)

Self-contained FastAPI app. Folder layout:

```
worker/
  Dockerfile
  requirements.txt         # fastapi, uvicorn[standard], cadquery
  main.py                  # FastAPI: POST /execute, GET /healthz
  code_guard.py            # Level 0 AST allowlist
  limits.py                # setrlimit child-process runner
  cq_exec_core.py          # shared execution core (see below)
```

**Shared execution core, single source of truth.** The pure execution logic in
`app/cq_worker.py` (exec code → export STL → build geometry-info) is the same
in both modes. To avoid divergence, that core is factored into a module the
worker build context copies in (`cq_exec_core.py`), or the worker `Dockerfile`
COPYs `app/cq_worker.py` directly. Local mode keeps invoking it via subprocess;
the worker invokes it in a resource-limited child. Neither copy contains the
LLM key or any app state.

### Endpoints

- `POST /execute` → runs `code_guard` then the limited child execution; returns
  the wire contract above.
- `GET /healthz` → `{"ok": true}` for Compose `depends_on`/healthcheck.

### Per-request isolation inside the shared worker

The worker is **one long-lived container shared by all users**, so container
limits alone do not isolate requests from each other. Each `/execute` therefore:

1. runs `code_guard` (Level 0) and rejects on violation before any exec;
2. spawns a **fresh child process** for the actual `exec`, wrapped with
   `resource.setrlimit`:
   - `RLIMIT_CPU` (seconds) — kills infinite loops per request;
   - `RLIMIT_AS` (address space, e.g. 1.5 GB) — caps memory bombs;
   - `RLIMIT_NPROC` — blocks fork bombs;
   - `RLIMIT_FSIZE` — caps written file size;
   plus a wall-clock `timeout` (reuse `CADQUERY_WORKER_TIMEOUT_SECONDS`);
3. uses a scratch dir on `tmpfs` (`tempfile.TemporaryDirectory()`), wiped after;
4. is gated by a concurrency semaphore `EASYCAD_WORKER_CONCURRENCY` (default 2)
   so one heavy request cannot starve the shared worker; excess requests queue.

The container rootfs is read-only, so nothing persists between requests and no
implant survives a request. Result: the container protects the host; the
per-request child protects users from each other.

## Level 0 code guard (`worker/code_guard.py`)

Static AST allowlist run **in the worker, mandatory**, before every exec. It is
defence-in-depth, **not** the security boundary (the container is), but it stops
casual and accidental abuse cheaply.

- Allowed imports only: `cadquery` (and `as cq`), `math`. Everything else
  rejected (`os`, `sys`, `subprocess`, `socket`, `shutil`, `pathlib`, …).
- Reject `__import__`, `eval`, `exec`, `compile`, `open`, `input`.
- Reject access to dunder attributes used for sandbox escape
  (`__globals__`, `__builtins__`, `__subclasses__`, `__class__`, `__mro__`, …).
- Reject on parse failure.
- On rejection: return `success:false` with a clear
  `error: "Code rejected by guard: <reason>"`.

**Local mode preserves current behaviour:** `LocalExecutor` does **not** gate on
the guard by default (SPEC11 local flow has no validator, and this must stay
identical). The guard module may be enabled locally via an opt-in flag
(`EASYCAD_LOCAL_GUARD=1`) but defaults off. The worker always enforces it.

## Deployment

### Dockerfiles

- `app/Dockerfile` (or repo-root, existing build): builds the app image
  (FastAPI + built frontend + `LocalExecutor` still present but unused when a
  worker URL is set). Needs CadQuery only if it must ever fall back to local.
- `worker/Dockerfile`: `python:3.11-slim` + `worker/requirements.txt`
  (fastapi, uvicorn, cadquery), non-root user (`10001:10001`), runs
  `uvicorn worker.main:app --host 0.0.0.0 --port 8853`.

### docker-compose.yml (hosted)

```yaml
services:
  app:
    build: .
    environment:
      - DEEP_SEEK_KEY=${DEEP_SEEK_KEY}
      - EASYCAD_WORKER_URL=http://worker:8853
    ports: ["8852:8852"]
    volumes: ["userdata:/data"]      # user settings/sessions only here
    networks: [egress, internal]
    depends_on: [worker]

  worker:
    build: ./worker
    networks: [internal]             # ONLY private net → no internet egress
    read_only: true
    tmpfs: ["/tmp:size=256m"]
    cap_drop: ["ALL"]
    security_opt:
      - no-new-privileges:true   # default seccomp profile applies automatically
    mem_limit: 512m
    pids_limit: 128
    cpus: "1.0"
    user: "10001:10001"
    # no secrets in env, no volumes
    # runtime: runsc                 # future: gVisor, drop-in, no code change

networks:
  egress: {}                         # internet (LLM) + published port
  internal: { internal: true }       # no route outside → worker cannot egress

volumes:
  userdata: {}
```

Local deployment (default) runs the app **without** this compose file and
without `EASYCAD_WORKER_URL`: pure SPEC11 behaviour.

## Security model & residual risk

- **Secrets:** LLM key and user data live only in the app container; the worker
  env is clean. A worker breakout does not directly leak the key.
- **Network:** worker on an `internal: true` network → no exfiltration, no
  outbound attacks, no mining.
- **Host:** read-only rootfs, `cap_drop: ALL`, `no-new-privileges`, seccomp,
  mem/pids/cpu limits contain a breakout inside the worker.
- **Users from each other:** per-request child + `setrlimit` + stateless
  read-only worker + concurrency cap.
- **Residual:** app and worker share the host kernel — a kernel exploit could
  still escape. Accepted at low traffic. Upgrade path is a one-line
  `runtime: runsc` (gVisor) on the worker service, **no code change**, giving
  per-request microVM-grade isolation when desired.

## Acceptance criteria

1. With `EASYCAD_WORKER_URL` unset, `execute()` uses `LocalExecutor` and every
   SPEC11 flow (chat, execute, execute-manual, initial code, revert, export,
   variations) behaves exactly as before — verified by the existing backend
   test suite passing unchanged.
2. With `EASYCAD_WORKER_URL` set, `execute()` returns an `ExecResult`
   indistinguishable in shape from the local one for the same code (success and
   failure cases), driven entirely through the worker HTTP endpoint.
3. The `worker/` folder builds and runs standalone from its own Dockerfile with
   no import of `app/`; deleting `worker/` does not break local mode.
4. The worker rejects a probe payload (`import os` / `__subclasses__` escape)
   via `code_guard` with `success:false` and a guard error, before exec.
5. A worker request with an infinite loop is killed by `RLIMIT_CPU`/timeout and
   returns `success:false` without affecting a concurrent valid request.
6. In the compose deployment, the worker container has no network egress
   (a code attempt to open an outbound socket fails) and no `DEEP_SEEK_KEY` in
   its environment.
7. The LLM-code execution core is not duplicated in a way that can silently
   diverge: local and worker run the same geometry-info/STL logic.

## Non-goals

- No per-user or per-request **container** spawning (no Docker socket in the
  worker — that would be a host-root hole). Isolation between requests is the
  per-request child + rlimits inside the shared worker.
- No change to the chat pipeline, refiner/triage, step store, persistence
  format, or frontend.
- No authentication / multi-tenant accounts / billing — separate concern.
- No gVisor/Firecracker setup now; left as the documented one-line upgrade.
- No new CadQuery operations or LLM providers.
