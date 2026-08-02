# SPEC21: Observability — request-context logging & daily crash report

## Status

Proposed. Two independent but sequenced work items on the shipped multi-tenant
app (SPEC13/14), the isolated worker (SPEC18), and the SPEC19 launch surface
(`/admin`, coded operational notices, `ADMIN_EMAIL`/`send_mail`). Nothing in the
generation pipeline, LLM behaviour, pricing, auth, or the execution/security
model is re-opened.

- **W1 — Request-context logging.** Every log line in both services carries a
  per-request `trace_id`, the caller identity (`user` = email when signed in,
  else `anon:<session>`), and the app `version`, propagated app→worker so a
  worker log correlates to the app request that caused it.
- **W2 — Daily crash report.** 5xx / unhandled errors in both services are
  appended (enriched with the W1 context) to a per-day JSONL file in a mounted
  volume; once per UTC day a lazy trigger emails `ADMIN_EMAIL` a light digest of
  the previous day and prunes files older than the 3-day retention.

W1 is the foundation and ships first — the crash records in W2 are only useful
because they carry the W1 context.

## Problem Statement

The product is deployed and taking real traffic, but the operator is blind to
what actually goes wrong in production:

1. **Logs carry no context.** `logging.basicConfig` emits flat text
   (`%(asctime)s %(levelname)s %(name)s %(message)s`, `app/main.py:106`). The
   access-log middleware writes `METHOD PATH -> STATUS (Nms)`
   (`app/main.py:249`) but no request identity. When a user reports "it broke",
   there is no `trace_id` to quote and no way to tie their session, their build
   version, or a downstream worker failure to a specific request. The data is
   *available* mid-request (`request.state.session_id` at `app/main.py:229`,
   user via `current_session`) — it just never reaches the log lines, and there
   is no app `version` recorded anywhere.
2. **No proactive crash signal.** A 500 is logged once with a traceback
   (`@app.exception_handler(Exception)`, `app/main.py:274`) and then scrolls off.
   Worker execution failures surface to the app but are not retained. There is no
   daily "here is what crashed and why" — the operator would have to be watching
   `docker logs` at the moment of failure. The only existing proactive alert is
   the once-a-day trial-budget email (`_budget_alert`, `app/main.py:361`).

## Goal

Make production failures self-reporting: every log line identifies the request
and the build, a failure in the worker is traceable to the app request that
triggered it, and the operator receives one light daily email summarizing what
crashed — each crash enriched with enough context to investigate — with no new
infrastructure beyond a mounted directory. Explicitly **not** in scope:
centralized log aggregation (Loki/ELK), an error-tracking service
(Sentry/GlitchTip), per-crash real-time alerting, or capturing process-death
(OOM/SIGKILL) that never reaches an exception handler — see *Non-goals*.

---

## W1 — Request-context logging

### Problem
Logs are flat text with no request identity, no caller, no version, and no way
to correlate an app request with the worker call it spawned.

### Implementation Decisions

- **Context carrier: one `ContextVar`, one `logging.Filter`.** A new
  `app/log_context.py` holds a `ContextVar[dict]` with `trace_id`, `session_id`,
  `user`, `version`. A `logging.Filter` subclass reads it and stamps those four
  attributes onto every `LogRecord` (with safe `-` defaults for log lines emitted
  outside a request, e.g. at boot). This reaches **every** existing call site —
  `log.error`, `log.exception`, `easycad.llm`, `cadquery_exec` — with no changes
  at those sites. A `LoggerAdapter` was rejected precisely because it would not.
- **Where context is set, and reset.** Generate `trace_id = secrets.token_hex(8)`
  and populate the ContextVar (trace_id + `version` + `user` from the auth cookie)
  in a context middleware registered so it is the **outermost user middleware**
  (its `finally` runs last, after the access-log middleware, so the crash record
  and access line inside are still stamped); `session_id` is filled in from
  `request.state` where the existing `_session_cookie` middleware computes it
  (`app/main.py:225`). The ContextVar is set via `token = _ctx.set(...)` and
  **reset in a `finally`** (`_ctx.reset(token)`) — sync endpoints run on a
  thread-pool and a task/thread can be reused, so without the reset a later
  request could inherit a previous caller's identity. Always store
  `request.state.trace_id` (survives the ContextVar reset — see next point) and,
  on the normal response path, add the `X-Trace-Id` response header.
- **The exception handler owns trace_id/header on the error path.** Because the
  context middleware's `finally` resets the ContextVar *before* the re-raised
  exception reaches Starlette's `ServerErrorMiddleware`, the
  `@app.exception_handler(Exception)` (`app/main.py:274`) runs with the **default**
  context — so it must not rely on it. The handler reads `request.state.trace_id`
  (still present on the Request object), stamps it explicitly on its
  `log.exception` line (via `extra=`), echoes it in the 500 JSON body, and sets
  the `X-Trace-Id` header on its own response — the middleware never got to add it
  for a request that raised. (Header + trace-id are the handler's *only* new job;
  it still does **not** record the crash — that stays in the one chokepoint.)
- **Configure logging explicitly — do not rely on `basicConfig`.** `basicConfig`
  only touches the root logger and is a no-op once a handler exists, and uvicorn
  installs its own handlers on `uvicorn`, `uvicorn.error`, `uvicorn.access` with
  `propagate=False` — so a root-only filter never sees request/access lines. Use
  `logging.config.dictConfig` at import to own root **and** the three uvicorn
  loggers: one shared handler + a **context-aware text formatter**
  (`… [trace=%(trace_id)s user=%(user)s v=%(version)s] %(message)s`), with the
  context `Filter` attached **to the handler** (so it stamps every record routed
  there regardless of emitting logger). The worker configures logging the same way
  (it currently configures none). **No JSON formatter / `python-json-logger`** —
  the crash JSONL (W2) is written directly by `crashlog.record` from a structured
  dict and never parses stdout, so structured stdout buys nothing now (YAGNI). A
  text formatter with the context fields is enough; JSON/log-shipping stays an
  out-of-scope future concern.
- **App version — baked into the image, both services.** Images ship no `.git`,
  so a `git rev-parse` fallback yields nothing in prod. Add
  `ARG EASYCAD_VERSION` → `ENV EASYCAD_VERSION=$EASYCAD_VERSION` to **both**
  Dockerfiles; CI passes `--build-arg EASYCAD_VERSION=<git tag/SHA>` when building
  each image (the version *is* the built image, not a property of the deploy
  host). `app/version.py` resolves the env once at import, with a
  `git rev-parse --short HEAD` fallback for local dev → `"unknown"` last. A
  runtime `EASYCAD_VERSION` env still overrides the baked default if ever needed.
  This keeps the worker secret-free and volume-free — it gets its version from the
  baked `ENV`, needing no compose change. Surface `version` on `/api/admin/stats`
  so the dashboard shows the running build.
- **App→worker propagation (both worker calls).** The app sends `X-Trace-Id` on
  **every** call that reaches the worker — `RemoteExecutor.execute` → `POST
  /execute` **and** `RemoteExecutor.export` → `POST /export`
  (`app/cadquery_exec.py:231,280`) — otherwise export-path worker logs would not
  correlate. The worker (a small `worker/log_context.py` mirror + filter, same
  shape) seeds its ContextVar from that header in a per-request middleware and
  **resets it via `token` in a `finally`**, exactly like the app — a reused
  task/thread must not inherit a prior request's `trace_id`. The worker has no
  user identity — it logs `trace_id` + `version` only.

### Acceptance
- A single request produces app log lines (access-log, and any error) and, if it
  reaches execution, worker log lines that **all** share one `trace_id`.
- Signed-in requests log `user=<email>`; anonymous requests log
  `user=anon:<session-prefix>`.
- Every line carries the running `version`; `X-Trace-Id` is present on responses
  and echoed in 500 bodies.

---

## W2 — Daily crash report (file sink + lazy digest)

### Problem
Crashes are logged once and lost. There is no retained, enriched record and no
daily summary, so the operator cannot review "what broke yesterday, and why".

### Implementation Decisions

- **Sink: per-day JSONL, app is the sole writer — no worker volume, no
  database.** The crash record is a temporary operational artifact, not product
  data, so it does not belong in the app DB. Crucially, the **worker cannot be a
  writer**: it runs `read_only: true`, as UID 10001, with no volumes and only a
  `/tmp` tmpfs (`docker-compose-prod.yml:44-62`) — mounting a writable host
  directory into it would regress its stateless, secret-free isolation posture
  (SPEC12/18). So the **app is the single writer**. `app/crashlog.py` exposes
  `record(event: dict)` appending **one JSON line** to
  `<EASYCAD_CRASH_DIR>/crashes-<UTC-date>.jsonl`. The app already runs as root and
  owns the existing `./data:/data` mount, so `EASYCAD_CRASH_DIR` defaults to
  `/data/crashes` **inside that same volume** — no new volume, no `chown`/init
  step, no ownership problem. The app creates the subdir on boot; dev falls back
  to `./crashlog`. If the dir is missing/unwritable, `record` logs one warning and
  no-ops — **crash logging must never break a request**. Concurrency: within the
  single app process, a module `threading.Lock` serializes each write, and each
  event is emitted as **one `os.write()` of a single newline-terminated line** to
  an fd opened `O_APPEND` (size-capped by the truncated traceback). That is
  sufficient here — the app runs one process (no `uvicorn --workers`). The
  `PIPE_BUF` guarantee applies to pipes, **not** regular files, so it is not
  relied on; if the app is ever run multi-process, add an `fcntl.flock` around the
  append (noted, not built).
- **What is a "crash": transport/5xx only — a failed generation is not a crash.**
  A user's CadQuery that runs but yields no geometry comes back as
  `success=False` with HTTP **200** (normal product flow — the app is "describe →
  maybe fail → refine"); that is product signal already covered by metrics, **not
  a crash**, and recording it would drown the report in user errors. A crash is a
  **transport/server failure**: an app unhandled exception, or a 5xx. This
  narrowing also dissolves the "HTTP 200 failure the middleware can't see"
  contradiction — we deliberately don't want those.
- **Worker failures reach the chokepoint via a 5xx — on BOTH worker calls.**
  `RemoteExecutor.execute` maps a dead/slow/malformed worker to the coded
  `worker_unavailable` / `execution_timeout` (`app/cadquery_exec.py:247-269`),
  which SPEC19's `_raise_if_operational` turns into a 5xx the chokepoint sees.
  **`export` does not** — it swallows every transport error and returns `None`
  (`app/cadquery_exec.py:285-289`), indistinguishable from a legit empty/
  unsupported-format result, so an export-time worker outage would never become a
  5xx and never be recorded. Fix the root cause and make export **symmetric**:
  `export` distinguishes transport/timeout (→ coded operational) from
  no-data/bad-format (→ `None`), and the export endpoint raises the operational
  case through the same `_raise_if_operational` path. Now both worker calls
  surface outages identically, the single-chokepoint invariant holds, and export
  also gets a correct 5xx (instead of a misleading empty download). *(Cheaper
  alternative if this is deferred: explicitly declare export out of W2 scope — a
  worker outage is still visible via `/execute` failures. Recommended: symmetry.)*
- **Explicit `request.state` contract carries `service`/`code` to the chokepoint.**
  The access-log middleware sees only an HTTP status — it cannot tell an
  app-origin 503 from a worker-origin one, nor recover the `code`. So the site
  that converts a worker/operational failure into an HTTP error
  (`_raise_if_operational` / `_coded_error`) **sets `request.state.op_error =
  {service, code}`** before raising; the chokepoint reads it to fill `service`
  (default `app`) and `code`. Without this the crash line would mislabel worker
  outages as app errors.
- **One chokepoint, no double-count.** Recording happens in a **single place —
  the access-log middleware** (`app/main.py:249`), with two mutually-exclusive
  branches: its `except` branch sees unhandled exceptions re-raised on the way to
  `ServerErrorMiddleware` (→ `kind: error` from the live `exc`), and its normal-
  response branch sees `status >= 500` responses that never raised, i.e.
  operational/explicit 5xx incl. the mapped worker failures above, enriched from
  `request.state.op_error` (→ `kind: operational`). The exception handler does
  **not** record (it only owns trace_id/header on the error path, W1) — removing
  the double-write the two-path design would otherwise cause. A defensive
  `request.state.crash_recorded` flag guards against any future overlap. Each
  event carries the W1 context plus specifics:
  `ts, trace_id, service, method, path, status, kind, exc_class, exc_message,
  traceback_tail, code, user, session_id, version`. `kind` separates a genuine
  bug (`error`) from an operational 5xx (`server_busy`, `execution_timeout`,
  `worker_unavailable`) so the digest reports them apart. **Scrubbing is
  mandatory**: never write BYOK keys, auth tokens, or full prompt text —
  `exc_message`/`traceback_tail` are length-capped and secret-scrubbed.
- **Trigger: lazy, at-most-once (single atomic claim).** Delivery semantics are
  chosen explicitly: **at-most-once** — a report may rarely be lost (crash between
  claim and send), never duplicated. SMTP + filesystem cannot give exactly-once,
  and for a daily ops digest a possible-loss is fine while a duplicate is noise;
  KISS wins. A module guard `_report_day` is only a hot-path short-circuit (avoid
  the FS on every request); the authority is a **single atomic marker**. On the
  first request of a new UTC day, `_maybe_send_daily_report()` claims the day with
  **one** atomic `O_CREAT|O_EXCL` create of `reports/<date>.sent` (this both wins
  the race between concurrent workers and marks it done across restarts); the
  winner then builds yesterday's digest and best-effort `send_mail`s it. **No
  retry, no two-phase `.sending`** — if the mail fails, the report for that day is
  lost by design. It then applies retention (below). All wrapped so it never
  breaks the request (the `_budget_alert` contract). No cron, no scheduler thread,
  no new infrastructure.
- **The digest is light.** Subject `text2part: daily crash report <date> — N
  crashes`. Body: totals per `service` (app/worker) and per `kind`, then the top
  signatures grouped by `(exc_class + top traceback frame)` → count, first/last
  time, and **one** representative (`trace_id`, path, truncated message). Full
  detail stays in the JSONL file, found by `trace_id`. On a zero-crash day send a
  one-line "0 crashes" heartbeat (so silence is never ambiguous);
  `EASYCAD_CRASH_REPORT` can disable the mail entirely.
- **`ADMIN_EMAIL` is now mandatory.** It is the destination for the crash report
  (and the SPEC19 ops mail), so it moves into the fail-fast `_check_required_env`
  (`app/main.py`): in production (`EASYCAD_SECURE_COOKIES=1`) the app **refuses to
  start** without it, alongside the existing secret checks; dev warns. This
  removes the "unset → silently no crash mail" foot-gun in prod.
- **Retention: keep the last 3 dated files (count-based, one rule).** Sort
  `crashes-<date>.jsonl` by date and delete all but the newest 3 — **not** an
  "older than 3 days" age rule, which would disagree on days with no crashes (no
  file is created, so a fixed 3-day window and "last 3 files" diverge). Count-
  based matches the original ask ("keep the last three files") and is
  deterministic regardless of quiet days. `reports/*` markers older than the
  oldest retained crash date are pruned in the same pass.

### Acceptance
- An app unhandled 500, and a transport/operational worker failure (mapped to a
  5xx), each write one scrubbed JSON line (`service: app` / `service: worker`)
  with a matching `trace_id` — an unhandled exception exactly once. A normal
  failed generation (HTTP 200, no geometry) writes **no** crash line.
- The first request of a new UTC day writes `reports/<date>.sent` in one atomic
  create, emails a grouped digest of the previous day, and leaves at most 3 dated
  crash files on disk; a restart on the same day does **not** re-send (at-most-
  once — a mail failure loses that day's report, by design).
- A missing/unwritable crash dir or a failing SMTP never turns into a failed user
  request. In prod the app refuses to boot without `ADMIN_EMAIL`.

---

## Config (new env)

| Var | Default | Purpose |
| --- | --- | --- |
| `EASYCAD_VERSION` | baked at build; git SHA / `unknown` fallback | Running build id, stamped on every line |
| `EASYCAD_CRASH_DIR` | `/data/crashes` | Crash JSONL dir, **inside the existing `./data` volume** (app-owned) |
| `EASYCAD_CRASH_REPORT` | `1` | Send the daily crash email (`0` disables) |

`EASYCAD_VERSION` is supplied as a Dockerfile **build arg** in both images (CI
sets it from the git tag/SHA); a runtime env still overrides it. No new volume:
`EASYCAD_CRASH_DIR` lives under the app's existing `/data` mount, and the worker
gets **no** volume. `ADMIN_EMAIL` becomes **required in prod** (see W2). Reuses
existing `send_mail` and the once-per-UTC-day guard. No `EASYCAD_LOG_FORMAT` /
JSON logging — a context text formatter is enough (YAGNI).

## Non-goals / known gaps

- **Process death** (OOM, SIGKILL, segfault) never reaches an exception handler,
  so it leaves no crash line. Covered later by an external uptime/log-scrape
  monitor — noted, not built here.
- **No centralized aggregation or error-tracking service.** The JSONL sink is a
  deliberately temporary, self-contained solution. If crash volume grows enough
  to need grouping/search/graphs, the structured crash JSONL plus the
  `trace_id`/`version`/`user` context W1 stamps on every line make GlitchTip/
  Sentry or Loki a straightforward upgrade — out of scope here.
- **No per-crash real-time paging** — the daily digest is the only alert.

## Test plan

- W1: filter stamps defaults outside a request and real values inside; the
  ContextVar is reset in `finally` in **both** app and worker middleware (no leak
  across reused tasks); `dictConfig` routes `uvicorn.access`/`uvicorn.error`
  through the filtered handler; middleware sets `trace_id`/`X-Trace-Id`; `user`
  resolves email vs `anon:`; the worker seeds its ContextVar from the inbound
  `X-Trace-Id` on **both** `/execute` and `/export`.
- W2: `record` appends a valid scrubbed line to the dated file and no-ops on an
  unwritable dir; an unhandled exception is recorded **exactly once** (chokepoint,
  not double-written by the exception handler); a worker transport/operational
  failure is recorded as `service: worker` while a normal HTTP-200 failed
  generation is **not** recorded; the digest builder groups by signature and
  counts per `service`/`kind`; the lazy trigger sends once and, after a simulated
  restart (module guard cleared) with a `reports/<date>.sent` marker present, does
  **not** re-send (at-most-once); retention prunes to 3 dated files;
  `_check_required_env` fails in prod without `ADMIN_EMAIL`. Follow the
  `metrics._reset_for_tests()` style for resettable module state.
