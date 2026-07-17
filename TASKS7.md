# EasyCAD Spec 7 Tasks

Status legend: `[x]` done, `[~]` in progress, `[ ]` not started.

Milestone order follows SPEC 7 Sequencing: A → B1 → D1/D2 → B2 → D3/D4 → C.
Every milestone is independently shippable; nothing waits on SPEC 8 except
the Milestone 8 default flip.

## Milestone 1: Lint Foundations (A2)

- [ ] Extract the non-raising partial resolver `resolve_dimension_values(draft) -> (values, unresolved_ids)` from `validate_specification`'s value loop; keep the raising build path unchanged.
- [ ] Add `app/draft_lint.py` with `LintIssue` (`rule`, `issue_id`, `severity`, `feature_ids`, `message`, optional `suggestion`) and the `issue_id` scheme `<rule>@<sorted feature_ids joined by '+'>[@<qualifier>]`, qualifier mandatory for multi-finding rules.
- [ ] Add `resolved_feature_extent(feature, values) -> Extents | None` for box (plane XY), cylinder (any plane), hole/through_hole, pocket/slot; `None` for all other types; tolerance 0.5 mm.
- [ ] Implement the footprint overlap fraction on the plane perpendicular to the cut's extrusion axis.
- [ ] Implement rules: `cut_misses_target` (error), `cut_mostly_outside_target` (warning, threshold 0.25), `additive_disconnected` (error, per-target, root = first feature), `negative_origin` (error, per-coordinate qualifier), `overall_extent_mismatch` (warning, per-axis qualifier), `through_hole_short` (warning).
- [ ] Add `SpecificationDimension.role: overall_x | overall_y | overall_z | null` to the model and tool schema; axis mapping prefers `role`, falls back to the `_number_parameter` id lists, skips unmapped axes.
- [ ] Unit tests: mostly-outside cut → warning; through-cut past target thickness and boundary groove centred on a face → no cut-placement issue; per-axis and per-coordinate `issue_id` uniqueness; unresolved values → `unevaluated_feature_ids`, never a raise.

Acceptance: SPEC 7 criteria 1, 2, 5, and the `issue_id` half of 11 pass as provider-free unit tests.

## Milestone 2: Analysis Coverage And Exclusion Record (A1)

- [ ] Enforce the identifier contract in `_normalize_analysis_features`: synthesized `feature_<index>` ids, `_<n>` dedup suffixes, uniqueness guaranteed; same path for recorded/replayed analyses.
- [ ] Add `SpecificationFeature.source_feature_ids` to the model and planner tool schema; require it in the planner prompt.
- [ ] Carry `source_feature_ids` through `project_from_specification` into `FeatureOperation.source_feature_ids`; round-trip test proving the field survives specification → feature graph without loss.
- [ ] Implement review-time coverage (clauses 1–3) as a `finish_draft` rejection listing uncovered analysis ids.
- [ ] Add `DraftSpecification.exclusions: [{feature_id, source_feature_ids, reason}]` with untrusted-input re-validation on every endpoint that accepts a client specification (subset of normalized analysis ids; no live `feature_id`; non-empty `reason`; violations 422).
- [ ] Implement the build-gate tightening: clauses 1/2 (any feature status, including `unsupported`) or an exclusion entry; violations are 422 with stage `analysis_coverage`.
- [ ] Unit tests: missing/duplicate analysis ids normalize deterministically; coverage rejection in a recorded tool-call replay; accepted-assumption-only coverage rejected at build; exclusion-covered draft builds; forged exclusion entries rejected on validate and build.

Acceptance: SPEC 7 criteria 3, 4, and the exclusion-validation half of 12 pass without a provider.

## Milestone 3: Lint Integration And Run Metrics (A3, A4)

- [ ] Reject `finish_draft` on lint errors over evaluable features; unevaluated features never block the planner.
- [ ] Add `lint: {issues, unevaluated_feature_ids}` to `/api/specifications/analyze` and `/api/specifications/validate` responses.
- [ ] Gate `/api/specifications/build` on lint errors (422, stage `draft_lint`) before compile; non-empty `unevaluated_feature_ids` at build is itself a diagnostic.
- [ ] Add `app/run_metrics.py` writing one `logs/planner_runs.jsonl` record on every `_run_draft_builder` exit path, with turns, tool calls/errors, finish-rejection reasons, lint counts, duration.
- [ ] Map every exit path to exactly one SPEC 7 A4 outcome: `completed` (finish accepted), `turn_limit` (cap raised), `provider_error` (transport exception, HTTP ≥ 400, malformed/unparseable response), `planner_stopped` (response without tool calls); invalid JSON tool arguments increment `tool_errors` and are not an exit.
- [ ] Tests: HTTP tests for the three integration points; one-record-exactly tests per outcome class — completed, turn-limit (recorded replay), provider error (mocked HTTP 500), malformed response (mocked garbage payload → `provider_error`), and `planner_stopped` (mocked tool-call-free response) — each asserting no duplicate record is written.

Acceptance: SPEC 7 criteria 1 (endpoint half) and 8 pass — criterion 8 covered for every outcome class, not only turn-limit; `make test-unit` stays green.

## Milestone 4: Schematic Views (B1)

- [ ] Add `app/draft_preview.py` `draft_schematic(draft) -> {front, top, right}` SVGs from resolved extents; additive filled, cuts dashed; every element carries its feature id.
- [ ] Implement the non-computable fallback: hatched 10-unit square at a resolved origin, per-view `approximate` legend otherwise, both in sorted feature-id order, overlaps permitted.
- [ ] Add `POST /api/specifications/schematic` returning `{views, lint}`; no worker, no LLM.
- [ ] Frontend: `Schematic` tab in `DrawingPanel`; two-way hover/selection highlight via the existing `selectedId` store field.
- [ ] Tests: three SVGs for both recorded fixture specifications; no subprocess spawned; byte-identical SVGs on consecutive calls, including a non-computable feature.

Acceptance: SPEC 7 criterion 6 passes; schematic p95 < 100 ms on the recorded fixtures.

## Milestone 5: Review Plan And Structured Answers (D1, D2)

- [ ] Compute `review_plan: [{tier, item_type, item_id, reason}]` server-side (tiers 1–5 per the SPEC 7 table); `item_type` extends the identity enum with `lint_issue` (keyed by `issue_id`) and `exclusion` (keyed by the excluded `feature_id`).
- [ ] Include exclusion entries in tier 4 as omission warnings carrying their recorded `reason`.
- [ ] Include `review_plan` in `analyze` and `validate` responses.
- [ ] Frontend: `ReviewWorkspace` renders sections in plan order; one-click accept-all for tier 3.
- [ ] Frontend: render exclusion entries as omission warnings (id + reason) in the tier-4 section.
- [ ] Frontend: `QuestionRow` renders `alternatives` as choice buttons with free text as fallback only.
- [ ] Tests: every unresolved item planned exactly once; distinct entries for same-rule different-features and same-features different-qualifier lint findings; an exclusion entry appears in tier 4 with its reason; markup test for section order.

Acceptance: SPEC 7 criterion 11 passes end to end.

## Milestone 6: Draft Preview Build (B2)

- [ ] Add `?mode=draft` to `/api/specifications/build`: deterministic front half unchanged; skip only `validate_generation_geometry`, `validate_feature_measurements`, `validate_feature_coverage`.
- [ ] Return `status: "draft_preview"` with renders, bounding box, and per-feature measurements; set `generation.semantic_status = "draft_preview"`; compile/worker failures keep the normal error shape.
- [ ] Enforce non-exportability in `/api/projects/export`: 409 unless `generation.semantic_status == "success"`.
- [ ] Frontend: `Preview` button in `ReviewWorkspace` on every round.
- [ ] Tests: draft render for a spec failing overall-extent validation; worker failure shape in draft mode; export 409 for a `draft_preview` project.

Acceptance: SPEC 7 criterion 7 passes.

## Milestone 7: Exclusion And Apply-Fix (D3, D4)

- [ ] Add `excluded_feature_ids` to `SpecificationEditRequest`; treat as supersession, record in `DraftSpecification.exclusions`, append the deterministic prompt line.
- [ ] Implement the dangling-reference policy server-side before replan: dependent `target` → 422 with dependents; moot questions removed; annotation links pruned (annotation removed when its primary `field_id` is excluded); assumption `affected_ids` pruned, assumption retained.
- [ ] Add `feature_field_edits` to `SpecificationEditRequest`: whitelist `placement.origin[i]` and numeric parameters, plain numbers only, atomic all-or-nothing with a 422 listing every invalid edit, deep-copy application, response returns updated specification plus fresh lint, zero planner calls on edits-only requests.
- [ ] Frontend: `Exclude` action on feature rows; `Apply` button on suggestion-bearing lint issues.
- [ ] Tests: prose-free exclusion; dependent rejection; `review_reference_issues` clean after exclusion; atomic rejection of mixed valid/invalid edits; suggestion applied with zero LLM calls.

Acceptance: SPEC 7 criteria 12 and 13 pass.

## Milestone 8: Incremental Replan (C)

- [ ] Add `EASYCAD_REPLAN_MODE = full | incremental` (default `full`) and `MAX_INCREMENTAL_REPLAN_TURNS = 16`.
- [ ] Implement `DraftBuilder.seed(previous, locked_items)` with typed `{item_type, id}` locks (confirmed/accepted minus clarification-superseded minus excluded); `_clarification_superseded_ids` returns typed identities in this mode.
- [ ] Enforce locks at call time: `add_*` on a locked identity returns `ok=false` naming the clarification requirement.
- [ ] Add `remove_item {item_type, id, reason}` (unlocked only; reason recorded in run metrics) and `resolve_question {id}` tools; incremental finish contract (answered questions resolved or replaced, no lint errors) with no snapshot comparison and no `required_*_ids` check in this path.
- [ ] Build the incremental context payload: user inputs, open and superseded items in full, locked items as an identity summary only.
- [ ] Write the delta-editing replan prompt variant.
- [ ] Tests: recorded a3 rim clarification replay — locked items byte-identical, superseded rim replaced or removed, ≤ 16 turns, no snapshot-equality code executed; lock-collision test for a question and feature sharing an id.
- [ ] Flip the default to `incremental` after a SPEC 8 `--gate replan_mode` run passes on every case (blocked on SPEC 8).

Acceptance: SPEC 7 criteria 9 and 10 pass; the default flip lands only with the SPEC 8 gate report attached.

## Milestone 9: Language And Guardrails

- [ ] Add the SPEC 7 terms to CONTEXT.md: Lint issue, Review plan, Locked item, Item identity, Draft preview.
- [ ] Extend the prompt-content guard test: no fixture filename, named test part, or hard-coded fixture coordinate in any planner prompt, including the new coverage/exclusion prompt lines.
- [ ] Document the warning→error promotion criterion (`cut_mostly_outside_target`, `overall_extent_mismatch`, `through_hole_short` promote only after SPEC 8 shows zero false positives on the corpus).

Acceptance: SPEC 7 criterion 14 passes; CONTEXT.md and spec stay in sync.
