# SPEC20: Core App UX Overhaul

## Status

Proposed. Scope is the **main app workspace** (`/app`) only — the three-panel
`Code | Model | Chat` layout plus the step timeline (`frontend/src/app.tsx`,
`components/{Editor,Viewer,Chat,Timeline}.tsx`, `styles.css`). Built on the
shipped SPEC13/14 multi-tenant app and the SPEC19 launch polish; it does **not**
touch the generation pipeline, refiner/triage, worker, pricing, auth, or the
marketing landing.

Derived from a UX pass over the live app (2026-08-01). The findings are layout
and interaction issues, not bugs — everything renders and works; the problem is
that the default experience is shaped for a developer while the product promise
("describe it, print it") targets someone who does not want to see code.

Revised twice after spec reviews (`review.md`, 2026-08-01). Round 1: W1/W2
reconciled via an explicit layout matrix (a fresh, editor-hidden load never shows
the three-column code-first default); W2 requires conditional mounting of the lazy
editor (not `display:none`); W5 defines an at-rest CTA for an already-exhausted
session. Round 2: **W4 redesigned around the server helpers that already exist**
(`strip_/append_geometry_block`, the separate `geometry_info` field, the single
`_base_code` chokepoint) — the frontend holds only base code and needs no marker
knowledge, which removes the marker coupling and the editor two-way-binding fight.
A **Capacity / load** section was added because holding **10 RPS** is a hard
constraint: the overhaul is client-only or load-reducing except for W4, whose only
hot-path cost is one idempotent regex at the LLM boundary (dwarfed by the LLM +
CadQuery work already on that path). Round 3: the re-attach moved out of
`_base_code` into a null-safe `_with_geometry` helper applied only at the LLM
boundary (so `_is_initial_model` and the first-request `replace_initial` path keep
working, and a missing `geometry_info` can't 500), an explicit endpoint
canonicalization matrix was added, and the load-harness path/threshold were
corrected. Round 4: the base-code contract moved to the single `Step.to_public()`
serialization boundary (so the block also stays gone on revert/reload/preview/
import, not just on generate), the `_with_geometry` seam was clarified for
`/api/refine`, and W3's uncontrolled proposal-textarea got its own resize path.
Round 5: **`Step.code` now stores base code** (not `code_with_geometry`) so
`to_public` strips nothing on read — this removes the O(history) per-serialization
cost that `_session_payload` would otherwise incur on every generation; the stat
strip is parsed language-neutral and labelled via EN/RU i18n (not the English
`summarize`); and the worker load harness is no longer cited as W4 evidence (it
exercises only the worker, not the API where W4 lives — API tests cover it).
Round 6: made **`Step.code` is always base** an explicit invariant enforced at
every write boundary — including a `strip_geometry_block` at `Project.load` so an
imported legacy file can't resurface the block now that `to_public` no longer strips.

## Problem Statement

1. **Not responsive.** `styles.css` has zero `@media` rules. The workspace is a
   horizontal flex of three panels with a fixed `.chat-panel { flex: 0 0 360px }`
   (`styles.css:404-407`). On a tablet/phone the editor and viewer collapse toward
   zero width and the 360px chat rail does not fit. The landing is mobile-friendly,
   so mobile visitors reach `/app` and hit an unusable wall.
2. **Code is front-and-center by default.** Raw CadQuery Python occupies the
   leftmost third from first load (`app.tsx:72-80`, `Editor.tsx`). It contradicts
   the "no code" promise and intimidates the target user, while consuming the most
   horizontal space of any panel.
3. **Chat input is too small for the actual task.** The prompt textarea is
   `min-height: 44px` (~2 lines), `max-height: 140px`, `resize: vertical`
   (`styles.css:775-784`) — it does not auto-grow. Real modelling instructions are
   often several sentences; users type into a two-line box that only enlarges if
   they manually drag the handle.
4. **Geometry info is duplicated.** The same bounding-box / size / topology block
   appears both as comments inside the editable code and in the `pre` under the
   viewer (`Viewer.tsx:95`). Injecting auto-generated read-only text into editable
   source is confusing (the "do not edit" comment is the tell), and it is presented
   as a code comment rather than a readable stat.
5. **Cryptic, hover-only controls.** `×3` (variations, `Chat.tsx:348-355`), the
   `refine` checkbox (`Chat.tsx:153-160`), and the icon-only topbar buttons
   (save/load/new) convey meaning only through `title` tooltips — which do not
   exist on touch. The red `0 free left` trial pill (`Chat.tsx:177-189`) also sits
   in the chat header at rest, reading as a discouraging first impression.

## Goal

Make the core workspace usable and on-message for a non-coder on any screen:
responsive down to a phone, code hidden until asked for, an input sized for real
instructions, geometry info shown once as a clean readout, and controls that are
legible without hover. No change to what the app *does* — only how the workspace
is laid out and labelled. EN + RU throughout.

---

## W1 — Responsive layout

### Problem
The single horizontal three-panel layout is desktop-only; there is no mobile or
tablet fallback (no `@media` in `styles.css`).

### Implementation Decisions
- Introduce breakpoints in `styles.css` (the only stylesheet). The code editor's
  visibility is owned by W2's persisted toggle; W1 defines how each regime lays out
  for **both** editor states. The default persisted state is *hidden* (W2), so the
  "default first load" column below is the hidden one.

  | Regime | Editor hidden (default) | Editor shown |
  |---|---|---|
  | **Wide** (≥ ~1024px) | Viewer + Chat, two columns | Editor + Viewer + Chat, three columns (today's layout) |
  | **Medium** (~640–1024px) | Viewer + Chat, two columns | Editor collapses to an overlay/full-width panel over Viewer+Chat; not a third column |
  | **Narrow** (< ~640px) | single column: Viewer → Chat, timeline as a compact bar | editor opens full-screen over the stack, dismissable back to the stack |

  There is no regime where a fresh (editor-hidden) load shows the three-column
  code-first layout — this resolves the W1/W2 tension the old "wide = unchanged"
  wording created.
- Replace the fixed `.chat-panel { flex: 0 0 360px; width: 360px }` with a
  min/max (e.g. `min-width: 320px`, grows) that can go full-width at narrow sizes.
- Minimum usable sizes: Viewer never below ~280px in its cross axis; Chat input +
  Send always visible without scrolling the body at any breakpoint.
- The 3D viewer (`viewer3d.ts`) must resize when its container reflows (breakpoint
  change, editor toggle, orientation change), not only on window resize — confirm
  the stage re-fits after a layout change, not just a viewport change.
- No separate mobile route or component tree — one responsive layout.

### Testing Decisions
- Manual/visual at 375px, 768px, and 1440px, in **both** editor states: no
  horizontal body scroll, chat input + Send reachable, viewer at a usable size,
  and a fresh load never shows the three-column code-first default.
- The existing component tests are unaffected (layout/CSS change). Make the three
  viewport checks (both editor states) part of the release acceptance procedure in
  this spec — there is no existing verify checklist file to amend.

---

## W2 — Code editor behind progressive disclosure

### Problem
The Monaco editor is the leftmost, always-on panel, but the target user never
edits Python. It dominates the layout and undercuts the "no code" positioning.

### Implementation Decisions
- Hide the editor by default; expose it via a **"Show code"** toggle in the topbar
  or the Model panel header. Persist the choice in `localStorage` (same pattern as
  the welcome-seen flag in `Chat.tsx:6`), so power users who open it keep it open.
- When hidden, give the freed horizontal space to the Viewer and Chat per the W1
  matrix.
- **Conditionally mount** the lazy `Editor` on the persisted choice: today
  `<Editor />` is always rendered inside `Suspense` (`app.tsx:72-75`), so its
  dynamic import — and Monaco init — start on first render even behind
  `display:none`. Only render `<Suspense><Editor/></Suspense>` when the editor is
  requested; otherwise render nothing (no Monaco download/init). This is what makes
  the default load lighter — a purely visual `display:none` would **not** deliver
  that benefit.
- Keep the editor fully functional when shown (manual edit + Run — `Editor.tsx:59`);
  this is disclosure, not removal. On show, the code (kept in the store) populates
  the freshly-mounted editor; on hide, the store's code is retained so a later show
  or a chat update is consistent.
- The `Run ▷` action must remain available whenever the editor is visible.
- Do **not** eagerly preload the Editor chunk (no top-level `import('./Editor')`,
  no preload hint) — conditional mounting only keeps Monaco off the default load if
  nothing else triggers its dynamic import first.

### Testing Decisions
- Default first-load (fresh `localStorage`) shows no editor **and** does not
  request the Monaco chunk; the toggle reveals it, loads the chunk, and the state
  survives reload.
- With the editor hidden, a chat generation still updates the viewer and timeline;
  opening the editor afterward shows the current code.

---

## W3 — Auto-growing chat input

### Problem
The prompt box is a fixed ~2-line textarea that only grows on manual drag
(`styles.css:775-784`), but instructions are frequently long.

### Implementation Decisions
- Make the textarea auto-grow with content between a larger `min-height`
  (~3 lines) and a `max-height` (then scroll), driven off `scrollHeight` on input
  in `Chat.tsx` (the component already holds the `text` state and an `inputRef`).
- Raise the resting size so a typical multi-sentence instruction is visible
  without scrolling; keep `Enter` = send, `Shift+Enter` = newline (already wired,
  `Chat.tsx:337-342`) and add a subtle hint of that convention near the input.
- Apply the same growth behaviour to the refined-prompt proposal textarea
  (`Chat.tsx:257-263`) for consistency, since edited refinements are also long.
  That textarea is **uncontrolled** (`defaultValue` + `proposalRef`, no `value`
  state), so it can't be driven off `text` state — give it its own `onInput`
  handler plus a one-shot resize on mount to fit the prefilled refined prompt.
- Recompute the DOM height on **every** `text` change, not only `onInput`:
  programmatic changes must resize too — the retry-restore path sets `text` via
  `setText` (`Chat.tsx:87-93`), and clearing on send must shrink the box back to
  the resting height. Drive the recalc from the `text` state (e.g. a layout effect),
  so keystrokes, retry-restore, and send-clear all reflow.
- To **shrink** as well as grow, reset `height` to `auto` before reading
  `scrollHeight` each recalc (otherwise height only ratchets up and send-clear
  never returns to resting). Drop the manual `resize: vertical` (`styles.css:782`)
  since JS now owns the height — a manual drag would just be overwritten on the
  next keystroke.

### Testing Decisions
- Typing a multi-line instruction grows the box up to the cap, then scrolls; after
  Send the box returns to its resting height.
- Retry-restore (`server_busy`) repopulates the box **and** grows it to fit the
  restored prompt, not a clipped 2-line box.
- `Enter` submits, `Shift+Enter` inserts a newline, `busy` still disables input.

---

## W4 — Single geometry readout

### Problem
Bounding box / size / topology is shown twice — as comments in the editable code
and in the viewer `pre` (`Viewer.tsx:95`) — and both as raw code comments.

### What the codebase already gives us
The geometry block is **already** a server-side concern with helpers for both
directions, so W4 reuses them instead of adding a parallel frontend parser:
- `strip_geometry_block(code)` and `append_geometry_block(code, info)` on a
  compiled `_GEOMETRY_RE` (`app/cadquery_exec.py:36,59-66`). `append` is
  idempotent — it strips first, then re-appends — so `execute()` already rebuilds
  the block on **every** run regardless of whether its input carried one
  (`cadquery_exec.py:85`).
- Every response already returns `geometry_info` as its **own field**, separate
  from the code (`app/main.py:1063,1082,1208,1367`; the store already keeps it as
  `geometryInfo` and the viewer already shows it).
- `_base_code(store, current_code)` (`app/main.py:732-736`) is the **single**
  place triage, generate, and refine obtain the base code from.

The generator (`app/llm.py:134-142`) and refiner (`app/refiner.py:31-59,167-169`)
need the block for coordinate positioning — but they obtain their code
server-side, just after `_base_code`, so the block can be re-attached there
(see the LLM-boundary helper below) without the client ever holding it.

### Implementation Decisions
- **Frontend holds and edits base (stripped) code only** — no marker knowledge,
  no `splitGeometryComment`. This removes both the cross-language marker coupling
  and the Editor two-way-binding fight (the editor model and the store both hold
  the same stripped code, so the `code`-sync effect in `Editor.tsx:50-53` never
  re-injects a block).
- **Store base code in `Step.code` — do not store `code_with_geometry`.** Today
  every successful path stores `res.code_with_geometry` as `Step.code`
  (`app/main.py:684,1080,1206,1365,1412`). Change these to store the **base** code
  (`strip_geometry_block(...)`, or the base `execute()` already has before its
  `append`), keeping `Step.geometry_info` as the separate field it already is
  (`app/store.py:20,22`). Then:
  - `Step.to_public()` serializes base code with **no strip** — every read path
    (`/api/session`, `/api/steps` at `main.py:724`, revert, commit,
    `/api/project/export`, and `_session_payload`) returns base code at **zero**
    per-serialization cost. This is what avoids the O(history) strip that a
    strip-on-read design would add: `_session_payload` serializes *all* steps on
    every successful generation (`main.py:1087,1220,1232,1419`), so stripping there
    would scale with history length.
  - Two payloads that carry code but aren't `Step.code` also become base:
    the variation-candidate dict (`main.py:1366`) and the `/api/execute` dict
    (`main.py:1064`) — store/emit base there too.
  - The stored `code_with_geometry` field/column is no longer needed; drop it.
  - Frontend never strips. The wire payload gets *smaller* (loses the ~4 lines).
- **Outbound reconciliation at the LLM boundary only — not in `_base_code`.**
  `_base_code` stays base-in/base-out; re-attaching there would poison
  `_is_initial_model`, which compares its argument to `current.code` by exact
  string (`app/main.py:739-741`). Instead add a single null-safe helper
  `_with_geometry(store, base_code)` and apply it **only** to the code handed to
  the LLM (triage/generate/refine), so the initial-placeholder detection keeps
  working on base code and only the LLM sees the block:
  - On the **generate** paths (`/api/chat`, `/api/variations`), apply it **after**
    the `_is_initial_model` check (`app/main.py:1141,1251,1315,1344`) so the
    `replace_initial` first-request path keeps working on base code.
  - On **`/api/refine`**, the triage call runs on `_base_code(...)` directly with
    **no** `_is_initial_model` check (`app/main.py:1105`) — refine always operates
    on an existing model — so wrap that triage input **unconditionally** at 1105.
    The "after the initial-model check" rule applies only to the generate paths.
  - **Null contract:** if `store.current()` is missing or its `geometry_info` is
    falsy (`ExecResult.geometry_info` is `str | None`, and a failed initial/worker
    run leaves it unset — `app/main.py:680-689`), `_with_geometry` returns the base
    code **unchanged** and never calls `append_geometry_block` with `None` (which
    would `TypeError` → 500).
  - Also normalize `_is_initial_model` to compare stripped-vs-stripped defensively,
    so a stored initial that still carries a block never mismatches base input.
  - Cost: one idempotent regex on the generate/refine path, which already does an
    LLM call + CadQuery execution — no meaningful cost.
- **Endpoint canonicalization matrix** (make the seam explicit — since `Step.code`
  is base, the "Returns" column is base with no read-time strip):

  | Endpoint / payload | Accepts | Re-attaches block for LLM | Returns |
  |---|---|---|---|
  | `/api/chat`, `/api/variations` | base code | yes, via `_with_geometry` after `_is_initial_model` | base code (`Step.code` via `to_public`; candidate dict already base) + separate `geometry_info` |
  | `/api/refine` | base code | yes, `_with_geometry` unconditionally (`main.py:1105`) | n/a (triage only — verdict/refined prompt) |
  | `/api/execute`, `/api/execute-manual` | base code | no — `execute()` rebuilds the block itself; comments don't affect CadQuery | base code + `geometry_info` |
  | `/api/session`, `/api/steps`, revert, commit, `/api/project/export` | — | — | base code (via `to_public`, no strip) |
  | `/api/export/{id}/source` | — | — | base code (`Step.code` is already base — no strip) |
- **Stat strip — parse once, label bilingually.** Do **not** reuse
  `summarize(geometry_info)` verbatim: it returns the raw worker lines `Size:` /
  `Topology:` (English, from `cq_worker.py:21-33`) after stripping `#`
  (`Chat.tsx:133-140`), so RU users would see an English strip (`solid(s)`,
  `faces`, `edges`). Extract a **language-neutral** parser (dimensions + counts as
  a structured result) and render labels via new EN/RU i18n strings — including the
  "geometry unavailable" state. Point the existing variation cards at the **same**
  formatter so they get localized too (they use `summarize` today, `Chat.tsx:307`).
- **`.py` export** (`/api/export/{id}/source`, cold path): `Step.code` is already
  base, so it serves base directly — no strip. STL/STEP untouched.
- **Invariant: `Step.code` is always base.** Enforce it at **every** write
  boundary, not only at step creation. `Project.load` is one such boundary: it
  assigns `code` from the file verbatim (`app/store.py:177`), so strip it to base
  there (`strip_geometry_block`, cold path, one regex per step at import).
  Otherwise an imported pre-SPEC20 file whose `code` carries the block would leave
  `Step.code` with it and — since `to_public` no longer strips — surface the block
  in the editor. With the load-boundary strip, base code holds for any input source;
  `geometry_info` is read alongside (`app/store.py:179`) so the next generate
  re-attaches via `_with_geometry`. This keeps the invariant, not backward compat —
  no per-version migration is written.
- **Stat-strip fallback:** detect failure from the `geometry_info` payload
  (absence of `Size:`/`Topology:` lines — the `"could not extract"` marker,
  `cq_worker.py:36`), not from the code, and show a neutral "geometry unavailable"
  state.

### Testing Decisions
- The Monaco view and the `.py` export show source **without** the block; a
  generate/refine turn where the client sent base code still reaches the LLM with
  the block re-attached (assert `_with_geometry` output contains it).
- **First-request replacement survives the conversion:** with base code stored and
  sent, the first `/api/chat` and `/api/variations` still detect the initial
  placeholder and pass `replace_initial=True` (regression test for the
  `_is_initial_model` normalization).
- **Null-safe re-attach:** when the current step has no `geometry_info` (failed
  initial/worker run), the next chat/refine returns its normal
  operational/generation result — **not** a 500 from appending `None`.
- **Block stays gone across all read paths:** after a revert, a session
  reload/rehydrate, a variation preview, and a project export→import round-trip,
  the editor code contains no geometry block (regression for base-`Step.code` +
  candidate/`execute` base). Importing a **legacy** project file whose `code`
  carries a block also yields base `Step.code` (the `Project.load` strip).
- The viewer stat strip renders localized labels (EN **and** RU) from the parsed
  `geometry_info`, and shows the localized "geometry unavailable" fallback for the
  `"could not extract"` payload; variation cards use the same formatter.
- STL/STEP exports are byte-for-byte unchanged. Manual Run (`/api/execute-manual`)
  accepts base code and returns base code; the geometry block does not affect the
  produced STL (it is only comments), so no re-attach is needed or asserted there.

---

## W5 — Legible, touch-friendly controls

### Problem
Key controls rely on hover tooltips and terse glyphs; the trial counter reads as a
warning at rest.

### Implementation Decisions
- Give the `×3` variations button a visible label ("3 variants" / "3 варианта")
  or an icon-plus-text treatment; keep the tooltip as backup (`Chat.tsx:348-355`).
- Label the `refine` toggle so its purpose is clear without hover, or move it into
  a small labelled control group (`Chat.tsx:153-160`).
- Topbar icon buttons (save/load/new) get accessible visible affordances on
  narrow/touch layouts (text labels or a labelled menu), not `title` only.
- Trial pill: keep it informative but not alarming while generations remain, and
  reserve the red `empty` styling for the actual exhausted state
  (`Chat.tsx:177-189`, `styles.css:749-766`).
- **Rest-state CTA.** Today the `Notice` (with its sign-in / add-key CTA) is only
  set when a request throws a trial-exhausted error (`store.ts:171-187`); a session
  that *loads* already-exhausted (`trialRemaining === 0`) shows a red pill with no
  action. Add a rest-state affordance so the zero state is actionable without
  first bouncing off a failed generation: when `trialRemaining === 0` (and no
  BYOK key), the pill itself is a button that opens the account/sign-in flow
  (`setAccountOpen(true)`), reusing the same target as the notice CTA. Keep the
  existing request-time 402 notice as the fallback path.

### Testing Decisions
- On a touch/narrow layout, variations, refine, and the topbar actions are
  operable and their purpose is legible with no hover.
- While generations remain, the pill shows its non-alarming style. When a session
  loads already-exhausted, the zero-state pill is actionable at rest (opens the
  account/sign-in flow) — without needing a failed request first — and the
  request-time notice still appears as a fallback.

---

## Capacity / load (must not erode the 10 RPS target)

This overhaul must not raise per-request server cost — the SPEC19 launch gate is a
resized worker sustaining the target RPS, and generation cost is dominated by the
LLM call + CadQuery execution, not by app-layer work. Per work item:

- **W1, W3, W5** — client-only (CSS, textarea sizing, labels). Zero server cost.
- **W2** — **reduces** load: the Monaco chunk and its editor worker are no longer
  fetched on the default (editor-hidden) load, cutting bytes served per new visitor.
- **W4** — net neutral, by design:
  - `Step.code` now stores **base** code, so `to_public()` serializes without any
    strip — the read paths (including `_session_payload`, which serializes *all*
    steps on every successful generation) carry **no** new per-step cost. This is
    the whole reason to store base rather than strip-on-read, which would scale a
    regex with history length.
  - The only new hot-path work is one `_with_geometry` (null-safe
    `append_geometry_block`) at the LLM boundary on generate/refine — a single
    regex per turn, dwarfed by the LLM + CadQuery cost already on that path.
  - No new per-keystroke or per-render server calls — the editor stays fully
    client-side; the editor-facing `code` payload gets *smaller* (drops ~4 lines).

The W4 changes live in the **API** service (`Step.to_public`, `_with_geometry`,
the chat/refine/variation paths), so the SPEC18/19 worker harness
`spikes/spec18/http_throughput.py` — which drives `POST /execute` against the
**worker** only (`:49-52,76-82`) — **cannot** measure or regress them; do not cite
it as W4 evidence (keep it for the worker gate). W4's behaviour and its no-extra-
per-step-cost invariant are covered by the API tests above (a generate turn issues
the same LLM/worker calls, and `to_public` performs no strip). A dedicated app-layer
load test is unnecessary for a change this small beside LLM + CadQuery latency.

## Global Out of Scope
- The generation pipeline, refiner/triage, LLM behaviour, worker, and the SPEC18
  execution/security model.
- Pricing, trial limits, auth, BYOK key handling.
- The marketing landing, `/terms`, `/privacy`, `/admin`.
- A visual redesign / rebrand — this is layout, disclosure, sizing, and labels,
  not a new design language.
- Any new CAD operations, providers, or export formats.

## Rollout order
W1 (responsive) → W2 (hide code) → W3 (input) → W4 (geometry readout) →
W5 (labels). W1 is the load-bearing change and W2 depends on its breakpoints;
W3–W5 are independent polish that can ship in any order after W1.

## Acceptance
1. The app is usable with no horizontal body scroll at 375px, 768px, and 1440px in
   both editor states; the chat is the primary surface on narrow screens and a fresh
   load never shows the three-column code-first default.
2. A fresh visitor sees no code editor by default and the Monaco chunk is not
   fetched; "Show code" mounts it and the choice persists across reload.
3. The chat input auto-grows for multi-sentence instructions (including retry-restore)
   and resets after send.
4. The editor shows base source (no geometry block) on **every** read path —
   generate, revert, reload, variation preview, import, `.py` export — because
   `Step.code` stores base code (no read-time strip); geometry appears once as a
   readable, EN/RU-localized stat strip; a generate/refine turn still reaches the
   LLM with the block re-attached via `_with_geometry`, and the first request still
   replaces the placeholder; STL/STEP exports are unchanged.
5. Variations, refine, and topbar actions are legible and operable without hover;
   the trial pill is non-alarming while generations remain and is actionable at rest
   when a session loads already-exhausted.
6. Load unchanged: `Step.code` stores base code so `to_public` does no read-time
   strip; a generate/refine turn issues the same LLM/worker calls and adds only one
   `_with_geometry` regex at the LLM boundary. Verified by API tests, not the worker
   harness (which cannot exercise the API-layer W4 changes).
