# EasyCAD — TODO / backlog (not yet implemented)

A running backlog of improvements suggested during development but **not built
yet**. Grouped by origin. Status of what IS done lives in `SPEC11_TASKS.md`.

Legend — rough effort: **S** small (~<1h), **M** medium (a few hours), **L** large (a day+).

---

## UI / i18n

### Bilingual UI (ru/en)  · M
The UI chrome is currently English-only. Add a language toggle (ru/en) with an
i18n string table, detect the initial language from the browser
(`Accept-Language`), and persist the choice (per session; per user when signed
in). The LLM already replies in the language of the user's prompt, so this is
purely the static interface text (buttons, tooltips, panel labels, hints).

---

## A. UX improvements I proposed this session

### A1. Make "resume" transparent — session banner + quick "New"  · M
**Why:** With autosave/resume, opening the app silently continues the last
session. This already caused confusion (a leftover test model appeared as the
starting geometry, and a "first" prompt was applied on top of it).
**What:** On load, show a small banner like *"Продолжена сессия (N шагов, изменена <дата>)"*
with a one-click **New model** next to it, so the user always knows whether they
are continuing or starting fresh.

### A2. Autofocus the chat input  · S
**Why:** During testing, the first click on the input sometimes didn't focus it,
so the typed prompt went nowhere ("empty send").
**What:** Autofocus the chat textarea on mount and after each send/confirm so the
user can always just type.

### A3. Better progress feedback during LLM calls  · S–M
**Why:** Triage → refine → generate → execute can take several seconds; today
the only feedback is a disabled Send button showing "…".
**What:** A clearer inline "thinking…" state (which stage: triaging / generating /
executing), so long waits don't feel frozen. (Requires per-stage signals, or at
least a labeled spinner.)

---

## B. Persistence / project-format ideas I floated

### B1. Multiple named sessions with a switcher  · L
**Why:** P2-5 was intentionally scoped to a single autosaved session. The full
SPEC11 vision is `~/.easycad/sessions/<id>/…` with list / resume / duplicate /
delete.
**What:** A session menu in the topbar (list, new, switch, rename, duplicate,
delete), each session its own text-only project on disk.

### B2. Export a project as a folder of per-step `.py` files  · M
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

### D1. Frontend code-splitting  · S
The production bundle is ~3.1 MB (Monaco + three.js in one chunk). Lazy-load
Monaco and/or the viewer to speed first paint. (Fine for a local app; matters if
ever hosted.)

### D2. Auto-migrate legacy project files  · S
Old project/autosave files may still contain base64 STL and `\uXXXX`-escaped
text. They load fine and become clean text/UTF-8 on the next save — a one-shot
"normalize on load" could make this explicit.

### D3. Tune the refiner's "ready" verdict  · S
Triage tends to pick `refine` even for already-precise prompts (it still adds a
hint). Not harmful (the user confirms), but tuning it to return `ready` more
often would skip an unnecessary confirmation step for expert users.
