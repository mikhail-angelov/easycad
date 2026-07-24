# SPEC14: Free Trial, Provider/Model Pickers, Key Validation & Warning Notices

## Status

Proposed implementation specification. Builds on SPEC13 (multi-tenant SaaS,
BYOK, magic-link auth). Does not change the chat/step/geometry UX or SPEC12
execution isolation. Motivated by first-user feedback: people liked the app but
did not understand *how to start* — they hit the "add your LLM key" wall
immediately and the key form gave no guidance (no provider choice, no model
choice, no validation).

## Goal

Lower the first-run barrier and make the key form self-explanatory:

1. **Free trial on the operator's DeepSeek key**, so a new user can produce a
   model before ever touching a key:
   - **Anonymous visitor:** 1 free generation, then must register or add a key.
   - **Registered (signed-in) user:** 10 free generations, then must add a key.
2. **Explicit provider + model pickers** in the key form (DeepSeek / OpenRouter),
   with a sensible hardcoded default that the user can change.
3. **Key validation** — check the prefix by provider *and* do a live test call,
   so the user learns immediately whether the key works instead of only at the
   next generation.
4. **Warning (orange) notifications** — distinct from red errors — for
   "free limit reached", "register for more", "invalid key", "generation failed".

## Definitions

- **"Request" / generation** = one `POST /api/chat` call that hits the LLM to
  produce/modify code. `triage` and `refine` sub-calls do **not** count
  separately; one user turn = one trial unit.
- **Trial key** = the operator's server-side `DEEP_SEEK_KEY` (existing env).
  On trial, **both provider and model are fixed** — always DeepSeek with
  `deepseek-chat`. The user selects nothing; the pickers are inactive/hidden
  until a key is added. Model selection is a **BYOK-only** feature.

## Trial tiers

| Tier | Identity | Free generations | Tracking | On exhaustion |
|------|----------|------------------|----------|---------------|
| Anonymous | `easycad_session` cookie + client IP | **1** | durable, by IP (cookie is secondary/best-effort) | orange notice: "Register for 10 free generations, or add your own key" |
| Registered | account (email) | **10** | durable, per `user_id` | orange notice: "You've used your 10 free generations — add your key to continue" |
| BYOK | account or session with a saved key | unlimited (their key, their cost) | — | — |

Rationale for IP-based anonymous tracking: the in-memory session
(`SessionRegistry`) is evicted on TTL/restart and the cookie is trivially
cleared, so a session-only counter is not a real limit. Anonymous count is
persisted in SQLite keyed by IP; the cookie is a soft secondary signal. Accept
that shared NAT/IP may under-count — 1 free anon request is low-stakes.

## Design

### Providers & models (`app/llm.py`)

Extend each `PROVIDERS` entry with a static allow-list of models and a key
prefix rule:

```python
PROVIDERS = {
    "deepseek": {
        "base_url": "https://api.deepseek.com",
        "api_key_env": "DEEP_SEEK_KEY",
        "default_model": "deepseek-chat",
        "models": ["deepseek-chat", "deepseek-reasoner"],
        "key_prefix": "sk-",
    },
    "openrouter": {
        "base_url": "https://openrouter.ai/api/v1",
        "api_key_env": "OPEN_ROUTER_KEY",
        "default_model": "deepseek/deepseek-chat",   # DeepSeek is the default
        "models": [
            "deepseek/deepseek-chat",                # default
            "openai/gpt-4o-mini",
            "anthropic/claude-3.5-sonnet",
            "google/gemini-3-flash",
        ],
        "key_prefix": "sk-or-",
    },
    "openai": { ... },  # kept in code, NOT surfaced in the UI dropdown
}
```

> Verify the exact OpenRouter model slugs against its live catalog before
> shipping (`google/gemini-3-flash` and `deepseek/deepseek-chat` in particular) —
> the dropdown must only offer ids OpenRouter actually serves.

**Provider vs. model selection (UX rule).** The user always picks a **model**;
they never toggle a **provider** as a per-generation control. The provider is
implied:

- **Provider is chosen only when entering a key** (in the key form), so we know
  the key type — which drives prefix validation and the API endpoint. One saved
  key at a time (`settings = {provider, model, key}`, unchanged from SPEC13).
- **At generation time** the provider follows from state: on trial it is forced
  to DeepSeek (operator key); with a saved key it is that key's provider.
- **On trial, nothing is selectable** — provider and model are hard-forced to
  DeepSeek / `deepseek-chat`; the model dropdown is inactive until a key exists.
- **With a saved key**, the model dropdown becomes the live generation control:
  user-selectable among that provider's `models`, defaulting to `default_model`.

### Trial accounting (`app/db.py`, new)

Durable counters in the existing SQLite DB:

- Add column `trial_used INTEGER NOT NULL DEFAULT 0` to `users`.
- New table `anon_trial (ip TEXT PRIMARY KEY, used INTEGER NOT NULL DEFAULT 0,
  first_seen REAL)`.
- Helpers: `incr_user_trial(user_id) -> int`, `get_user_trial(user_id) -> int`,
  `incr_anon_trial(ip) -> int`, `get_anon_trial(ip) -> int`. Increment must be
  atomic (single UPDATE … RETURNING or INSERT … ON CONFLICT).

Limits configurable via env with defaults: `EASYCAD_TRIAL_ANON=1`,
`EASYCAD_TRIAL_USER=10`.

**Grant is lifetime, not periodic.** The counter is cumulative and never resets
— "10 free generations" is a one-time onboarding grant, not a monthly quota.
(This is a trial, not a plan; billing/metering is a non-goal.)

**`anon_trial` cleanup.** The table grows one row per distinct IP ever seen. A
periodic sweep (reuse the session sweeper cadence, or a daily job) deletes rows
older than N days by `first_seen`. Rows past the anon limit can be pruned freely;
a returning IP simply re-inserts and gets its 1 grant again — acceptable for a
1-request anon allowance.

### Key resolution (`app/main.py` `_resolve_llm`)

New precedence, replacing the current `REQUIRE_USER_KEY`-only logic:

1. **User has a saved key** → use it. Provider is fixed by the key; the **model**
   comes from the user's selection (their key, their cost — any model in that
   provider's list is fine). No trial counting.
2. **No key, trial remaining** (anon `used < 1`, or user `used < 10`) → use the
   operator `DEEP_SEEK_KEY` with `provider="deepseek"` **and** `model="deepseek-chat"`,
   both hard-forced (any provider/model in the request is ignored on trial, so
   nobody runs an expensive model on the operator's key). Increment the counter
   **only on a successful generation** (failed calls don't burn the quota).
3. **No key, trial exhausted** → raise `402`/`400` with a machine-readable code
   (`trial_exhausted_anon` / `trial_exhausted_user`) so the frontend shows the
   right orange notice.

Because increment must happen only on success, the counter is bumped in the
`/api/chat` success path, not inside `_resolve_llm`. `_resolve_llm` returns
whether this call is a trial call so the handler knows to count it.

**One user turn = one unit; retries are free.** The check-then-increment gate
counts a *successful* `/api/chat` turn once, regardless of how many internal LLM
calls it makes (triage, refine, retry-with-variations). Re-running a turn after
a failed generation does **not** consume another unit.

**Concurrency (accepted over-grant).** The gate reads the counter before the LLM
call and increments after success, so two simultaneous requests from the same
identity can both pass the check and yield one extra free generation. We accept
this — the worst case is a handful of extra trial calls under a race, not an
unbounded hole. No reservation/locking; keep it simple.

`_client_ip` (already present) supplies the anon key. `EASYCAD_REQUIRE_USER_KEY`
is superseded — remove it or repurpose it to "disable trial" (`EASYCAD_TRIAL_ANON=0`
and `EASYCAD_TRIAL_USER=0` achieve the same).

### Key validation (`app/main.py`, new endpoint)

`POST /api/settings/validate-key {provider, key}` →
`{ok: bool, reason: str|null}`:

1. Fast check: does `key` start with the provider's `key_prefix`? If not →
   `{ok:false, reason:"This looks like a <other> key, not a <provider> key."}`.
2. Live check: a minimal, cheap chat completion (e.g. 1 token). Map auth
   failures → `{ok:false, reason:"Key rejected by <provider>."}`; success →
   `{ok:true}`.

Frontend calls this on **Save**; only persists via `PUT /api/settings` after
`ok:true` (or lets the user save anyway with a warning — see F4).

**Rate-limited.** The live check makes a real provider call, so the endpoint is a
potential free proxy / stolen-key tester. Cap it per client IP (reuse
`RateLimiter`, e.g. 10/hour on `validatekey:{ip}`) and per session; short-circuit
on the prefix failure *before* the live call so bad-format keys never spend one.

### Session/settings payloads

- `/api/session` and `/api/settings` additionally return:
  `providers: {name: {default_model, models}}`, `trial_remaining: int`,
  `trial_tier: "anon"|"user"|"byok"`.
- Frontend renders "N free generations left" and swaps to the BYOK state once a
  key is saved.

### Notifications (frontend)

Split store state into `error` (red, existing) and `notice` (orange, new). New
lightweight `Notice.tsx` banner. Triggers:

- `trial_exhausted_anon` → "Register for 10 free generations, or add your own
  key." (with sign-in + add-key CTAs)
- `trial_exhausted_user` → "You've used your 10 free generations — add your key."
- key validation failure (prefix or live) → orange with the `reason`.
- generation failure currently swallowed into `store.error` → surface clearly.

**Error-code contract (API → frontend).** Error responses carry a stable
machine-readable `code` in the JSON body so the frontend maps code → notice
instead of matching on prose. The set:

| `code` | HTTP | Tier | Frontend treatment |
|--------|------|------|--------------------|
| `trial_exhausted_anon` | 402 | orange | register / add-key CTAs |
| `trial_exhausted_user` | 402 | orange | add-key CTA |
| `invalid_key_prefix` | 400 | orange | inline in key form, no live call spent |
| `key_rejected` | 400 | orange | inline in key form (`reason` from provider) |
| `provider_error` | 502 | red | generation error, offer retry |

Unknown/unmapped errors fall back to the generic red `error`.

## Task breakdown (suggested PRs)

- **PR1 — Providers/models metadata + pickers.**
  `app/llm.py` (`models`, `key_prefix`), `/api/session` payload, `Account.tsx`
  provider+model dropdowns, `store.ts` wiring. No trial yet. *Most visible, no
  contentious decisions — good first slice.*
- **PR2 — Key validation.** `/api/settings/validate-key`, Save-flow in
  `Account.tsx`, orange/green result. Depends on PR1's `key_prefix`.
- **PR3 — Trial accounting backend.** `db.py` schema + helpers, `_resolve_llm`
  rewrite, success-path increment in `/api/chat`, `trial_remaining` in payloads,
  env limits.
- **PR4 — Warning notifications frontend.** `Notice.tsx`, `notice` store state,
  trial-remaining display, exhaustion CTAs, wire error/notice split.

## Non-goals

- No payment/billing. Trial is a fixed free grant, not a metered plan.
- No cross-instance shared counters (single-app-instance assumption from SPEC13
  holds; SQLite is the source of truth).
- No change to CadQuery execution, steps, viewer, or export/import.

## Open risks

- **Client IP must come from a trusted source.** The anon gate keys on client
  IP, so a spoofable IP defeats it. The app sits behind **redoproxy**, which sets
  `X-Real-Ip` from the TCP peer via `Header.Set` (overwrite) — authoritative and
  not client-spoofable. `_client_ip` therefore prefers `X-Real-Ip`, and its
  `X-Forwarded-For` fallback takes the **last** hop (redoproxy's `SetXForwarded`
  appends the real IP to any client-sent XFF; the first element is attacker-
  controlled). Never trust the leftmost `X-Forwarded-For`. This also hardens the
  existing per-IP login rate limit. redoproxy is confirmed to be the edge (no CDN
  or LB in front), so its `RemoteAddr` is the true client — revisit only if that
  ever changes.
- **IP sharing (NAT/corporate/CGNAT):** many anon users behind one IP share the
  1-request grant. Acceptable given the tiny anon allowance; registration is the
  real gate.
- **Operator cost:** trial spends the operator's DeepSeek quota. Bounded by
  `TRIAL_ANON`/`TRIAL_USER` × active users; monitor and tune via env.
