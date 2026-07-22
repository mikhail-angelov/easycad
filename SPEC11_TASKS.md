# SPEC11 Implementation Tracker

Rebuilding EasyCAD as the SPEC11 "CadQuery Chat" — a text-only, single-screen
incremental 3D model builder. Old image-recognition app fully removed.

Runtime: backend runs in `.venv-poc` (has cadquery 2.8.0). LLM keys in `.env`
(`DEEP_SEEK_KEY`, `OPEN_ROUTER_KEY`). Default provider: deepseek-chat.

## Tasks

- [x] **Task 0 — Cleanup.** DONE. Removed old app/tests/worker/scripts/projects/fixtures/
      docs/TASKS*/CONTEXT/old static build/old frontend src + runtime dirs (old/artifacts/logs).
      Kept all spec*.md, scaffolding, poc_cadquery_chat.py, styles.css palette. Added .DS_Store to .gitignore.
- [x] **Task 1 — Backend core.** DONE. `app/cq_worker.py` (isolated worker), `app/cadquery_exec.py`
      (subprocess executor + geometry info), `app/llm.py` (providers + code generator, default
      deepseek-chat). `tests/test_cadquery_exec.py` — 5 passing. Installed fastapi/uvicorn/pytest
      into .venv-poc via uv. requirements.txt + Makefile PYTHON now point at .venv-poc.
- [x] **Task 2 — Backend API.** DONE. `app/store.py` (SessionStore/Step) + `app/main.py` FastAPI:
      `/api/session`(+reset), `/api/steps`, `/api/steps/{id}`(+revert), `/api/execute`(stateless),
      `/api/execute-manual`, `/api/chat`, `/api/export/{id}`. Static mount for built frontend.
      `tests/test_api.py` — 5 passing (10 total). Live chat smoke test OK (deepseek open-top box).
      NOTE: /api/generate from spec folded into /api/chat for MVP (direct generation, no refiner).
- [x] **Task 3 — Frontend shell.** DONE. Preact 3-panel layout (editor|viewer|chat) + bottom
      timeline, text input only. Files: main.tsx, app.tsx, api.ts, store.ts (zustand),
      components/{Editor,Viewer,Chat,Timeline}.tsx, rewritten styles.css (palette preserved).
      Vite dev proxy /api -> 8852. `npm run build` clean. Live HTTP smoke: session + chat
      (open-top box) verified. Editor=textarea + Viewer=placeholder pending Tasks 4/5.
- [x] **Task 4 — 3D viewer.** DONE. `viewer3d.ts` (three.js: scene, OrbitControls, grid,
      lighting, base64-STL parse, Z-up→Y-up, camera auto-fit, wireframe) + Viewer.tsx.
      Verified in-browser: box renders with shading on grid, export link, wireframe toggle.
- [x] **Task 5 — Code editor.** DONE. Monaco (slim: editor.api + python.contribution only,
      3.1MB bundle) with line numbers + Python highlighting, two-way store sync, Run button.
      Browser-verified: keywords blue, comments green, strings red; syncs on chat/revert.
- [x] **Task 6 — Chat + timeline wiring.** DONE (verified with Task 4). Chat bubbles
      (user + Step N ✓ / error), collapsible refined-prompt block, provider select,
      timeline click-to-revert. Browser-verified: chat updates editor+viewer+timeline;
      revert to step 0 restores solid box. No console errors.
- [x] **Task 7 — Integration & verify.** DONE. Full browser E2E (chat->code->3D->timeline->
      revert->branch) verified. Static served by FastAPI with no-cache index.html + cacheable
      hashed assets + SPA catch-all (fixes stale-bundle caching). Removed ported poc_cadquery_chat.py.
      10 backend tests pass; `npm run build` clean. `make run` serves the app on :8852.

## STATUS: SPEC11 MVP COMPLETE. All tasks done & verified.

Run: `make run` (uses .venv-poc) then open http://127.0.0.1:8852/ (rebuild frontend
with `npm run build` after UI edits). New source is uncommitted (not asked to commit).

## Phase 2 (in progress)

- [x] **P2-1 — Prompt Refiner (Stage 1).** DONE. `app/refiner.py` (refined prompt + clarifying
      questions from geometry info, robust JSON parse) + `/api/refine` + `/api/chat` `auto_refine`.
      Live-verified: short RU prompts -> precise coord-annotated instructions.
- [x] **P2-2 — Clarifying questions UI.** DONE. refine toggle, collapsible refined-prompt,
      clarify-question option buttons (store.pending + answerClarification). Browser-verified.
      Note: deepseek resolves aggressively, rarely returns questions (spec-compliant).
- [x] **P2-3 — Provider/model picker in UI.** DONE. Provider dropdown + model override input
      (blank = provider default). Wired through chat/variations. Build verified.
- [x] **P2-4 — Retry with variations.** DONE. Backend `/api/variations` (refine once, then N=3
      generations at temp 0.7) + `/api/commit`; store previewVariation/commitVariation/cancel;
      UI ×3 button + candidate cards (size/topology) + preview-in-viewer + Use/Cancel.
      Browser-verified: 3 candidates -> preview hole model -> commit as step 1.
- [x] **P2-5 — Session persistence (autosave+resume) + project file save/load.** DONE.
      Autosave to `~/.easycad/session.json` (override `EASYCAD_SESSION_FILE`) on every mutation;
      `_ensure_initial` resumes from disk on startup; reset() starts fresh (no reload).
      `store.to_project/load_project`. Endpoints `/api/project/export` (download JSON) +
      `/api/project/import`. Topbar: Save project / Load project (file picker) / New model.
      tests/conftest.py isolates autosave path; 6 persistence tests (16 total, all pass).
      Browser-verified: 2 steps survive a real server restart; export download + import roundtrip OK.

## STATUS: SPEC11 MVP + Phase 2 COMPLETE. All tasks done & verified. 16 backend tests pass.

## Post-Phase-2 fixes (user testing)

- **Refiner grew outer dims / used outward shell.** The refiner rephrased a precise
  "hollow, flush with top" prompt into a `shell` that expanded the box outward
  (53x83x31.5 instead of 50x80x30). Fixed REFINER_SYSTEM_PROMPT: preserve outer
  bounding box, hollow inward only, prefer boolean cuts over shell, keep already-precise
  prompts as-is. Now 3/3 correct 50x80x30.
- **Opaque exec error.** `Execution error: <cadquery.cq.Workplane object at 0x..>` when
  generated code failed an assert/raise carrying an object. Fixed cq_worker `_describe()`
  to prefix the exception type (now e.g. `Execution error: AssertionError`).
- **Stale-session confusion.** P2-5 resume loaded a leftover TEST session (box(40,40,40))
  so a "first" prompt built on the wrong base. Cleared ~/.easycad/session.json. Resume is
  as-designed; "New model" starts fresh. (Consider: surface which session resumed.)
- Tip: for already-precise prompts, the `refine` toggle OFF = exact Phase-1 direct generation.

## Text-only project format (no binary)

`store.to_project()` now excludes `stl_base64` — the project/autosave file is pure text
(prompts + code + geometry_info), small and git-diffable. The STL is regenerated from each
step's code on demand via `_ensure_step_stl()` (called in `_session_payload`, `get_step`,
`export_step`), so resume/revert/export still work without persisting binary. Verified:
autosave ~1.5KB, 0 stl_base64, resume regenerates the current model. Test asserts no stl in file.

## Triage redesign (replaces always-refine)

`app/refiner.py` is now `triage()` — one LLM call returns a verdict:
- **ready**: precise & consistent -> generate the ORIGINAL prompt unchanged (no degrading).
- **refine**: underspecified -> propose a refined prompt; the UI shows it (editable) and the
  user confirms with a button before generation (no silent rewrite). Size-preserving rules kept.
- **clarify**: ambiguous -> option-button questions (existing UI).
- **invalid**: contradicts the current model (e.g. "50x80x30 box" while model is a 40mm cube)
  -> shows the reason; user can "proceed anyway" (direct) or cancel. NO step created.
All human-facing text (refined/questions/reason) is produced in the SAME LANGUAGE as the request.
`/api/chat` response is now `action`-based: generated | confirm_refine | clarify | invalid.
Confirm = re-call /api/chat with auto_refine=false + refined_prompt (records original+refined).
Store: proposal/invalidNotice state + confirmProposal/dismissProposal/proceedInvalid/dismissInvalid.
Chat.tsx: proposal block (editable textarea + Использовать/Отмена) + invalid block.
Browser-verified: RU detailed prompt -> RU refined proposal -> confirm -> correct 50x80x30 (11 faces);
contradiction -> invalid with RU reason, no step. 16 backend tests pass.

## Deferred (Phase 3 per spec)

Measurement tool in viewer, code validation/linting pre-exec, template library,
prompt history/suggestions.

## Notes / decisions

- Keeping Preact (already set up) rather than switching to React/vanilla.
- Backend consolidated onto `.venv-poc`; Makefile PYTHON points there.
- `poc_cadquery_chat.py` kept as reference until Task 1 ports it, then removed.
