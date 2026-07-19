# EasyCAD Spec 9 Tasks

Status legend: `[x]` done, `[~]` in progress, `[ ]` not started.

Rollout order follows spec9.md: A → B → {C, D in parallel}. Nothing here
touches the constraint-solver idea from the design discussion — deferred,
not in scope.

## Milestone 1: Feature Roster (Part A)

- [x] Add `SpecificationFeature.omission_reason: str | None = None` to `app/models.py`.
- [x] Change `app/minimal_model.py:_omit` to set `omission_reason` instead of mangling `label`.
- [x] Add `app/feature_roster.py` with `FeatureRosterEntry` and `feature_roster(draft, values) -> list[FeatureRosterEntry]`, reusing `resolved_feature_extent`.
- [x] Wire into `app/main.py:_model_response`: compute `values = resolve_dimension_values(draft)`, call `feature_roster`, add `"features"` to the response.
- [x] Frontend: `FeatureRosterEntry`/`ModelResponse.features` in `frontend/src/types.ts`; store `features` in `frontend/src/store.ts`.
- [x] Frontend: roster list in `Workspace` — alias + label; unsupported rows show `omission_reason`, visually muted, not hidden.
- [x] Unit tests: `_omit` sets `omission_reason` and leaves `label` untouched; `feature_roster` returns correct extent/status/omission_reason per entry, including a non-computable feature (`extent: None`).

Acceptance: SPEC 9 Part A — omitted features are visible with their reason; no test currently locking the old label-suffix format breaks.

## Milestone 2: Stable Feature Aliasing (Part B)

- [x] `featureAliases: Record<string, string>` in the zustand store, assigned on `setModelResponse` for any roster id not yet a key; `A`-`Z` then `AA`, `AB`, … ; never reassigned or GC'd within a session.
- [x] Display the alias next to each roster row (Part A's list).

Acceptance: SPEC 9 Part B — a feature keeps the same letter across refine calls for the life of the browser session, confirmed by clicking through two refine rounds.

## Milestone 3: Selection Sync (Part C)

- [x] Per roster entry with a non-null `extent`: invisible filled `Mesh` proxy (raycast target) + visible `LineSegments`/`EdgesGeometry` wireframe outline, both tagged with the feature id, added to the `ModelViewer` scene.
- [x] Hover/click a roster row → highlight the matching overlay; click a roster row → `selectedId` set, camera unchanged.
- [x] Raycast click in the 3D view against the proxy meshes → resolve feature id → highlight + scroll matching roster row into view.
- [x] Entries with `extent: None` are list-only (no overlay), still selectable from the list.

Acceptance: SPEC 9 Part C — clicking a roster row highlights the right box in the 3D view and vice versa; entries with no extent are still selectable from the list only. Confirmed live (screenshot) with a real upload: selecting "Vertical Upright Block" highlighted the matching 3D box in amber.

## Milestone 4: Scoped Refine (Part D)

- [x] Add `referenced_feature_ids: list[str] = []` to `PromptRequest` in `app/main.py`.
- [x] `refine_model`: defensive filter `id in {f.id for f in request.specification.features}` before building `user_inputs`; pass surviving ids through.
- [x] `app/ai_generation.py`: conditional prompt sentence naming referenced ids/labels when the list is non-empty, alongside the existing `freeform_instruction` block.
- [x] Frontend: plain `<textarea>` with caret-relative `@` trigger regex, dropdown over current roster filtered by alias/label, inserts `@<alias> ` token (not the label) on selection.
- [x] Frontend: at submit time, regex-scan the prompt for `@<token>` candidates, keep only ones matching a live alias, map to ids, dedupe into `referenced_feature_ids`.

Acceptance: SPEC 9 Part D — a prompt containing `@B ...` resolves to `referenced_feature_ids: ["<B's real id>"]` client-side before the request is sent; unscoped prompts (no `@mention`) behave exactly as before this spec. Confirmed live end to end, including the model actually updating from a `@A make it 5mm taller` scoped prompt.

## Verification

- [x] Unit tests pass (`python -m unittest discover -s tests -t .`) — 10/10 green (1 opt-in E2E skipped by default).
- [x] Frontend unit tests pass (`npm test`, Node's built-in test runner, no new dependency) — `frontend/src/alias.test.ts` covers `nextAlias`.
- [x] `npm run build` (tsc --noEmit + vite build) passes.
- [x] Real-provider E2E committed (`tests/test_e2e_scoped_refine.py`, opt-in via `EASYCAD_RUN_REAL_E2E=1`, no mocked network calls) — passed against the real provider.
- [x] Manual live pass via Playwright + a real upload (`fixtures/3.png`) and real provider calls: roster + aliases render correctly including an omitted feature's reason; clicking a roster row highlights the matching 3D overlay and vice versa (screenshotted); `@` opens the mention dropdown, inserts a clean `@<alias> ` token, and a scoped refine round-trips to an updated model with the roster intact. Found and fixed one real bug in this pass (see docs/AI_LEARNED.md 2026-07-18 caret-race entry).

### Post-implementation review round (2026-07-18)

A second manual review of the delivered implementation found two real bugs
and a test-coverage shortfall, fixed the same day (see docs/AI_LEARNED.md
"A stale minimal_body id..." and "Alias hygiene..." entries):

- [x] `fallback_draft`/`minimal_reliable_draft` could leave zero confirmed
  features when a stale `minimal_body`-id feature was already present but
  unsupported — fixed via a single `_insert_fallback_box` helper, regression
  test added.
- [x] Client-side `@mention` resolution filtered only against the
  never-shrinking `featureAliases` map, not the live roster — a hand-typed
  reference to a superseded round's letter could silently resolve to a dead
  id. Now filtered against `state.features` too, and the resolved `@alias`
  token is replaced with the feature's real label before the prompt is sent
  (confirmed via captured network payload, not just the displayed text).
- [x] spec9.md's own Verification item asked for a frontend alias-generation
  unit test and a real-provider E2E; the original pass had neither
  (Playwright verification was one-off and uncommitted). Both now exist and
  are green.
