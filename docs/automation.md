# Driving text2part with a browser agent

This is the **public DOM automation contract** (SPEC22) — the "how to drive this
app" doc, the DOM-world analogue of an OpenAPI spec. It lets an AI agent that acts
through the browser (Playwright, Claude-in-Chrome, or any acts-like-a-user
automation) do the same jobs a human does — **create a model, iterate, export
STL** — with no human in the loop.

Two things make this possible:

1. **Personal Access Tokens (PAT)** — a headless way to reach a signed-in session
   without reading a magic-link email.
2. **A machine-readable state signal** (`data-state` + `data-state-rev`) so the
   agent knows, deterministically, when a turn has settled — no fixed sleeps.

Everything below is guarded by tests (`frontend/src/test-selectors.test.ts`,
`frontend/src/automation.test.ts`, `tests/test_spec22_pat.py`), so it stays true.

---

## 1. Get a token (one-time, human)

An agent can only *use* a PAT; a human mints it once from an existing account:

1. Sign in (magic link), open the account panel (`#account-toggle`).
2. In **Access tokens**: type a name in `#account-token-name`, click
   `#account-token-create`.
3. Copy the secret from `#account-token-value` (or `#account-token-copy`). It is
   shown **once** and never retrievable again. Format: `pat_<random>`.

Tokens live **30 days**, are **revocable** (`#token-revoke-{id}`), and up to
**10 active** per account. Revoking or expiry takes effect within **≤12 hours**
(the PAT session cookie's lifetime).

The same ids let an *orchestrating* agent mint tokens if a human bootstrapped it
once — but the normal path is: human mints, agent consumes.

**Token operations have their own completion signal — not `data-state-rev`.**
Minting/revoking a token is *not* a CAD mutation, so it deliberately does **not**
move `data-state` / `data-state-rev` (those describe the model-drive loop only —
clearing a CAD error because you minted a token would be wrong). Instead:

- **Create** — after clicking `#account-token-create`, wait for the secret to
  appear in `#account-token-value`; on failure (e.g. the 10-token cap) a
  machine-readable `#account-token-error` appears instead. Wait for **one of the
  two**.
- **Revoke** — after clicking `#token-revoke-{id}`, the row's revoke button
  disappears (the token is now inactive); on failure `#account-token-error`
  appears.

## 2. Bootstrap a session (agent, headless)

**The token never appears in the URL** (it would leak via proxy logs / `Referer`).
The agent seeds it into the app origin's `sessionStorage` **before navigation**;
the SPA consumes-and-deletes it on boot and exchanges it for the session cookie.

### Precondition: a fresh, isolated BrowserContext (mandatory)

PAT bootstrap **requires a clean `BrowserContext`** (`browser.newContext()`) or an
equivalent fresh tab/profile. This is a hard contract precondition — a *reused*
context carries prior cookies / CAD session that would break identity isolation.
Do not reuse a context across users.

### Shape A — document-start init script (preferred)

```js
await context.addInitScript(({ token, appOrigin }) => {   // BOTH passed as args
  if (window.top !== window) return                        // top frame only
  if (location.origin !== appOrigin) return                // exact app origin only
  if (sessionStorage.getItem('easycad_pat_used')) return   // single-use marker
  sessionStorage.setItem('easycad_pat_used', '1')          // set BEFORE the token
  sessionStorage.setItem('easycad_pat', token)             // — survives a failed exchange
}, { token: PAT, appOrigin: new URL(baseURL).origin })

await page.goto(baseURL + '/app')
```

Guards matter: an init script runs on **every** navigation and in **every** child
frame (incl. cross-origin) — without the top-frame + origin guards it would seed
the PAT into third-party iframes and re-seed on reload. `appOrigin` is passed as
an **argument**, never hard-coded (a literal would silently never match in
dev/staging). The `easycad_pat_used` marker is set **before** the token and
**regardless of exchange outcome**, so a reload never re-seeds the secret.
Retrying after a failed exchange requires a **new PAT in a new context**.

### Shape B — two-step fallback (no document-start capability)

```
goto /app  →  page.evaluate(() => sessionStorage.setItem('easycad_pat', PAT))  →  reload
```

`sessionStorage` is origin-scoped, so the seed must run either as a document-start
script bound to the target origin (A) or *after* the origin is loaded (B). There
is no "set storage in a neutral tab then navigate" path.

### What the SPA does on boot (`main.tsx`)

Reads `easycad_pat`, **deletes it before** the call, exchanges it with a 5 s
timeout, then always renders. Any failure (401 / network / 5xx / timeout) renders
the SPA on the **free trial** and sets `data-auth-error="invalid-token"` on the
root plus a dismissible banner (`#auth-error-banner` / `#auth-error-dismiss`). No
token is ever echoed into the DOM.

## 3. The state machine

The app root (`[data-state]`) carries a single derived state:

| `data-state`     | meaning                                                       |
|------------------|---------------------------------------------------------------|
| `idle`           | fresh / just reset — ready for a first prompt                 |
| `generating`     | an operation is in flight (`aria-busy="true"`)                |
| `awaiting-input` | a chat fork needs an answer (clarify / confirm / invalid / variations) |
| `done`           | a non-initial step succeeded and is current                   |
| `error`          | last action failed (hard error **or** operational notice)     |

Priority is first-match-wins in exactly that order. Companion attributes:

- `data-state-rev` — a monotonic counter bumped **once per mutating action**. It
  advances at action *start*, not on a painted frame, so a fast turn that never
  paints `generating` still moves it.
- `data-error-code` — present only in `error`: the notice code (e.g.
  `server_busy`, retryable) or `error` (hard). Branch on it.
- `data-auth-error` — present only after a failed PAT bootstrap.
- Per-step `#timeline-step-{id}` carries `data-status="ok|error"`.

### Race-free waiting (the important part)

```
rev  = read [data-state-rev]
click #chat-send      (or answer a fork, revert, reset, …)
wait until [data-state-rev] > rev
     AND  [data-state] ∈ { done, error, awaiting-input }
```

Never use fixed sleeps. Because `rev` advances at action start, the wait resolves
even for an instantaneous turn. On `error` with `data-error-code="server_busy"`,
retry; on a hard error, read the message / stop.

## 4. Selector reference

| Job                         | Selector |
|-----------------------------|----------|
| Prompt input                | `#chat-prompt` (also `[data-testid=chat-prompt]`, `[name=chat-prompt]`) |
| Send                        | `#chat-send` |
| Variations                  | `#chat-variations` |
| App state root              | `[data-state]`, `[data-state-rev]`, `[data-error-code]`, `[data-auth-error]`, `[aria-busy]` |
| Clarify options             | `[id^="clarify-"]` (i.e. `#clarify-{q}-{o}`) |
| Confirm-refine fork         | `#proposal-use` / `#proposal-cancel` (edit box `[name=refined-prompt]`) |
| Invalid-prompt fork         | `#invalid-generate` / `#invalid-cancel` |
| Variations fork             | option cards `[id^="variation-option-"]`; `#variation-commit` / `#variation-cancel` |
| Timeline step               | `#timeline-step-{id}` (+ `data-status="ok\|error"`) |
| Download menu               | `#viewer-download` |
| Export STL / STEP / source  | `#export-stl` / `#export-step` / `#export-source` |
| New model (reset)           | `#project-new` |
| Account panel               | `#account-toggle` |
| Token create                | `#account-token-name`, `#account-token-create`, `#account-token-value`, `#account-token-copy` |
| Token revoke                | `[id^="token-revoke-"]` |
| Delete account (in-DOM)     | `#account-delete` → `#account-delete-confirm` / `#account-delete-cancel` |

There are **no native `confirm/alert/prompt` dialogs** anywhere — those freeze
browser agents. Account deletion uses an inline confirm.

## 5. Worked recipe: create → ask → export STL

```js
const { chromium } = require('playwright')

const PAT = process.env.EASYCAD_PAT
const baseURL = process.env.EASYCAD_URL || 'http://localhost:8852'

const browser = await chromium.launch()
const context = await browser.newContext()          // fresh context — mandatory

await context.addInitScript(({ token, appOrigin }) => {
  if (window.top !== window) return
  if (location.origin !== appOrigin) return
  if (sessionStorage.getItem('easycad_pat_used')) return
  sessionStorage.setItem('easycad_pat_used', '1')
  sessionStorage.setItem('easycad_pat', token)
}, { token: PAT, appOrigin: new URL(baseURL).origin })

const page = await context.newPage()
await page.goto(baseURL + '/app')

async function turn(fn) {
  const root = page.locator('[data-state]')
  const rev = Number(await root.getAttribute('data-state-rev'))
  await fn()
  await page.waitForFunction((prev) => {
    const el = document.querySelector('[data-state]')
    const r = Number(el.getAttribute('data-state-rev'))
    const s = el.getAttribute('data-state')
    return r > prev && ['done', 'error', 'awaiting-input'].includes(s)
  }, rev)
  return root.getAttribute('data-state')
}

await turn(async () => {
  await page.fill('#chat-prompt', 'a 20mm cube with a 6mm hole through the center')
  await page.click('#chat-send')
})
// handle 'awaiting-input' forks here if they appear, then re-run turn()

const [download] = await Promise.all([
  page.waitForEvent('download'),
  (async () => { await page.click('#viewer-download'); await page.click('#export-stl') })(),
])
await download.saveAs('model.stl')

await browser.close()
```

The same logic runs under Claude-in-Chrome with an equivalent origin-guarded
document-start injection.

## 6. Contract limitation

The agent must be able to seed origin-scoped storage via shape A or B. Every
automation we target can. If some future agent can do neither, that is a
documented limitation — **not** a reason to reintroduce a URL token.
