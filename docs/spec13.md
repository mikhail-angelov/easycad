# SPEC13: Multi-Tenant SaaS — Ephemeral Sessions, Magic-Link Auth, Per-User BYOK

## Status

Proposed implementation specification. Builds on SPEC11 (chat builder) and
SPEC12 (isolated execution worker). Turns the single-user local app into a
multi-tenant hosted service at `easycad.bconf.com`. Does not change the
chat/step/geometry UX or the SPEC12 execution isolation.

## Goal

Make EasyCAD safe and sane to host for many users:

1. **No server-side file persistence of working state.** The CAD session
   (steps, code, geometry) lives only in memory, per user, and is evicted when
   idle. Users persist their own work via project export/import (SPEC11), which
   becomes the *only* durable store of CAD work.
2. **Per-user in-memory sessions with a sliding TTL**, so active users stay warm
   and only idle sessions are reclaimed.
3. **A client session identity** issued to every visitor (authenticated or not).
4. **Bring-your-own LLM key.** Each user supplies their own provider key, so the
   operator does not pay for generation. Authenticated users' settings persist;
   anonymous users' settings live only in their in-memory session.
5. **Passwordless auth via magic link**, mirroring the `playground` approach
   (stateless JWT link + SMTP), so no passwords are stored.

## What changes vs SPEC11/12

- **Removed:** `EASYCAD_SESSION_FILE` autosave-to-disk and resume-on-startup
  (`app/main.py` `AUTOSAVE`, `_load_session`). No working state touches disk.
- **Changed:** the single global `SessionStore` becomes a `SessionRegistry` of
  many stores keyed by session id; every `/api/*` working-state call resolves
  the caller's store from their session cookie.
- **Changed:** `app/llm.py` / `app/refiner.py` take the provider+key from the
  request's resolved settings, not from a process-global env var.
- **Kept:** `/api/project/export` and `/api/project/import` — now the user's own
  durable persistence (download/upload JSON), unchanged in format (text-only).
- **Added:** durable SQLite store for **accounts and settings only** (not CAD
  work) at `/data/easycad.db`; magic-link auth; settings endpoints; rate limits.

## Session model (in-memory, TTL)

### Identity

- On the first request without one, the server issues an opaque
  `easycad_session` cookie (random 128-bit id, httponly, SameSite=Lax, Secure).
- The in-memory working state is keyed by this session id — auth or not.
- On login, the session is linked to the user id (so their settings resolve);
  the session id itself does not change.

### Registry

`app/session_registry.py`:

```
SessionRegistry:
  sessions: dict[session_id -> Session]
Session:
  store: SessionStore            # the SPEC11 in-memory steps
  settings: SessionSettings      # anon settings live here (see BYOK)
  user_id: int | None
  last_access: float             # updated on every request
```

- **Sliding TTL:** `last_access` is refreshed on each request. A background
  asyncio sweeper (every 60s) evicts sessions idle longer than
  `EASYCAD_SESSION_TTL` (default 2h). Active users are never reclaimed.
- **Capacity cap:** at most `EASYCAD_MAX_SESSIONS` (default 500); on overflow,
  evict the least-recently-used. Protects server memory.
- Eviction drops the store; the user's work is gone unless they exported it.
  The UI must nudge users to export (see UX).

### UX consequence

- No silent resume. On load the app starts a fresh session (or the still-warm
  one for the same cookie).
- A banner reminds users that server state is temporary — **use Save project**
  to keep work. (Replaces SPEC11's autosave-resume banner idea.)

## Authentication — magic link (mirrors `playground`)

Stateless: the magic link *is* a short-lived JWT; no token table. Same shape as
`playground/src/services/authService.ts`, ported to FastAPI + PyJWT.

### Flow

1. `POST /api/auth/login { email }` — find-or-create the user by email; sign a
   magic JWT `{ user_id, email, type: "magic" }` with 15-minute expiry; email a
   link `${APP_URL}/api/auth/callback?token=<jwt>`. **Always** return
   `{ ok: true }` (never leak whether the account existed).
2. `GET /api/auth/callback?token=<jwt>` — verify signature, `type == "magic"`,
   not expired; issue a session JWT `{ user_id, email }` (30-day expiry); set it
   as httponly `auth_token` cookie; 302-redirect to `/`.
3. `POST /api/auth/logout` — clear the `auth_token` cookie.
4. `GET /api/auth/me` — from the cookie, return `{ authenticated, email?,
   has_key }` (never the key itself).
5. `DELETE /api/auth/me` — delete the account and its settings from the DB, drop
   any in-memory session, and clear the `auth_token` cookie (GDPR hygiene).

The magic link is **stateless and reusable until it expires** (15 min), matching
playground — no `jti`/single-use tracking.

### Email

`app/mail.py`, same transport as `playground/src/services/mailService.ts`:
SMTP over STARTTLS to Yandex postbox — `POST_SERVICE_URL` host, port 587,
`POST_USER` / `POST_PASS`, and — for now — the **same sender as playground**,
`From: no-reply@js2go.ru` (`MAIL_FROM`), reusing playground's already
SPF/DKIM-verified domain. Python `smtplib` (stdlib) or `aiosmtplib`. (The magic
link URL still points at `APP_URL` = `https://easycad.bconf.com`.)

### JWT

`app/jwt_utils.py`: `sign(payload, ttl)` / `verify(token)` using `JWT_SECRET`
(HS256). Two token types: `magic` (link) and session (cookie).

## Settings & BYOK key

Per-user settings: `{ provider, model, key }` (the LLM API key + provider/model
override — the SPEC11 provider dropdown, now per user).

### Storage

- **Authenticated:** persisted in SQLite `users.settings` as **plaintext JSON**,
  matching `playground`'s `users.api` (decision confirmed). Mitigation is
  restricting the DB file (`/data/easycad.db`, app-only, not web-served) and not
  logging it. Only the app process reads it; the key never reaches the worker.
- **Anonymous:** held only in `Session.settings` in memory; never written to
  disk; lost on TTL/eviction.

### Endpoints

- `GET /api/settings` — resolved settings for the caller: `{ provider, model,
  has_key }`. Never returns the key.
- `PUT /api/settings { provider?, model?, key? }` — authed: persist to DB
  (encrypted); anonymous: store in the in-memory session.

### Key resolution (per generation request)

Order: **session settings** (anonymous key) → **user DB settings** (authed key)
→ **server env fallback** (`DEEP_SEEK_KEY`, local/dev only; disabled in SaaS via
`EASYCAD_REQUIRE_USER_KEY=1`). The resolved key is passed into `generate_code` /
`triage` as an argument. It is **never** passed to the worker (SPEC12): the
worker runs geometry only and has no network.

If no key resolves and `EASYCAD_REQUIRE_USER_KEY=1`, `/api/chat` returns a
clear "add your LLM key in settings" error, not a 500.

## Data model (SQLite, durable — accounts only)

`/data/easycad.db`:

```sql
CREATE TABLE users (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  email      TEXT NOT NULL UNIQUE,
  settings   TEXT,                       -- JSON {provider, model, key(enc)}
  created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
```

Magic-link tokens are stateless JWTs — no table (matches playground). CAD
sessions are **not** stored here (they are in-memory only).

## Abuse & rate limiting

Even with BYOK (users spend their own LLM tokens), shared worker CPU and email
are operator costs. In-memory limiters (single app instance):

- `/api/auth/login` — per email + per IP (e.g. 5/hour) to prevent email bombing.
- `/api/chat`, `/api/execute`, `/api/variations` — per session (e.g. 30/min)
  and a global concurrency ceiling feeding the SPEC12 worker semaphore.
- Request body size cap on code/prompt payloads.
- `EASYCAD_MAX_SESSIONS` LRU cap (above) bounds memory.

## Security

- Cookies: `auth_token` and `easycad_session` are httponly, `SameSite=Lax`,
  `Secure`. Redoproxy terminates TLS (`easycad.bconf.com`).
- `JWT_SECRET` and `SETTINGS_KEY` are server secrets in `.env`, never shipped to
  the client or the worker.
- BYOK key at rest: plaintext in `/data/easycad.db` (confirmed decision, as in
  playground); protected by DB file permissions; never logged, never returned by
  any endpoint, never placed in the worker env.
- Magic link: 15-minute expiry; login never reveals account existence.
- Worker isolation from SPEC12 is unchanged and reaffirmed: no key, no network.

## Config / env additions

```
APP_URL=https://easycad.bconf.com
JWT_SECRET=...                # HS256 signing secret
MAIL_FROM=no-reply@js2go.ru   # reuse playground's verified sender for now
POST_SERVICE_URL=postbox.cloud.yandex.net
POST_USER=...                 # same Yandex postbox creds as playground
POST_PASS=...
EASYCAD_DB_PATH=/data/easycad.db
EASYCAD_SESSION_TTL=7200      # seconds; sliding idle expiry
EASYCAD_MAX_SESSIONS=500
EASYCAD_REQUIRE_USER_KEY=1    # SaaS: no server-key fallback
```

The compose `app` service mounts `/data` (already present in SPEC12) for the
SQLite accounts DB. The worker is unaffected.

## Migration from SPEC11/12

- Delete `AUTOSAVE`/resume from `app/main.py`; keep export/import.
- Existing local `~/.easycad/session.json` is simply ignored (local dev can
  still export/import). No migration of CAD work is needed — it was never a
  durable product artifact.

## Acceptance criteria

1. Two browsers (two cookies) get two independent CAD sessions; neither sees the
   other's steps.
2. No working-state file is written on the server for any flow; only
   `/data/easycad.db` (accounts) and user-initiated project export exist.
3. A session idle past `EASYCAD_SESSION_TTL` is evicted; an active session making
   requests is never evicted.
4. `POST /api/auth/login` emails a working magic link; the callback sets a
   session cookie and authenticates; `GET /api/auth/me` reflects it.
5. An authenticated user's `PUT /api/settings { key }` persists across logout/
   login; the key is never returned by any endpoint (GET /api/settings exposes
   only `has_key`).
6. An anonymous user's key works for their session and is gone after eviction/
   logout; it is never written to disk.
7. With `EASYCAD_REQUIRE_USER_KEY=1` and no key set, `/api/chat` returns a clear
   "add your key" error, and no generation runs.
8. Generation uses the caller's resolved key; the worker environment never
   contains any LLM key.
9. Login rate limiting blocks email bombing; per-session chat rate limiting caps
   worker load.
10. `DELETE /api/auth/me` removes the user row and settings, drops the session,
    and clears the cookie; a subsequent `GET /api/auth/me` is unauthenticated.

## Non-goals

- No horizontal scaling of the app tier yet — in-memory sessions assume a
  **single app instance** (or sticky routing). Multi-instance needs a shared
  store (Redis); deferred. (See Open decisions.)
- No teams/orgs, sharing, or public project gallery.
- No billing/quotas beyond rate limiting.
- No OAuth/social login — magic link only.
- No server-side storage of CAD sessions or a project database (export/import
  stays the persistence path).

## Decisions (all confirmed)

1. **Anonymous access — yes, with their own key.** Anonymous users can generate
   by entering their own LLM key, kept in session memory only (lost on TTL). No
   server-key fallback in SaaS (`EASYCAD_REQUIRE_USER_KEY=1`).
2. **BYOK key at rest — plaintext**, as in playground (see Settings/Storage).
3. **Magic link — stateless**, reusable until 15-min expiry, like playground; no
   single-use tracking.
4. **Single app instance** for now — in-memory sessions and rate limits assume
   one app container; no app replicas behind the proxy yet.
5. **Email sender — `no-reply@js2go.ru`** for now, reusing playground's verified
   Yandex postbox domain (SPF/DKIM already set); no new DNS needed.
6. **Account deletion — yes**, `DELETE /api/auth/me` removes the account and its
   settings.
