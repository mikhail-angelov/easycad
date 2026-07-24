# EasyCAD — TODO / backlog (not yet implemented)

A running backlog of improvements suggested during development but **not built
yet**. Grouped by origin. Status of what IS done lives in `SPEC11_TASKS.md`.

Legend — rough effort: **S** small (~<1h), **M** medium (a few hours), **L** large (a day+).

---

## Monetization

### Subscription — paid access to the server-side LLM  · L
**Why:** Today, past the free trial (SPEC14) users must bring their own key
(BYOK). A subscription would let a paying user keep using **our** server-side
LLM to build models — no key of their own — as the revenue model.
**What:** A paid plan that, in `_resolve_llm`, becomes a fourth tier above the
trial: an active subscriber runs on the operator key without the trial cap
(instead of hitting `trial_exhausted_*`). Natural extension of the SPEC14 trial
infrastructure (`_resolve_llm` precedence, per-user counters, provider forcing).
**Open (decide later):** billing/payments integration; whether to meter **token
usage** and enforce per-plan **limits** (vs. flat unlimited); which model(s) a
plan may use; how counters reset (monthly vs. lifetime, unlike the trial's
one-time grant). **Decisions and implementation are deferred** — this entry just
records the direction.

---

## Analytics / product metrics

### Usage analytics (Google Analytics / Yandex.Metrica)  · M
**Why:** We have no visibility into how people actually use the app — whether
they onboard correctly, where they drop off, and whether the SPEC14 onboarding
(trial pill, starter chips, welcome) actually converts. Need data to tune it.
**What:** Integrate a web analytics provider (Google Analytics and/or
Yandex.Metrica) and track a small, meaningful event set, not just pageviews:
- onboarding funnel: landing → open `/app` → first prompt sent → first
  **successful** step (the key "aha") → trial exhausted → sign-in / add-key;
- interaction signals: starter-chip click vs. free-typed prompt, refine
  confirm/dismiss, ×3 variations used, revert, manual `Run`, export, language
  switch (en/ru);
- health signals: generation failure rate, clarify/invalid verdict rate.
**Open (decide later):** which provider(s); **privacy/consent** — the app is
currently tracker-free and sets only privacy-preserving cookies, so adding
third-party analytics needs a consent banner (GDPR) and a CSP review; whether to
prefer a self-hosted/cookieless option (Plausible/Umami) over GA/Metrica; keep
event names in one taxonomy module so they don't drift.

---

## API / integrations

### External API + MCP tool (use EasyCAD from an agent)  · L
**Why:** Expose the "text → CadQuery → STL" capability programmatically so an AI
agent can generate/modify 3D models — e.g. an **MCP server** that Claude or
another agent calls to produce parts and get back the model.
**What:** A token-authed public API (not the cookie/magic-link browser flow) with
a small surface — generate, modify, export — returning the CadQuery code + STL
(and ideally STEP) + geometry info; then a thin **MCP server** wrapping it
(tools like `create_model(prompt)`, `modify_model(prompt)`, `export(format)`).
**Open (decide later):**
- **Auth:** per-account API keys/tokens issued from settings; not magic-link
  cookies. Rate-limit + abuse controls (it's an LLM + compute proxy).
- **State:** current sessions are in-memory + cookie-bound; agents need either
  explicit session ids threaded through calls, or a **stateless** `code + prompt
  → new code + STL` call. A stateless variant of `/api/chat`/`/api/execute` is
  probably the cleanest MCP fit.
- **Billing:** programmatic use burns LLM + compute harder than a human — decide
  BYOK-token vs. a paid API plan (ties into **Monetization** above).
- **Output formats:** STL / STEP / raw code; whether to run CadQuery per call or
  return code only.

---

## A. UX improvements

### A1. Autofocus the chat input  · S
**Why:** During testing, the first click on the input sometimes didn't focus it,
so the typed prompt went nowhere ("empty send").
**What:** Autofocus the chat textarea on mount and after each send/confirm so the
user can always just type.

### A2. Better progress feedback during LLM calls  · S–M
**Why:** Triage → refine → generate → execute can take several seconds; today
the only feedback is a disabled Send button showing "…".
**What:** A clearer inline "thinking…" state (which stage: triaging / generating /
executing), so long waits don't feel frozen. (Requires per-stage signals, or at
least a labeled spinner.)

---

## B. Project-format ideas

### B1. Export a project as a folder of per-step `.py` files  · M
**Why:** The project is currently one JSON file. A folder of numbered CadQuery
scripts (`step_00.py`, `step_01.py`, …) would be even more git-native and lets
users open individual steps in any editor / run them standalone.
**What:** Alternative export mode producing a directory (or zip) of `.py` steps +
a small `project.json` manifest (prompts, order). Import reads it back.

---

## C. Deferred from SPEC11 (Phase 3 in the spec)

### C1. Measurement tool in the 3D viewer  · M
Click two points on the model → show the distance. Useful for sanity-checking
generated dimensions without reading the code.

### C2. Code validation / linting before execution  · M
Static-check the generated/edited CadQuery code (syntax, `result` defined,
only-allowed methods) before running the subprocess — faster, clearer failures
than an execution error, and can guide the LLM (list allowed methods).

### C3. Template library (start from a shape, not an empty box)  · M
Let the user start from common primitives/objects (enclosure, bracket, plate,
cylinder…) instead of the default 50×80×30 box, to cut the first few prompts.

### C4. Prompt history / suggestions  · M
Suggest prompts based on past successful ones (per session or global), and/or a
quick-pick of the proven example prompts (the 5-step POC sequence).

---

## D. Smaller / opportunistic

### D1. Auto-migrate legacy project files  · S
Old exported project files may still contain base64 STL and `\uXXXX`-escaped
text. They load fine and become clean text/UTF-8 on the next save — a one-shot
"normalize on load" could make this explicit.

### D2. Tune the refiner's "ready" verdict  · S
Triage tends to pick `refine` even for already-precise prompts (it still adds a
hint). Not harmful (the user confirms), but tuning it to return `ready` more
often would skip an unnecessary confirmation step for expert users.

### D3. Force the reply language explicitly (don't rely on "same language")  · S–M
**Why:** The triage/refiner prompt asks the model to answer "in the SAME language
as the request", but the model drifts — observed live: an **English** starter
prompt ("Make it 10 mm thinner") came back with a **Russian** refined
instruction. That's a wart exactly in the new-user onboarding flow (clicked an
English chip, got Russian back).
**What:** Determine the prompt language explicitly instead of leaving it to the
model to infer, then inject it as a direct instruction (e.g. *"Write refined_prompt,
questions and reason in <language>."*). Detection can be a cheap heuristic
(unicode script / stop-words: Cyrillic → ru, else en) computed server-side and
passed into `triage()` / `generate_code()`, or a first step where the model
reports the detected language and we echo it back. Apply to both the refiner and
any human-facing generator text.
