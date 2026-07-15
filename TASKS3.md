# EasyCAD Spec 3 Tasks

Status legend: `[x]` done, `[~]` in progress, `[ ]` not started.

## Milestone 1: Domain Model

- [x] Add Pydantic models for `DraftSpecification`, dimensions, features, assumptions, questions, and 2D annotations.
- [x] Define statuses, critical-field rules, stable IDs, and patch schema.
- [x] Add deterministic DraftSpecification validation with field-linked diagnostics.
- [x] Add tests for valid, incomplete, conflicted, and cyclic-expression specifications.

Acceptance: a specification can represent confirmed values, blockers, assumptions, and annotations without a Feature Graph.

## Milestone 2: AI Extraction And Planning

- [x] Change vision output normalization to observations only; remove direct CAD-plan ownership from this stage.
- [x] Add planner prompt and normalization for `DraftSpecification`.
- [x] Require evidence, confidence, criticality, and alternatives for unresolved values.
- [x] Add constrained clarification-patch prompt for user free text.
- [ ] Record real provider responses and add deterministic fixture replays for complete and incomplete drawings.

Acceptance: an ambiguous drawing yields questions and assumptions, not a guessed executable model.

## Milestone 3: Specification API

- [x] Add upload endpoint returning `DraftSpecification` and source-image reference.
- [x] Add endpoint to validate structured specification edits without an LLM.
- [x] Add endpoint to apply structured edits and optional free-text clarification.
- [x] Return field IDs, alternatives, and source-linked diagnostics in all validation responses.
- [x] Add API tests proving structured edits do not call an LLM and free text produces only a constrained patch.

Acceptance: the API supports the complete clarification loop before CAD generation.

## Milestone 4: Review UI And 2D Preview

- [x] Replace immediate upload generation with the DraftSpecification review screen.
- [x] Render deterministic 2D annotations over the source image.
- [x] Build one editable form showing all blockers, assumptions, confirmed fields, alternatives, and free text.
- [x] Disable `Build 3D` until specification validation passes.
- [x] Keep the form available after preview; edits invalidate the prior build.
- [x] Add frontend markup and interaction tests for blockers and rebuild state.

Acceptance: a user can resolve all critical gaps in one screen and understand why 3D is blocked.

## Milestone 5: Specification-To-Feature-Graph Compiler

- [x] Implement deterministic conversion from confirmed specification to Feature Graph.
- [x] Map conversion errors back to DraftSpecification IDs.
- [x] Reuse the compiler capability registry; unsupported features remain explicit blockers.
- [ ] Add a clarified open-box fixture based on `a3_open_box_from_spec.stl` and verify graph, bbox, one solid, and STL.
- [ ] Add negative tests for missing feature direction, target, depth, and placement.

Acceptance: a fully confirmed specification generates the existing trusted Feature Graph without an LLM CAD plan.

## Milestone 6: Build, Refinement, And Export

- [x] Replace direct generate response with explicit `Build 3D` endpoint from DraftSpecification.
- [x] Preserve existing worker, semantic geometry, render, and export validation.
- [x] Convert worker/semantic failures to specification-linked review diagnostics.
- [x] Rebuild from an edited specification without re-running image analysis.
- [x] Add end-to-end tests for incomplete -> clarified -> validated -> STL and post-preview refinement -> rebuilt STL.

Acceptance: only a validated specification can produce preview, STL, or STEP.

## Milestone 7: Remove Legacy Repair Pipeline

- [x] Remove `/api/projects/repair`, UI controls, and client calls.
- [x] Remove auto-repair loop and repair-specific generation history.
- [x] Remove `repair_project`, `plan_repair`, `apply_repair_plan`, prompts, fixtures, and tests.
- [x] Remove repair Make targets and stale documentation.
- [x] Run the complete unit, smoke, capability, and specification E2E suites.

Acceptance: repository search finds no executable legacy repair path, and all current generation flows begin with DraftSpecification.
