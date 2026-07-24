# EasyCAD — TODO / backlog (not yet implemented)

A running backlog of improvements suggested during development but **not built
yet**. Grouped by origin. Status of what IS done lives in `SPEC11_TASKS.md`.

Legend — rough effort: **S** small (~<1h), **M** medium (a few hours), **L** large (a day+).

---

## UI / i18n

### Bilingual UI (ru/en)  · M
The in-app chrome is currently English-only. Add a language toggle (ru/en) with
an i18n string table, and persist the choice (per session; per user when signed
in). The LLM already replies in the language of the user's prompt, so this is
purely the static interface text (buttons, tooltips, panel labels, hints). *(The
static landing page already detects ru/en from the browser; this item is only
the app UI.)*

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
