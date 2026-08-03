# SPEC22 — Browser-agent enablement (PAT + DOM automation contract)

Status: **DESIGN** · 2026-08-03 · line: spec11→ (follows SPEC21)

## 1. Goal & framing

Make EasyCAD reliably drivable by an **AI agent that acts through the browser DOM**
— Playwright, Claude-in-Chrome / Claude Code Browser Integration, or any
"acts-like-a-user" automation. The agent must be able to do the same jobs a human
does: **create a model, ask/iterate, export STL** — without a human in the loop.

Explicitly **not** an API-first / one-shot design. We are optimising the *DOM* as the
integration surface. No MCP server. The two gaps that block a browser agent today are:

1. **Auth is agent-hostile.** The only sign-in is a magic-link emailed to the user
   (`/api/auth/login` → email → `/api/auth/callback`). An agent cannot read email, so
   it can never reach any signed-in / paid action. → **Personal Access Tokens.**
2. **The DOM has no machine-readable "am I done?" signal.** `busy`/`error` live in the
   store (`frontend/src/store.ts`) and only surface as `disabled` attributes and text.
   An agent has to guess when a generation finished. → **explicit `data-state`.**

### Non-goals

- No stateless `POST /api/generate` one-shot endpoint (dropped by decision).
- No MCP.
- No change to the CAD generation pipeline, sessions model, or trial gating.
- PATs are for **existing accounts**; no new signup path.

## 2. Personal Access Tokens (PAT)

### 2.1 Model — a `tokens` table

Magic-link JWTs are stateless by design (`app/db.py` header notes "no token table").
PATs are different: they are long-lived and **must be revocable**, so we add one table.

```sql
CREATE TABLE IF NOT EXISTS tokens (
  id         INTEGER PRIMARY KEY,
  user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  name       TEXT NOT NULL,            -- human label, e.g. "playwright-ci"
  hash       TEXT NOT NULL UNIQUE,     -- sha256 of the secret; raw token never stored.
                                       -- UNIQUE indexes the exchange lookup (by hash) —
                                       -- else it is a full scan as the table grows.
  created_at INTEGER NOT NULL,
  expires_at INTEGER NOT NULL,         -- created_at + 30 days
  revoked_at INTEGER                   -- NULL = active
);
CREATE INDEX IF NOT EXISTS idx_tokens_user ON tokens(user_id);
```

- **`PRAGMA foreign_keys=ON` on every connection** (`app/db.py` does not set it today) —
  otherwise `ON DELETE CASCADE` is silently ignored and deleting an account leaves
  orphan tokens. Test that account deletion actually removes the rows.
- `ON DELETE CASCADE` then piggybacks on the existing `delete_user` flow → deleting an
  account kills its tokens.
- No `last_used` column — it was speculative (a future audit view). Add it when there is
  a real consumer, not before.
- Store only `sha256(secret)`. Verification hashes the presented secret and looks it up
  with `SELECT ... WHERE hash = ?`. **No application-level constant-time compare** — the
  32-byte random secret has no low-entropy prefix to leak, and the equality is the indexed
  lookup itself (review Spec#5). We never need to reverse it.

### 2.2 Token format

`pat_<base64url(32 random bytes)>` — prefix makes it greppable/leak-scannable and lets
the backend reject obviously-wrong shapes before a DB hit. The `pat_` prefix is **not**
secret; the 32 bytes are.

### 2.3 Endpoints

| Method & path                    | Auth          | Purpose                                            |
|----------------------------------|---------------|----------------------------------------------------|
| `POST /api/tokens`               | signed-in     | Create a PAT. Body `{name}`. **Returns the raw secret once** (`{id, name, token, created_at}`); never retrievable again. **Bounded, atomically:** a single transaction `create_token_if_under_limit` (count active + insert in one critical section) rejects with `429` past **10 active (non-revoked, non-expired) tokens per user** — separate count-then-insert races two parallel requests past the cap (review Standards#3). Also rate-limit per-user via `limiter`. |
| `GET  /api/tokens`               | signed-in     | List the caller's tokens (id, name, created_at, expires_at, revoked_at) — **no secrets, no `last_used`** (column dropped). |
| `DELETE /api/tokens/{id}`        | signed-in + **owner** | Revoke: `UPDATE tokens SET revoked_at=? WHERE id=? AND user_id=? AND revoked_at IS NULL`. **Must scope by `user_id`** — without it, sequential ids let user B revoke user A's token (IDOR). Missing/foreign/already-revoked row → safe 404/no-op. Test cross-user access. |
| `POST /api/auth/token`           | none          | Body `{token}`. Validates PAT → issues the **same `auth` cookie** a magic-link callback issues, then returns `{ok:true, email}`. |

`POST /api/auth/token` is the crux: it converts a PAT into the existing session cookie,
so **every downstream route stays unchanged** — `current_session` (`app/main.py:454`)
already resolves identity from the `auth` cookie. No route needs a new auth path; PAT
support is entirely contained in this one exchange endpoint. (There is no `current_user`
dependency today — an earlier draft named one that does not exist.)

**Session resolution must verify the user still exists** (review Standards#3 / Spec#3).
`current_session` currently trusts the JWT `user_id` claim blindly:
`session.user_id = int(payload["user_id"])`. After an account is deleted, any still-valid
cookie — magic-link **or** PAT — keeps that session "signed in" until the JWT expires, and
can e.g. keep drawing user trial; `tokens` rows cascade-delete but the *session* does not.
Fix: in `current_session`, treat a `user_id` with no `db.get_user(user_id)` row as
**anonymous** (`session.user_id = None`). **`_maybe_refresh_auth` must do the same check**
(review Spec#1): it runs later in the request and today re-signs *any* JWT valid by claims
alone, so a deleted user's cookie would keep rolling to a fresh 1-year expiry even after
`current_session` anonymised the request. Make `_maybe_refresh_auth` skip refresh (and
ideally `delete_cookie`) when `db.get_user()` returns nothing. One shared existence check
covers both magic-link and PAT cookies. Test: delete the account in one browser context,
confirm a second context's cookie is anonymous **and not refreshed** afterward.
(Pre-existing gap, but PAT widens the session surface, so fix it here.)

Cookie issuance is **shared, not duplicated**: extract one `issue_auth_cookie(resp,
user_id, email, max_age, pat=False)` helper and call it from both `auth_callback` and
`/api/auth/token` (today `auth_callback` inlines `jwt_utils.sign(...)` + `set_cookie`).
One place defines the cookie contract. The **max-age differs by caller**: magic-link uses
`COOKIE_MAX_AGE`; `/api/auth/token` uses the short `PAT_COOKIE_MAX_AGE` (12h) so PAT
revocation/expiry takes effect within that window without any per-request check (§6.1).

**PAT cookies must carry a provenance marker and must not be refreshed** (review Spec#1).
Today `_maybe_refresh_auth` (`app/main.py:212`) re-signs *any* auth cookie with a fresh
`COOKIE_MAX_AGE` (1-year rolling session) after `AUTH_REFRESH_AFTER`. That would silently
promote a 12h PAT cookie to a 1-year one on the agent's first busy minute — destroying the
whole revocation bound. Fix: stamp `pat: true` into the PAT cookie's JWT payload, and make
`_maybe_refresh_auth` **return early when `payload.get("pat")`**. The 12h window is then a
hard cap from issuance; the agent re-bootstraps. Add a test asserting a PAT cookie is
never refreshed and expires at ≤12h.

### 2.4 Security

- **Hash at rest** (`sha256`), indexed `WHERE hash=?` lookup, `pat_` prefix pre-filter.
  No app-level constant-time compare (§2.1).
- **Rate-limit** `POST /api/auth/token` per-IP (reuse `limiter`, mirror the login caps)
  so a leaked-prefix guessing attack is bounded; `POST /api/tokens` is bounded too (§2.3).
- **Revocation** is checked at each exchange (`revoked_at IS NULL`); an already-issued PAT
  cookie stops at ≤12h (§6.1), and a deleted owner is anonymised at session resolution
  (§2.3).
- **Expiry: 30 days** (`expires_at = created_at + 30d`). Exchange rejects expired
  tokens; a human re-mints in the Account panel. No refresh/rotation flow — KISS.
- Creating a PAT requires an already-authenticated session → the one-time human
  magic-link login is the root of trust.

## 3. Agent auth flow — sessionStorage bootstrap (chosen)

**The token never appears in the URL.** A URL `?token=` transits the document request
(proxy/access logs) and can leak into the `Referer` of the first sub-resources *before*
any JS or `<meta>` runs — `replaceState` cannot undo that. Rejected.

Instead the agent seeds the token into `sessionStorage` **before navigation**, and the
SPA consumes-and-deletes it on boot:

```
1. agent seeds the token into the SPA origin's sessionStorage. Two supported shapes:
   (A) origin-bound document-start init script (preferred). MUST guard origin + top frame,
       because Playwright runs an init script on EVERY navigation and in EVERY (incl.
       cross-origin) child frame — an unguarded write would seed the PAT into third-party
       iframes' storage and re-seed on reload (review Spec#1). The app origin is passed as
       an ARGUMENT, not hard-coded — a literal 'https://APP_ORIGIN' never matches in
       dev/staging and the PAT would silently never be written (review Spec#2):
       page.addInitScript(({ token, appOrigin }) => {         // both passed as args
         if (window.top !== window) return                    // top frame only
         if (location.origin !== appOrigin) return            // exact app origin only
         if (sessionStorage.getItem('easycad_pat_used')) return  // single-use marker (see below)
         sessionStorage.setItem('easycad_pat_used', '1')      // mark BEFORE writing — survives a failed exchange
         sessionStorage.setItem('easycad_pat', token)
       }, { token: PAT, appOrigin: new URL(baseURL).origin })
       Claude-in-Chrome — an equivalent origin-guarded document-start injection.
   (B) if the agent has no document-start capability, the two-step fallback:
       goto /app  →  in-page sessionStorage.setItem('easycad_pat', PAT)  →  reload
2. goto /app  (or reload, in shape B)
3. main.tsx, before first render — read, DELETE, exchange (one-shot; see the code below).
4. SPA renders authenticated; agent proceeds via normal DOM.
```

**Genuine single-use via a sessionStorage marker set at seed time** (review Standards#1,
#4, Spec#1). The marker must be written **before** the PAT and **regardless of exchange
outcome** — setting it only after a *successful* `authWithToken` (an earlier draft) leaves
no marker on a 401/network/timeout, so the next reload re-seeds the raw PAT. So the init
script sets `easycad_pat_used='1'` immediately before `easycad_pat` (line above), and
`main.tsx` still deletes `easycad_pat` before exchanging. Then, for any outcome, a `reload`
finds the marker and does **not** re-seed → the secret is written to storage exactly once
and exchanged at most once. **Retrying after a failed exchange requires a new PAT in a new
context/tab** (fresh `sessionStorage`) — you never re-seed the same secret. The reload E2E
asserts **no second `/api/auth/token` fires**, including after a *failed* first exchange; a
cross-origin iframe E2E asserts the PAT is never written there.

### 3.0 Precondition: a fresh, isolated BrowserContext (mandatory)

PAT bootstrap **requires a clean `BrowserContext`** (Playwright `browser.newContext()`) or
equivalent fresh tab/profile. This is a hard contract precondition, not a nicety, because a
*reused* context carries prior state that breaks identity isolation on several axes (review
Standards#2, Spec#1):

- a leftover `auth_token` cookie → a failed PAT exchange could leave the agent signed in as
  the previous user;
- a leftover `easycad_session` cookie + its **in-memory registry CAD session** → user B's
  successful bootstrap would inherit user A's steps/settings (the server keys CAD state on
  `easycad_session`, which `logout` does **not** clear);
- an abort-race where the exchange response (with cookie) already reached the browser but
  `fetch` then rejected on timeout.

A fresh context has none of these — no cookies, no session — so a single mandate closes all
three. We do **not** try to support reused contexts by server-side session rotation: rotating
`easycad_session` on every `/api/auth/token` would wipe an agent's in-progress CAD work on
each ≤12h re-bootstrap. The `logout`-on-failure in the boot code below is retained only as
**best-effort** defence-in-depth; the *guarantee* is the fresh context. A two-user E2E (B
bootstraps in a context previously used by A) asserts B sees no A steps/settings and is not
signed in as A.

**Why an init script, not "plain JS before goto" (review Spec#2):** `sessionStorage` is
origin-scoped. Running JS in `about:blank` cannot write the *future* origin's storage —
the write lands on the blank origin and is lost. So the seed must run either as a
**document-start init script bound to the target origin** (shape A) or **after** the
origin is loaded (shape B: goto → set → reload). Both are documented; there is no
"set storage in a neutral tab then navigate" path.

**addInitScript passes the token as an argument** (review Standards#4). `addInitScript`
serialises the function and ships it to a fresh context — a captured `TOKEN` closure
variable is not in scope there. Pass it via the second arg (`addInitScript(fn, PAT)`), as
shown.

No secret in URL, history, `Referer`, or any visible/hidden DOM element. We do **not**
add a hidden DOM input for the token — it hurts accessibility and is only
security-through-obscurity.

**Bootstrap must never break the start — any failure, not just 401** (review Standards#1,
Spec#2, #5). The exchange is wrapped so the SPA renders on every path, and is **time-bounded**
so a hung request can't stall `renderSPA()` forever (`fetch` in `api.ts` has no timeout
today). The marker is owned by the init script (set at seed time, §3 above), so the boot code
does not touch it:
```ts
const t = sessionStorage.getItem('easycad_pat')
if (t) {
  sessionStorage.removeItem('easycad_pat')       // clear BEFORE the call — never retry the secret
  try {
    await api.authWithToken(t, { timeoutMs: 5000 })  // AbortSignal.timeout — bounds a hung request
  } catch {
    rootAuthError = 'invalid-token'              // 401 / network / 5xx / malformed / timeout — all here
    api.logout({ timeoutMs: 5000 }).catch(() => {})  // best-effort cleanup; not awaited (see below)
  }
}
renderSPA(rootAuthError)                          // always runs; see delivery in §3.2
```
- Key removed **before** the request, so a thrown or hung call can't leave a retry of the
  secret.
- **`authWithToken` is time-bounded** (`AbortSignal.timeout(5000)`) so `renderSPA()` always
  runs even on a hung network (review Standards#1). The `logout` is **not awaited** — it is
  best-effort cleanup, so a hung logout can never re-block the render (review Standards#1).
- **The isolation guarantee is the fresh context (§3.0), not `logout`.** In a fresh context
  there is no stale `auth_token`/`easycad_session` to clear, so identity mixing cannot occur.
  `logout` is retained only as defence-in-depth if the precondition is violated; because a
  fetch can reject *after* its response (with cookie) already reached the browser, a
  best-effort logout is not a guarantee — hence the hard fresh-context precondition (review
  Standards#2, Spec#1). Do **not** claim "always anonymous on failure" for reused contexts.
- On failure the SPA renders on the **free trial** (works without auth) and exposes a single
  neutral `data-auth-error="invalid-token"` on the root + a dismissible banner — no
  distinction of cause, **no token echoed anywhere**. The agent detects it and re-mints in a
  fresh context.

**Contract limitation (explicit):** the agent must be able to seed origin-scoped storage
via shape A or B. Every automation we target (Playwright, Claude-in-Chrome) can. If some
future agent can do neither, that is a documented contract limitation — **not** a reason
to reintroduce a URL token.

Frontend touch-points: `frontend/src/main.tsx` (boot exchange) + `frontend/src/api.ts`
(`authWithToken`, bounded `logout`) + `frontend/src/app.tsx` (receive the auth-error — §3.2).

### 3.1 Token generation UI (for the human)

Extend the **Account** panel (`frontend/src/components/Account.tsx`) with a "Access
tokens" section, only when signed in:

- exact ids (no wildcard — review Spec#3): input `#account-token-name`, button
  `#account-token-create`, one-time copyable field `#account-token-value`, copy button
  `#account-token-copy`.
- list of existing tokens, each with a revoke button `#token-revoke-{id}` (prefix selector
  `[id^="token-revoke-"]`).

This is human-facing (an agent never logs in here), so plain UX; but give it stable ids
so an *orchestrating* agent could also mint tokens if a human bootstrapped it once.

### 3.2 Delivering the auth-error to the root App (review Spec#2)

The root element that carries `data-state`/`data-auth-error` is owned by `App` in
`app.tsx`, but `main.tsx` today renders `<App />` with no props — so a `rootAuthError`
computed in the boot code has no channel to reach it. Specify the channel explicitly:
`main.tsx` passes it down — `render(<App authError={rootAuthError} />)` — and `App`
renders `data-auth-error={authError}` on the root plus a dismissible banner
(`#auth-error-banner` / `#auth-error-dismiss`). (A tiny boot-store field read by `App` is an
equivalent channel; pick one.) This adds `app.tsx` to the bootstrap scope — reflected in §5
— and the UI path is covered by the browser E2E (bad PAT → banner shows, root attribute set,
no token in DOM).

## 4. DOM automation contract

The heart of SPEC22: a **documented, test-guarded** set of selectors + a state machine
so an agent drives the app deterministically. Foundation already exists — stable
`id`/`data-testid` across components and `frontend/src/test-selectors.test.ts` guarding
them. We add the missing state signal and codify the contract.

### 4.1 Machine-readable state (the key change)

Add to the app root (`app.tsx` top-level container) a `data-state` attribute derived by
**one pure function** of the full automation input — no `lastStepOk` store field
(compute from what already exists). The function is what the unit tests exercise.

The input fields are the **actual store fields** (`frontend/src/store.ts`), not invented
ones — an earlier draft guessed these wrong:

```ts
type AutomationInput = {
  busy: boolean                  // any in-flight op (gen, manual execute, reset/import/revert, variation commit)
  error: string | null           // red/hard error
  notice: Notice | null          // operational failure — server_busy / timeout / trial (has .code)
  pending: Pending | null        // clarify questions fork
  proposal: Proposal | null      // confirm-refine fork
  invalidNotice: InvalidNotice | null  // invalid-prompt fork
  variations: Variations | null  // variation-pick fork
  steps: Step[]
  currentId: number | null
}
function automationState(i: AutomationInput):
  'idle' | 'generating' | 'awaiting-input' | 'done' | 'error'
```

**State priority (first match wins):**

| # | condition                                                        | data-state       |
|---|------------------------------------------------------------------|------------------|
| 1 | `busy`                                                           | `generating`     |
| 2 | `pending \|\| proposal \|\| invalidNotice \|\| variations`       | `awaiting-input` |
| 3 | `error \|\| notice`                                              | `error`          |
| 4 | a **non-initial** current step exists and succeeded             | `done`           |
| 5 | otherwise (initial / after reset)                               | `idle`           |

**#1 — `generating` = any `busy`, not `busyKind==='gen'`** (review Standards#1). Manual
execute, reset/import/revert and variation-commit set `busy=true` with `busyKind=null`;
gating on `'gen'` alone would let the agent act mid-operation. `busyKind` stays for the
progress UI; the automation contract keys off `busy`.

**#2 — `awaiting-input` uses the real fields** (review Standards#1). The chat forks are
four **distinct** store fields: `pending` = clarify questions, `proposal` = confirm-refine
(these are *not* the same field — the earlier draft collapsed them and confirm-refine
would have surfaced as `done`/`idle`), `invalidNotice`, `variations`. The agent answers
via ids `clarify-*` / `proposal-*` / `invalid-*` / `variation-*`.

**#3 — operational failures count as `error`** (review Standards#3). `reportError`
(`store.ts:171`) routes `server_busy`, execution timeout and trial errors into **`notice`**
while leaving `error === null`. If the automaton looked only at `error`, a failed last
turn on top of a prior good model would read `done`. So `notice` (any code) → `error`
state. `data-error-code` (from `notice.code`/`error`) is exposed alongside so the agent can
branch on `server_busy` (retryable) vs hard errors.

**Every state-changing action must clear `notice` at start** (review Standards#2). Today
only `sendChat`/`sendVariations` reset `notice` (`store.ts:192,435`); `reset`, `runManual`,
`revert`, `importProject`, init and account ops set `error: null` but leave a stale
`notice`. Consequence: `server_busy` → a **successful** `reset` would still read
`data-state="error"` with the old `data-error-code` instead of `idle`. Fix: clear **both**
`error` and `notice` at the start of every mutating store action (a shared
`beginAction()` helper is the clean way). Regression-test: soft notice, then a successful
reset/manual/revert ends in `idle`/`done`, not `error`.

**#4/#5 — `idle` must stay reachable** (review Standards#4). Init and reset create a
successful **`initial`** step (`kind === 'initial'`), so a naive "current step succeeded"
returns `done` and `idle` is dead. Rule #4 therefore excludes the initial step: `done`
requires a *non-initial* successful current step; a fresh/reset session with only the
`initial` step is `idle`.

```
data-state-rev = <store counter, +1 per agent-triggered mutating action>   // NEW — see below
data-error-code = <notice.code | 'error' | absent>
aria-busy       = "true" while generating
```

**`data-state-rev` is an action-scoped store counter, NOT derived from observed
`data-state` changes** (review Standards#2/#3, and the follow-up). Deriving it from "did the
`data-state` *value* change between two commits" is fragile: if a fast action coalesces as
`done → generating → done` in a single DOM commit, the render only ever sees the same final
`done`, the counter never moves, and an agent waiting for `rev > old` **hangs forever** — the
very fast-completion case the spec requires. Root cause: that scheme couples "the action
registered" to "an intermediate frame painted," a rendering-timing assumption, not a logical
invariant.

Fix: bump a real store field in `beginAction()` — the same shared entry point that already
clears `error`/`notice` — so `actionRev` increments **once per mutating action the agent
triggers**, regardless of how the intermediate states paint. Render it as `data-state-rev`
on the root alongside the settled `data-state`. The counter changes because a new action
*happened*, so coalescing is irrelevant.

**Race-free waiting.** Agents read `data-state-rev` before submitting, then wait until it
increased *and* `data-state ∈ {done, error, awaiting-input}` (a settled state). Because the
counter advances at action start — not on an observed frame — the wait resolves even when
`generating` never paints. A fast-completion E2E (a turn that resolves almost immediately)
asserts this. Unit-test the DOM attribute value after each action, not just the counter.

Complement with per-step status on timeline nodes: `data-status="ok|error"` on
`#timeline-step-{id}` (currently only `disabled` reflects busy).

### 4.2 Canonical selectors (already present unless marked NEW)

| Job                    | Selector                                  |
|------------------------|-------------------------------------------|
| Prompt input           | `[data-testid=chat-prompt]` — textarea today has only `name`+`data-testid`, **no id**. Add `id="chat-prompt"` **NEW** so the id works too; the guard test verifies the id, not just the testid. |
| Send                   | `#chat-send`                              |
| App state root         | `[data-state]`, `[data-state-rev]`, `[data-error-code]`, `[data-auth-error]` **NEW** |
| Awaiting-input forks    | confirm-refine `#proposal-use`/`#proposal-cancel`; invalid `#invalid-generate`/`#invalid-cancel`; variations actions `#variation-commit`/`#variation-cancel`, option cards `[id^="variation-option-"]`; clarify options `[id^="clarify-"]`. **`*` is shorthand — the contract publishes exact ids and `[id^=…]` prefixes (`#clarify-*` is not valid CSS).** |
| Current model download menu | `#viewer-download`                   |
| Export STL             | `#export-stl`                             |
| Export STEP / source   | `#export-step` / `#export-source`         |
| New model              | `#project-new`                            |
| Timeline step          | `#timeline-step-{id}` (+ `data-status` **NEW**) |
| Account / token entry  | `#account-toggle`; tokens `#account-token-name`/`-create`/`-value`/`-copy`, revoke `[id^="token-revoke-"]` **NEW** (exact ids, no `*` wildcard) |

Export already carries stable ids (`Viewer.tsx`) — an agent clicks `#viewer-download`
then `#export-stl` and captures the browser download (`api.exportUrl(currentId)` →
`GET /api/export/{step_id}`, unchanged).

**Variation option ids must be disambiguated** (review Standards#2). `Chat.tsx` today gives
option cards `id={`variation-${i}`}` while the actions are `#variation-commit` /
`#variation-cancel` — so `[id^="variation-"]` would also select commit/cancel and an agent
could commit/cancel instead of picking. Rename the option cards to
`id={`variation-option-${i}`}` so `[id^="variation-option-"]` selects only cards; update the
guard and E2E.

### 4.3 Remove the native `confirm()`

`Account.tsx:110` uses `confirm(t('account.deleteConfirm'))` for account deletion.
Native `confirm/alert/prompt` **freeze browser-automation agents** (the dialog blocks
all further events). Replace with an in-DOM confirm (`#account-delete-confirm` /
`#account-delete-cancel`). Audit the tree to ensure no other native dialogs remain.

### 4.4 Contract doc + test guard

- **`docs/automation.md`** — the public contract: bootstrap flow, the selector table,
  the `data-state` machine, and a worked agent recipe (create → ask → export STL).
  This is the "how to drive this app" doc, the DOM-world analogue of an OpenAPI spec.
- **Selector presence** — extend `frontend/src/test-selectors.test.ts` to assert every
  selector in the table exists. But a source/regex scan proves markup exists, **not** that
  the state machine transitions correctly.
- **State machine (unit)** — test the pure `automationState(...)` function directly across
  the documented conditions (idle → generating → done/error/awaiting-input, rev bumps).
- **Browser E2E (acceptance)** — a real headless run is the only thing that proves the
  PAT→cookie exchange, cookie/session, `data-state`/`data-state-rev` transitions and STL
  download actually work end-to-end. Source tests are necessary but not sufficient. Cover
  the review's edge cases: **reload** (marker prevents a second `/api/auth/token` request,
  *including after a failed first exchange*), **hung network/logout** (bounded/non-awaited,
  render still happens), **fast-completion turn** (rev-based wait still resolves),
  **two-user reused-vs-fresh context** (fresh context → B sees no A steps/settings, not
  signed in as A), **cross-origin iframe**
  (init script does not seed it), **reused context with a stale `auth_token`** (failed PAT
  exchange logs out, not signed-in-as-wrong-user), and **deleted account** (a second
  context's cookie is anonymous). Backend tests: cross-user revoke (IDOR), PAT-cookie never
  refreshed / ≤12h, deleted-owner cookie anonymised, create-cap `429`.

## 5. Rollout / touch-points

| Area        | Files                                                        |
|-------------|-------------------------------------------------------------|
| PAT storage | `app/db.py` (tokens table `UNIQUE(hash)` + CRUD, **atomic `create_token_if_under_limit`**, `PRAGMA foreign_keys=ON`) |
| PAT routes + cookie | `app/main.py` (`/api/tokens*` **owner-scoped + atomic cap/rate-limit**, `/api/auth/token`, `issue_auth_cookie` helper, **`_maybe_refresh_auth` skips `pat` cookies**) |
| Session hardening | `app/main.py` — **`current_session` AND `_maybe_refresh_auth` anonymise/skip a `user_id` with no `db.get_user()` row** (deleted-account cookie, no rolling refresh) |
| Boot exchange | `frontend/src/main.tsx` (seed read+delete, bounded exchange, best-effort logout, pass `authError` down), `frontend/src/api.ts` (`authWithToken` + `logout` with `AbortSignal.timeout`), **`frontend/src/app.tsx`** (`authError` prop → `data-auth-error` + `#auth-error-banner`) |
| Init-script contract | agent-side: origin+top-frame guard, `easycad_pat_used` marker set **at seed time**, appOrigin passed as arg; **fresh `BrowserContext` a hard precondition** (§3.0) |
| Token UI    | `frontend/src/components/Account.tsx`                       |
| State signal | `frontend/src/app.tsx` (pure `automationState` → `data-state`, render `data-state-rev` from store), `frontend/src/store.ts` (**`beginAction()` clears `error`/`notice` + bumps `actionRev`**), `Chat.tsx` (**add `id="chat-prompt"`, rename options → `variation-option-{i}`**), `Timeline.tsx` (`data-status`) |
| Kill confirm | `frontend/src/components/Account.tsx`                      |
| Contract    | `docs/automation.md`, `frontend/src/test-selectors.test.ts`, state-machine unit test, browser E2E |

No changes to: generation pipeline, worker, sessions, trial gating, CORS (agent is
same-origin in the browser), robots.txt.

## 6. Resolved decisions

1. **PAT TTL — 30 days.** `expires_at = created_at + 30d`; exchange rejects expired
   tokens, human re-mints in the Account panel. No refresh flow.
2. **Scope/limits — inherit the owner's exactly.** An agent token is the owner acting;
   trial/budget counting is unchanged (no per-token accounting).
3. **Download capture — no fallback.** Rely on the browser's native download; Playwright
   captures it, and that is sufficient. No copyable data/URL affordance.
4. **`data-state` — KISS, single attribute, but must cover the input forks.** No second
   `data-last-action` attribute. States: `idle`, `generating`, `awaiting-input`, `done`,
   `error`, derived by one pure function + a monotonic `data-state-rev` for race-free
   waiting (§4.1). `awaiting-input` is not scope-creep — without it an agent hangs at the
   clarify/confirm-refine/invalid/variations forks the existing chat flow already
   produces. If the last action errored while an earlier good model is still current,
   state is `error`; the agent still exports the current model via the export selectors.

### 6.1 Decisions from the design review

- **Bootstrap: URL token dropped** → sessionStorage pre-navigation seed (§3).
- **Fresh `BrowserContext` is a hard precondition** (§3.0), not reused-context support. One
  mandate closes stale-`auth_token` sign-in, `easycad_session`/CAD-state mixing, and the
  fetch abort-race. Server-side session rotation on `/api/auth/token` was rejected — it would
  wipe an agent's in-progress CAD work on each ≤12h re-bootstrap.
- **PAT revocation — short-lived PAT cookie (chosen: option a).** `/api/auth/token` issues
  the `auth` cookie with a **short max-age — `PAT_COOKIE_MAX_AGE = 12h`** — instead of the
  full `COOKIE_MAX_AGE` used for magic-link sessions. Revoking (or expiring) a PAT then
  stops working within ≤12h, because the agent's cookie dies and re-bootstrapping calls
  `/api/auth/token` again, which re-checks `revoked_at IS NULL` and `expires_at`. No
  per-request DB hit, no route touches the tokens table — the whole PAT concern stays in
  the exchange endpoint. The one helper — `issue_auth_cookie(resp, user_id, email,
  max_age, pat=False)` (same signature as §2.3) — takes both the max-age and the `pat`
  provenance flag: `(COOKIE_MAX_AGE, pat=False)` for magic-link, `(PAT_COOKIE_MAX_AGE,
  pat=True)` for PAT. The `pat` flag stamps the JWT so `_maybe_refresh_auth` skips it.
  Accepted trade-off: an agent re-bootstraps every ≤12h (trivial for automation), and
  revocation lags by at most that window.

## 7. Acceptance

A Playwright script, given only a PAT, can headlessly: seed the token into
`sessionStorage` before navigation, load `/app` authenticated, submit a prompt, wait on
`data-state-rev` + `data-state` transitions (no fixed sleeps, no API calls outside the
browser), and export a valid STL. Same script logic runs under Claude-in-Chrome. Proven
by a **browser E2E** run — not source tests alone (§4.4). The unit-level state-machine
and selector-presence tests guard the contract in CI.
