# EasyCAD — Architecture

Incremental, text-driven 3D-model builder for 3D printing. The user describes
one change at a time in natural language; the backend generates CadQuery
(parametric CAD in Python) code, executes it in an isolated worker, and returns
an STL that the browser renders. Models evolve step by step, each step appending
one feature to the accumulated code.

This document maps the whole system: backend and frontend components with their
responsibilities, the execution/isolation model, and the main use cases with the
components each one touches.

- **SPEC11** — the core chat builder (three-panel UI + step history).
- **SPEC12** — pluggable execution backend; untrusted code runs in a hardened worker container.
- **SPEC13** — multi-tenant SaaS: ephemeral in-memory sessions, magic-link auth, per-user BYOK keys.

---

## 1. System topology

Two deployment shapes from one codebase, selected by environment:

```
LOCAL / DESKTOP (default)                 HOSTED / SaaS (easycad.bconf.com)
─────────────────────────                 ─────────────────────────────────────────────
┌───────────────────────────┐             ┌──────────────────┐   internal   ┌────────────────────┐
│ app process (FastAPI)      │             │ app container    │─────net─────▶│ worker container   │
│  in-process subprocess     │             │  FastAPI + SPA   │   HTTP       │  /execute          │
│  (LocalExecutor→cq_worker) │             │  LLM key, users, │              │  code_guard +      │
│  serves built SPA          │             │  sessions        │              │  setrlimit child + │
│  CadQuery runs locally     │             │  (no CadQuery)   │              │  cq_worker (CadQ.) │
└───────────────────────────┘             └──────────────────┘              │  no key, no egress │
   make run, no worker                     redoproxy (TLS) ─┘               └────────────────────┘
```

- **Local:** `execute()` uses `LocalExecutor` (a subprocess), no worker, no
  Docker, no network hop. Identical to SPEC11 behaviour.
- **Hosted:** `EASYCAD_WORKER_URL` is set → `execute()` uses `RemoteExecutor`,
  delegating CadQuery to the worker container over an internal (egress-less)
  network. The app tier holds secrets and user data; the worker holds neither.

The switch is a single env var; no application code differs between modes.

---

## 2. Request lifecycle

1. **Session middleware** (`app/main.py`) ensures every visitor has an opaque
   `easycad_session` cookie; sets it on first response.
2. **`current_session` dependency** resolves the in-memory `Session` from the
   registry and links it to a user if a valid `auth_token` (JWT) cookie is present.
3. The endpoint operates on that session's `SessionStore`, resolves the caller's
   LLM settings (BYOK), and — for generation — calls the LLM then `execute()`.
4. `execute()` dispatches to `LocalExecutor` or `RemoteExecutor`; the worker (or
   local subprocess) runs the code and returns STL + geometry info.

---

## 3. Backend components (`app/`)

| Module | Responsibility |
|---|---|
| `main.py` | FastAPI app. Session middleware + `current_session` dependency, lifespan sweeper, all HTTP endpoints (auth, settings, session/steps, chat/execute/variations, project export/import, STL export), SPA static serving. Resolves BYOK provider/model/key per request and threads it into the LLM calls. |
| `store.py` | `SessionStore` + `Step` — ordered in-memory step history for one CAD session (linear + revert, `parent_id` for future branching). Text-only project serialize/load (`to_project` / `load_project`); STL is never persisted, regenerated on demand. |
| `session_registry.py` | `SessionRegistry` / `Session` — many `SessionStore`s keyed by session id, each with anonymous settings + optional `user_id`. Sliding idle TTL, background sweeper eviction, LRU capacity cap. (SPEC13) |
| `llm.py` | Stage-2 **code generator**. Provider registry (deepseek/openrouter/openai), `INITIAL_CODE` (starting box), the POC-proven system prompt, `make_client(provider, api_key)` (BYOK, env fallback), `generate_code(...)`. |
| `refiner.py` | Stage-1 **triage**. One LLM call classifies a request vs the current model → `ready` / `refine` / `clarify` / `invalid`; returns a refined prompt, questions, or a reason (in the user's language). |
| `cadquery_exec.py` | Public `execute(code) -> ExecResult` + backend selection. `LocalExecutor` (subprocess to `cq_worker`, optional `EASYCAD_LOCAL_GUARD`), `RemoteExecutor` (HTTP to worker). Owns the geometry-info block strip/append. |
| `cq_worker.py` | The **execution core** (shared with the worker image). Reads a JSON job, `exec`s the code, exports `result` to STL, computes the geometry-info comment block. Runs in a child process for OCP-crash isolation. |
| `code_guard.py` | **Level 0 AST allowlist** (defence-in-depth, not the boundary). Rejects non-`cadquery`/`math` imports, `eval`/`exec`/`open`, dunder-attribute escapes. Shared: always enforced in the worker; opt-in locally via `EASYCAD_LOCAL_GUARD`. |
| `db.py` | SQLite store for **accounts only** (`users`: id, email, settings JSON). Durable; CAD sessions are never stored here. BYOK key stored plaintext (decision). (SPEC13) |
| `jwt_utils.py` | Minimal HS256 JWT (stdlib HMAC) — signs/verifies magic-link and session tokens. (SPEC13) |
| `mail.py` | Transactional email via Yandex-postbox SMTP (STARTTLS); dev fallback prints the link to the console. Used for magic links. (SPEC13) |
| `ratelimit.py` | In-memory fixed-window `RateLimiter` (login anti-spam, per-session generation cap). Single-instance. (SPEC13) |

### Worker service (`worker/`) — hosted execution tier

| File | Responsibility |
|---|---|
| `main.py` | FastAPI worker: `POST /execute` (guard → limited run) and `GET /healthz`; per-request concurrency semaphore. |
| `limits.py` | Runs the code in a fresh child with `setrlimit` (CPU/AS/NPROC/FSIZE) + wall timeout, tmpfs scratch wiped after — per-request isolation inside the shared worker. |
| `Dockerfile` | Vendors the shared core (`app/cq_worker.py`, `app/code_guard.py`) + worker code; installs CadQuery/OCP; non-root, runs uvicorn on 8853. |

---

## 4. Execution & isolation model

```
execute(code)
  ├─ LocalExecutor  → [optional guard] → subprocess: python -m app.cq_worker  → STL
  └─ RemoteExecutor → HTTP POST /execute → worker:
        code_guard.check()            (Level 0 AST allowlist, mandatory)
        limits.run(): fresh child + setrlimit + tmpfs → cq_worker → STL
```

Defence in depth for untrusted, LLM-generated code (hosted mode):

- **Container** (`docker-compose-prod.yml`): `read_only`, `cap_drop: ALL`,
  `no-new-privileges`, seccomp, mem/pids/cpu caps, non-root, **no network egress**
  (`internal: true`), **no secrets in env** → protects the host.
- **Per-request** (`limits.py`): fresh `setrlimit` child + tmpfs → protects users
  from each other inside the one shared worker.
- **Level 0 guard** (`code_guard.py`): cheap AST allowlist, stops casual abuse
  before exec (not the security boundary).
- Future one-line upgrade: `runtime: runsc` (gVisor) on the worker service.

> ⚠ **Local mode is a trusted-user boundary.** `LocalExecutor` runs arbitrary
> Python on the host with normal builtins and **no isolation** — the guard is
> off unless `EASYCAD_LOCAL_GUARD=1`. This is safe only because `make run` binds
> to `127.0.0.1` (loopback) for a single trusted user. **Never bind local mode to
> a non-loopback address without the worker or, at minimum, `EASYCAD_LOCAL_GUARD=1`.**
> Multi-tenant/public serving must use the hosted worker path (SPEC12).

---

## 5. Frontend components (`frontend/src/`) — Preact + Vite + Zustand

| File | Responsibility |
|---|---|
| `main.tsx` | Mounts `<App/>`. |
| `app.tsx` | App shell: top bar (Save/Load project, New model, `<Account/>`), three-panel workspace, bottom timeline. Calls `init()` on mount. |
| `store.ts` | Zustand store — the single source of client state: steps, current code, STL/geometry, provider/model, chat log, pending clarification/proposal/invalid/variations, auth (authenticated/email/hasKey), busy/error. All API round-trips and their state transitions live here. |
| `api.ts` | Typed fetch client for every endpoint (session, chat, variations, commit, execute-manual, revert, project export/import, auth, settings) + shared response types. |
| `viewer3d.ts` | three.js STL viewer engine: loads base64 STL, Z-up→Y-up, orbit controls, auto-fit, grid. |
| `components/Editor.tsx` | Monaco code editor over the current CadQuery code; "Run" executes manual edits (`runManual`). |
| `components/Viewer.tsx` | Hosts the three.js viewer; renders the current STL, export button. |
| `components/Chat.tsx` | Chat panel: prompt input, refine toggle, provider dropdown + model override, chat log, clarify questions, refine-proposal confirm/edit, invalid notice, variation cards (×3), Send. |
| `components/Timeline.tsx` | Horizontal step timeline; click a node to revert (`revert`). |
| `components/Account.tsx` | Sign-in by email (magic link), LLM-key + provider settings panel, sign-out, delete account. (SPEC13) |

---

## 6. State & data model

- **CAD working state** — in-memory only, per session (`Session.store`,
  `SessionStore` of `Step`s). Never written to disk. Evicted on idle TTL.
- **User persistence of CAD work** — the user's own responsibility via
  **project export/import** (a text-only JSON of steps; STL omitted, regenerated
  on load).
- **Accounts & settings** — durable in SQLite (`users`), the only server-side
  durable store. Per-user `{provider, model, key}` (key plaintext by decision).
- **Sessions** — `easycad_session` cookie → `Session`; `auth_token` JWT cookie →
  logged-in user linkage.

---

## 7. Auth & settings (SPEC13)

- **Magic link** (mirrors playground): `POST /api/auth/login {email}` → find-or-
  create user → email a short-lived (15 min) magic JWT link → `GET
  /api/auth/callback` verifies it and sets a 30-day session JWT cookie. Stateless
  (no token table). Login never reveals whether the account existed.
- **BYOK key resolution** per generation: session settings (anonymous) → user DB
  settings (authed) → server env fallback (disabled in SaaS via
  `EASYCAD_REQUIRE_USER_KEY`). The key is used by the app to call the LLM and is
  **never** passed to the worker.
- **Anonymous vs authed settings:** anonymous settings live in the in-memory
  session (lost on TTL); authenticated settings persist in SQLite.

---

## 8. API endpoints

| Group | Endpoints |
|---|---|
| Auth | `POST /api/auth/login`, `GET /api/auth/callback`, `POST /api/auth/logout`, `GET /api/auth/me`, `DELETE /api/auth/me` |
| Settings | `GET /api/settings`, `PUT /api/settings` |
| Session/steps | `GET /api/session`, `POST /api/session/reset`, `GET /api/steps`, `GET /api/steps/{id}`, `POST /api/steps/{id}/revert` |
| Generation/exec | `POST /api/chat`, `POST /api/refine`, `POST /api/variations`, `POST /api/commit`, `POST /api/execute`, `POST /api/execute-manual` |
| Project/export | `GET /api/project/export`, `POST /api/project/import`, `GET /api/export/{id}` |
| SPA | `GET /{path}` (catch-all, static index) |

---

## 9. Main use cases (with components involved)

### UC1 — Describe a change → new model step (the core loop)
`Chat.tsx` → `store.sendChat` → `api.chat` → **`main.api_chat`**: `_resolve_llm`
(BYOK) → `refiner.triage`. Then either a refine proposal / clarify questions /
invalid notice come back to `Chat.tsx`, or on `ready`/confirm → `llm.generate_code`
→ `cadquery_exec.execute` (→ worker/`cq_worker`) → new `Step` in `SessionStore` →
`store` updates code + `viewer3d` renders the STL.
Components: Chat, store, api, main, refiner, llm, cadquery_exec, cq_worker, store(Step), Viewer/viewer3d.

### UC2 — Edit code by hand and run it
`Editor.tsx` (Monaco) → `store.runManual` → `api.executeManual` →
**`main.api_execute_manual`** → `execute()` → manual `Step`. Viewer updates.
Components: Editor, store, api, main, cadquery_exec, cq_worker, store(Step), Viewer.

### UC3 — Generate several variations, pick one
`Chat.tsx` (×3) → `store.sendVariations` → `api.variations` →
**`main.api_variations`** (triage once, then N generations at temp 0.7, nothing
committed) → candidate cards in `Chat.tsx` → preview in `Viewer` → `store.commitVariation`
→ `api.commit` → **`main.api_commit`** → committed `Step`.
Components: Chat, store, api, main, refiner, llm, cadquery_exec, Viewer.

### UC4 — Navigate history / revert
`Timeline.tsx` → `store.revert` → `api.revert` → **`main.revert_step`** (moves the
`SessionStore` current pointer) → `store.applySession` → Editor + Viewer sync.
Components: Timeline, store, api, main, store(SessionStore), Editor, Viewer.

### UC5 — Save / load a project
Save: top-bar link → `GET /api/project/export` → **`main.export_project`**
(`store.to_project`, text-only). Load: `app.tsx` file input → `store.importProject`
→ `api.importProject` → **`main.import_project`** (`store.load_project`) → session
replaced, `viewer3d` re-renders.
Components: app.tsx, store, api, main, store(SessionStore), Viewer.

### UC6 — Sign in (magic link) & set BYOK key
`Account.tsx` → `store.login` → `api.login` → **`main.auth_login`** → `db.get_or_create_user`
+ `jwt_utils.sign` + `mail.send_mail`. User clicks the emailed link →
`GET /api/auth/callback` → session cookie set → app reloads authenticated.
Set key: `Account.tsx` → `store.saveKey` → `PUT /api/settings` →
**`main.put_settings`** → `db.update_settings` (authed) or in-memory (anon).
Components: Account, store, api, main, db, jwt_utils, mail, session_registry.

### UC7 — Session lifecycle (multi-tenant)
Every request: session middleware + `current_session` → `SessionRegistry.get_or_create`
(touch `last_access`). A lifespan sweeper evicts idle sessions past
`EASYCAD_SESSION_TTL`; an LRU cap bounds memory. Two browsers = two isolated sessions.
Components: main (middleware/lifespan), session_registry, store(SessionStore).

### UC8 — Hosted execution (untrusted code isolation)
`main.execute()` → `RemoteExecutor` → worker `POST /execute` → `code_guard.check`
→ `limits.run` (setrlimit child) → `cq_worker` → STL back. Container has no egress
and no key.
Components: cadquery_exec(RemoteExecutor), worker/main, code_guard, worker/limits, cq_worker.

---

## 10. Configuration (env)

| Var | Purpose |
|---|---|
| `DEEP_SEEK_KEY` / `OPEN_ROUTER_KEY` / … | Provider keys (local/dev fallback; BYOK in SaaS) |
| `EASYCAD_WORKER_URL` | Set → hosted mode (RemoteExecutor); unset → local |
| `EASYCAD_LOCAL_GUARD` | `1` enables the AST guard in local mode (default off) |
| `CADQUERY_WORKER_TIMEOUT_SECONDS` | Execution wall-clock timeout |
| `EASYCAD_WORKER_CONCURRENCY` / `_CPU_SECONDS` / `_AS_MB` / `_NPROC` / `_FSIZE_MB` | Worker per-request limits |
| `JWT_SECRET`, `APP_URL` | Auth: token signing + magic-link base URL |
| `MAIL_FROM`, `POST_SERVICE_URL`, `POST_USER`, `POST_PASS` | Magic-link email (SMTP) |
| `EASYCAD_DB_PATH` | SQLite accounts DB path |
| `EASYCAD_SESSION_TTL`, `EASYCAD_MAX_SESSIONS` | Session eviction / cap |
| `EASYCAD_REQUIRE_USER_KEY` | SaaS: no server-key fallback |
| `EASYCAD_SECURE_COOKIES`, `EASYCAD_GEN_RATE_LIMIT` | Secure cookies; per-session generation rate |

---

## 11. Deployment & CI

- **Images:** `Dockerfile` (app, ~216 MB, no CadQuery) and `worker/Dockerfile`
  (~2.1 GB, CadQuery/OCP). `docker-compose-prod.yml` wires app on `proxy-net`
  (redoproxy TLS for `easycad.bconf.com`) + `internal` (to worker); worker on
  `internal` only (no egress), hardened.
- **CI** (`.github/workflows/ci.yml`): pytest + frontend build, then build & push
  both images to `ghcr.io`.
- **`Makefile`**: `run` (local), `build` (frontend), `install`/`deploy` (ssh:
  pull images, `compose down/up`). See `docs/DEPLOY.md`.

---

## 12. Testing (`tests/`)

- `test_api.py` — session bootstrap, execute (stateless), manual step + export,
  revert, session isolation between clients.
- `test_auth.py` — magic-link flow, anon vs user settings, key never returned,
  logout, delete account.
- `test_persistence.py` — store roundtrip, project export/import, reset, no
  working-state file written.
- `test_cadquery_exec.py` — CadQuery execution + geometry-info.
- `test_spec12_backend.py` — executor selection, AST guard, local-guard opt-in.

---

## 13. Key design decisions & constraints

- **LLM writes CadQuery code** — the pivot that made the product work (SPEC11);
  reliability comes from small incremental steps + explicit geometry info.
- **CAD state is ephemeral** — in-memory, TTL-evicted; export/import is the
  durable path. No working state on the server.
- **Untrusted code, layered isolation** — container + per-request rlimits + AST
  guard; the guard is defence-in-depth, the container is the boundary.
- **Local mode is trusted-user only** — arbitrary Python, no isolation, loopback
  bind (see the warning in §4). Anything public/multi-tenant must use the worker.
- **Input bounds** — HTTP body-size middleware + per-field `max_length` +
  per-session step cap protect app and worker memory before parsing/retention.
- **BYOK, key never in the worker** — the worker runs geometry only, offline.
- **Single app instance** — in-memory sessions + rate limits assume one app
  container (sticky/one-node); horizontal scale would need a shared store (Redis).
- **BYOK key stored plaintext** — accepted decision (as in playground), mitigated
  by DB file access control.
