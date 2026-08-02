# SPEC21 task list (recomposed — build order)

Recomposed from the W1/W2 split in the spec into a dependency-ordered build
plan. Same scope, grouped by the file/unit each change lives in so the
foundation (context primitives) lands before the wiring that consumes it.

## Phase A — Shared context primitives (no wiring yet)

- [x] `app/version.py` — `VERSION` resolved once at import: `EASYCAD_VERSION` env
      → `git rev-parse --short HEAD` → `"unknown"`.
- [x] `app/log_context.py` — `ContextVar[dict]` (`trace_id`/`session_id`/`user`/
      `version`, `-` defaults) + `ContextFilter` (stamps only missing attrs, so
      `extra=` on a record wins) + `set/update/reset/current` + `configure_logging()`
      (`dictConfig` owning root **and** `uvicorn`/`uvicorn.error`/`uvicorn.access`;
      one handler; context text formatter; **no JSON**).
- [x] `worker/log_context.py` — trace-only mirror (`trace_id`+`version`) + its own
      `configure_logging()`.

## Phase B — W1 request-context logging (wiring)

- [x] `app/main.py` — replace `basicConfig` with `log_context.configure_logging`;
      import `VERSION`.
- [x] `app/main.py` — `_request_context` middleware registered **last (outermost)**:
      `trace_id = secrets.token_hex(8)`, seed ctx (`trace_id`+`version`+`user` from
      auth cookie), store `request.state.trace_id`, `X-Trace-Id` on the normal
      response, **reset ctx via token in `finally`**.
- [x] `app/main.py` — `_session_cookie` fills `session_id` into ctx and sets
      `user=anon:<session-prefix>` when not signed in.
- [x] `app/main.py` — exception handler reads `request.state.trace_id`, stamps it
      via `extra=`, echoes it in the 500 body, sets `X-Trace-Id` on its own
      response. Still does **not** record the crash.
- [x] `app/cadquery_exec.py` — send `X-Trace-Id` (from ctx) on **both**
      `RemoteExecutor.execute`→`/execute` and `export`→`/export`.
- [x] `worker/main.py` — `configure_logging` + per-request middleware seeding ctx
      from inbound `X-Trace-Id`, **reset via token in `finally`** (covers `/execute`
      and `/export`).
- [x] `app/main.py` + `admin.html` — surface `version` on `/api/admin/stats`.

## Phase C — W2 daily crash report

- [x] `app/crashlog.py` — `record(event)` (one scrubbed, length-capped
      `os.write` line under `EASYCAD_CRASH_DIR`, `threading.Lock`, warn-once + no-op
      on unwritable dir), `build_digest(date)`, `claim_report(date)` (atomic
      `O_CREAT|O_EXCL`), `apply_retention(keep=3)`, `_reset_for_tests()`.
- [x] `app/cadquery_exec.py` — `ExportResult{data, code}`; make `export`
      **symmetric** with `execute` (transport/timeout → coded operational,
      no-data/bad-format → `data=None`).
- [x] `app/main.py` — op_error contract: `_raise_operational(request, code)` sets
      `request.state.op_error={service, code}` before raising; `_raise_if_operational`
      + `gen_slot` (server_busy) + STEP-export endpoint route through it.
- [x] `app/main.py` — single chokepoint in `_access_log`: `except` → `kind:error`
      from live exc; response `status>=500` → `kind:operational` from `op_error`;
      `request.state.crash_recorded` guard.
- [x] `app/main.py` — `_maybe_send_daily_report()` (in-memory `_report_day`
      short-circuit → atomic claim → best-effort `send_mail` → retention), fired
      from `_request_context`; `ADMIN_EMAIL` required in `_check_required_env`
      (prod hard-fail); ensure crash dir on boot.

## Phase D — Config / infra

- [x] `Dockerfile` + `worker/Dockerfile` — `ARG EASYCAD_VERSION` → `ENV`.
- [x] `.github/workflows/ci.yml` — `--build-arg EASYCAD_VERSION=<sha>` for both images.
- [x] `.env.prod.example` — `EASYCAD_VERSION` / `EASYCAD_CRASH_DIR` /
      `EASYCAD_CRASH_REPORT`; note `ADMIN_EMAIL` now required in prod.

## Phase E — Tests

- [x] `tests/test_spec21_w1.py` — filter defaults/values, ctx reset (app+worker),
      uvicorn-logger routing, middleware trace id + `X-Trace-Id`, `user` email vs
      `anon:`, worker trace propagation on execute + export.
- [x] `tests/test_spec21_w2.py` — record append/no-op, record-exactly-once,
      worker-failure `service:worker` via op_error, export-outage reaches
      chokepoint, HTTP-200-failure-not-recorded, digest grouping, marker survives
      restart (no re-send), count-based retention (newest 3), prod boot fails
      without `ADMIN_EMAIL`.
- [x] Update `tests/test_spec12_backend.py` for the `ExportResult` return type.
