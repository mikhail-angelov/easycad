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

## Deferred (Phase 2/3 per spec)

Prompt refiner (Stage 1) + clarifying questions, multi-provider UI picker,
retry-with-variations, filesystem session persistence, measurement tool, template library.

## Notes / decisions

- Keeping Preact (already set up) rather than switching to React/vanilla.
- Backend consolidated onto `.venv-poc`; Makefile PYTHON points there.
- `poc_cadquery_chat.py` kept as reference until Task 1 ports it, then removed.
