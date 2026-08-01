# SPEC19: Product Hunt Launch Readiness (non-marketing)

## Status

Implementation in progress. Bundles the remaining **engineering / product** work
needed before a Product Hunt launch. Marketing deliverables (screencast, PH
gallery, tagline, maker comment) are explicitly out of scope. Builds on the
shipped multi-tenant app (SPEC13/14), the zygote worker (SPEC18), analytics, and
the in-app feedback form — none of those are re-opened here.

Implemented so far:
- **W2** — `GET /admin` read-only dashboard (usage + worker `/statz` proxy +
  feedback), admin-gated; markup in `app/templates/admin.html`. `/api/admin/stats`
  carries a `worker` health block that degrades to `{reachable: false}` when no
  worker is configured/reachable.
- **W1** — operational failures surface as coded, localized notices: `server_busy`
  (503, from the inflight cap, preserves the prompt for one-click retry),
  `execution_timeout` (504), `worker_unavailable` (503). Backend raises via
  `_raise_if_operational`; `ExecResult.code` tags the operational cases; the SPA
  maps each code → orange notice (EN+RU). The worker-resize (config) remains a
  launch-gate step, not code.
- **W3** — verified existing first-run: welcome copy, "your first build is free —
  no sign-up", one-tap `STARTERS`, trial pill (EN+RU). No redesign needed.
- **W4** — `GET /terms` and `GET /privacy` (bilingual static pages under
  `frontend/public/`, linked from the landing footer and the app account panel).
  Repo `LICENSE` (MIT, Mikhail Angelov) added.

Remaining: the W1 worker vertical-resize config + load re-run (launch gate).

## Problem Statement

The product works and is deployed, but a Product Hunt front-page spike exposes
gaps that would read as "broken" to first-time visitors and leave the operator
blind:

1. **Capacity.** On the current single 1-CPU worker, complex geometry sustains
   only ~6–7 RPS (measured; SPEC18 Evidence). A launch spike will saturate it.
   An over-capacity request already returns `503 "Server is busy…"`
   (`app/main.py:562`, gated by `EASYCAD_MAX_INFLIGHT_GEN` / `_gen_semaphore`),
   but as a bare string — the SPA shows it as a generic failure, not a calm,
   localized "try again" notice. Timeouts and a down worker are worse.
2. **Operator blindness.** `GET /api/admin/stats` exists and returns usage +
   feedback, but there is no page: during launch the operator can only SSH/curl.
3. **First impression.** New visitors land on an empty editor; the value and the
   "one change at a time" interaction must be obvious in the first 10 seconds.
4. **Trust.** There is no Terms/Privacy page, yet users may paste their own LLM
   API keys (BYOK) — a fraction of the PH audience checks before trying.

## Goal

Make the app survive and convert a traffic spike: degrade gracefully under load
with honest localized messaging, give the operator a live view, sharpen the
first-run, and publish minimal legal pages. No change to the generation pipeline,
LLM behaviour, pricing, or the execution/security model.

---

## W1 — Capacity headroom & graceful degradation

### Problem
A saturated or slow worker currently surfaces as a raw 503 / timeout / transport
error. Under PH load that is most of what a new user might see.

### Implementation Decisions
- Give the worker real headroom: raise `cpus` and `EASYCAD_WORKER_CONCURRENCY`
  together on the worker service, sized from the SPEC18 numbers, and confirm the
  memory budget still holds (`/statz rss_mb`; zygote import is CoW-shared). This
  is config, not code — but it is part of the launch gate.
- Convert the operational failures the user can hit into **coded, localized,
  actionable notices** using the existing `_coded_error(status, code, message)`
  contract and the frontend `code → notice` mapping (as trial notices already
  do). New codes:
  - `server_busy` (503) — over `EASYCAD_MAX_INFLIGHT_GEN`; "We're under heavy
    load — try again in a few seconds."
  - `execution_timeout` — worker wall-clock timeout; "That model took too long —
    simplify it or try again."
  - `worker_unavailable` — transport failure to the worker; "The modelling
    service is briefly unavailable — try again in a moment."
- The busy notice is a soft/orange notice (retryable), never a red error; it must
  not clear the user's prompt so a retry is one click.
- Keep the existing per-IP rate limit and daily budget kill-switch; ensure the
  budget-exhausted path shows the existing `trial_budget_exhausted` notice (it
  does) rather than a generic error.

### Testing Decisions
- Unit: each new operational failure returns the right `{code}` and HTTP status;
  the SPA maps each `code` to its localized notice (EN+RU) and preserves the
  prompt on `server_busy`.
- Load: re-run `spikes/spec18/http_throughput.py` against the resized worker and
  confirm the target RPS at bounded latency with **zero unhandled 5xx** — a
  saturated run must produce `server_busy` notices, not stack traces.

---

## W2 — Operator admin page

### Problem
`/api/admin/stats` is JSON-only; there is no human view for launch day.

### Implementation Decisions
- Add `GET /admin` — a small, self-contained server-rendered page gated by
  `require_admin` (signed in as `ADMIN_EMAIL`; 404 otherwise, so it is hidden).
- It renders, by fetching `/api/admin/stats`:
  - **Usage:** generations (ok/failed), live sessions, avg chat-gen latency,
    today's trial budget used/limit, signups/feedback counts.
  - **Worker health:** proxy the worker's `/statz` through the app
    (`EASYCAD_WORKER_URL`) so the page shows real jobs_total → **derived RPS**
    (Δ over the poll interval), fork+exec p50/p95, crashes/timeouts, rss. This is
    the "real user RPS" view.
  - **Feedback:** a table of recent entries (rating, email, message, date) — the
    already-included `feedback.recent`.
- No new datastore and no write actions — read-only. Auto-refresh on a short
  interval client-side.

### Testing Decisions
- Admin gate: anonymous and non-admin sessions get 404 for `/admin` and
  `/api/admin/stats`; the admin email gets 200 (extends existing admin tests).
- The stats payload includes the worker-health block when the worker is
  reachable, and degrades to "worker unreachable" (not a 500) when it is not.

## Out of scope for W2
- Per-user drill-down, moderation actions, charts/history, exports. This is a
  read-only launch dashboard, not an analytics product.

---

## W3 — First-run / onboarding clarity

### Problem
The editor opens on a bare model; the core interaction ("describe one change at a
time") and the free-first-build offer must land immediately.

### Implementation Decisions
- Keep and verify the existing empty-state helper and localized `STARTERS`
  one-tap prompts; ensure they are visible before any interaction and that the
  "your first build is free — no sign-up" line is present for anonymous visitors.
- Ensure a first successful generation is reachable in one click (a starter
  prompt) without touching settings or signing in.
- No redesign — copy/visibility polish only, EN+RU.

### Testing Decisions
- The initial session for a brand-new anonymous visitor shows the starters and
  the free-build affordance; clicking a starter yields a rendered model without
  auth (already covered by the session/trial tests — extend if a gap exists).

---

## W4 — Legal: Terms & Privacy (minimal)

### Problem
No Terms/Privacy exists; BYOK key handling and data retention are undocumented on
the public surface.

### Implementation Decisions
- Add two minimal static pages, `GET /terms` and `GET /privacy` (same static
  serving as the landing), linked from the landing footer and the app account
  panel. EN + RU.
- Privacy must state plainly: what is stored (account email, settings, CAD
  sessions in-memory, feedback), that **BYOK keys are encrypted at rest**
  (SPEC14) and never logged, the analytics in use (Yandex.Metrica), and how to
  delete the account (the existing `DELETE /api/auth/me`).
- Terms: as-is/no-warranty, acceptable-use, that generated geometry is the
  user's. Keep it short and honest; this is a solo-operator launch, not an
  enterprise contract.

### Testing Decisions
- `/terms` and `/privacy` return 200 and are linked from the landing and the app;
  robots/sitemap unaffected. No dynamic behaviour to test beyond reachability.

---

## Global Out of Scope
- All marketing: screencast/demo video, PH gallery images, tagline, maker profile
  and first comment, launch-day scheduling.
- Horizontal worker scaling / load balancer / multi-node (W1 is vertical headroom
  on the existing single worker; multi-worker remains SPEC18's documented note).
- Payments/subscriptions, new LLM providers, new CAD operations.
- Any change to the generation pipeline, refiner/triage, or the SPEC18 execution
  and security model.

## Rollout order
W2 (admin visibility) → W1 (capacity + graceful degradation) → W3 (first-run) →
W4 (legal). W2 first so the operator can watch W1's load behaviour and the launch
itself; W1 is the load-bearing reliability work; W3/W4 are lower-risk polish.

## Acceptance (launch gate)
1. Resized worker sustains the target RPS for the representative model mix with
   zero unhandled 5xx; saturation shows `server_busy` notices.
2. `/admin` shows live usage + worker RPS + feedback, admin-gated.
3. A new anonymous visitor reaches a rendered model in one click.
4. `/terms` and `/privacy` are published and linked.
