# EasyCAD Spec Tasks

Status legend: `[x]` done, `[~]` in progress, `[ ]` not started.

## Milestone 1 — Generated-code fixture project

- [x] Universal `CADProject` schema with parameter provenance and generated source.
- [x] Static CadQuery source validator.
- [x] Isolated worker protocol for STL/STEP artifacts.
- [x] Local CadQuery execution path using `uv` environment.
- [x] Fixture projects for bracket and bolt.
- [x] Three.js STL preview and parameter editor.
- [x] STEP/STL export.
- [x] PY/JSON export.

## Milestone 2 — AI-generated code

- [x] `POST /api/projects/generate` upload endpoint.
- [x] OpenRouter drawing-analysis call scaffold.
- [x] DeepSeek CAD-plan call scaffold.
- [x] AI JSON normalization into `CADProject`.
- [x] Frontend upload + instruction controls.
- [x] Project-level validation beyond AST.
- [x] Live provider verification against fixture drawings.
- [x] Prompt/schema hardening from live failures.
- [ ] Save Feature Graph project JSON from UI and reload it without AI.

## Milestone 3 — Repair loop

- [x] `/api/projects/repair` endpoint.
- [x] Generation history model.
- [x] Automatic repair on AST validation failures.
- [x] Automatic repair on worker execution/export failures.
- [x] Two-attempt repair limit.
- [x] Frontend failure details and repair action.

## Milestone 4 — Structured Feature Graph

- [x] Define Pydantic models for feature operations, targets, profiles, placement, patterns, evidence, confidence, and coverage status.
- [x] Add stable feature IDs and relationships to drawing-analysis output.
- [x] Extend the vision prompt and JSON normalization for the strict feature inventory.
- [x] Store the original drawing reference for completeness and render-comparison stages.
- [x] Map every analysis feature to a Feature Graph operation or an explicit `approximated`, `unresolved`, or `unsupported` state.
- [x] Reject silent omission of high-confidence recognized features.
- [x] Persist the Feature Graph and coverage report in project JSON.
- [x] Add recorded LLM fixtures for repeated cuts, ribs, shells, and perforations.

Milestone exit: a perforated rib can be represented without classifying the complete part or generating CadQuery code.

Acceptance checklist:

- [x] `M4-AC1` strict Feature Graph schema accepts valid fixtures and rejects malformed operations.
- [x] `M4-AC2` feature IDs and target relationships are deterministic and valid.
- [x] `M4-AC3` every high-confidence feature has an explicit coverage state.
- [x] `M4-AC4` project save/load preserves the complete Feature Graph without AI calls.

## Milestone 5 — Trusted Feature Compiler

- [x] Compile additive/subtractive extrusions and revolutions from Feature Graph operations.
- [x] Compile holes, counterbores, countersinks, slots, and pockets.
- [x] Compile ribs and gussets with explicit host and thickness.
- [x] Compile fillets, chamfers, shells, mirrors, and planar text.
- [x] Compile linear and polar patterns with count, pitch, axis, and margins.
- [x] Compile perforation patterns on a declared planar rib face.
- [x] Preserve operation IDs in generated source stages and worker diagnostics.
- [x] Superseded: generated-CadQuery fallback removed by Milestone 10.
- [x] Superseded: fallback feature-ID declarations are no longer part of the runtime.
- [x] Add compiler tests for parameter overrides and STL/STEP export.

Milestone exit: a body with two ribs and repeated through-perforations is generated and exported using only generic feature operations.

Acceptance checklist:

- [x] `M5-AC1` generic perforated-rib fixture exports valid STL and STEP.
- [x] `M5-AC2` parameter overrides measurably update all declared dimensions and pattern values.
- [x] `M5-AC3` compiler or worker failures identify the Feature Graph operation ID.
- [x] `M5-AC4` unknown operations become explicit `unsupported`; executable fallback is forbidden.

## Milestone 6 — Feature-Preserving Repair

- [x] Add declared feature coverage to every whole-model stabilizer.
- [x] Prevent a stabilizer from running when it would omit additional recognized features.
- [x] Replace whole-model stabilization with operation-level repair where compiler support exists.
- [x] Include missing and mismatched feature IDs in repair prompts.
- [x] Preserve unaffected Feature Graph operations during repair.
- [x] Mark the project `needs_review` when repair cannot preserve high-confidence features.
- [x] Add a regression test proving an L-bracket stabilizer cannot remove rib perforations.

Milestone exit: stabilization and automatic repair cannot silently reduce feature coverage.

Acceptance checklist:

- [x] `M6-AC1` an L-bracket stabilizer cannot remove additional perforation features.
- [x] `M6-AC2` repair changes only the failed operation and required dependencies.
- [x] `M6-AC3` unrepairable high-confidence features produce `needs_review`.
- [x] `M6-AC4` repair history records operation and coverage changes.

## Milestone 7 — Semantic Geometry Verification

- [x] Extend worker metadata with feature-oriented measurements where reliable.
- [x] Validate expected hole and pocket counts for supported operations.
- [x] Validate repeated-feature count, pitch, and edge margins.
- [x] Validate that subtractive features reduce material and additive features add material.
- [x] Add printable-solid checks for minimum wall/rib thickness and disconnected bodies.
- [x] Link semantic validation errors to Feature Graph operation IDs.
- [x] Store semantic verification status separately from syntax and execution status.
- [x] Add a negative fixture whose bounding box is correct but perforations are missing.

Milestone exit: a model with omitted perforations fails semantic verification even if it exports successfully and has the correct bounding box.

Acceptance checklist:

- [x] `M7-AC1` semantic verification distinguishes complete and missing-perforation models with identical bounds.
- [x] `M7-AC2` count, diameter, pitch, and margins meet declared measurement tolerances.
- [x] `M7-AC3` additive, subtractive, and no-op booleans are distinguished.
- [x] `M7-AC4` syntax, execution, and semantic statuses are independent.
- [x] `M7-AC5` printable-solid constraint fixtures produce the expected result.

## Milestone 8 — Render Comparison And Targeted Correction

- [x] Render front, top, right, and isometric images from the generated solid.
- [x] Send source drawing views, generated renders, and feature IDs to the comparison model.
- [x] Return feature-linked missing, extra, misplaced, and dimension-mismatch findings.
- [x] Keep initial comparison advisory and show findings in the frontend.
- [x] Store render artifacts and comparison results in project history.
- [x] Add targeted correction for high-confidence findings tied to one operation.
- [x] Prevent visual repair from replacing unrelated successful operations.

Milestone exit: the comparison stage identifies a missing rib perforation and points to its Feature Graph operation without rebuilding the complete model.

Acceptance checklist:

- [x] `M8-AC1` four distinct non-blank render artifacts are produced and stored.
- [x] `M8-AC2` comparison identifies missing, misplaced, and extra feature variants by feature ID.
- [x] `M8-AC3` advisory comparison does not mutate project geometry.
- [x] `M8-AC4` enabled visual repair changes only the identified operation and is reverified.

## Milestone 9 — Capability Evaluation

- [x] Define capability labels: `supported`, `experimental`, and `unsupported`.
- [x] Build feature fixtures for ribs, linear perforations, polar perforations, slots, pockets, shells, text, sweeps, and lofts.
- [x] Record real provider responses after each verified fixture run.
- [x] Replay recorded responses in deterministic unit and integration tests.
- [x] Verify both STL and STEP for every supported capability fixture.
- [x] Track feature precision, feature recall, valid-solid rate, and dimension error separately.
- [x] Surface approximated, unresolved, and unsupported features before export.

Milestone exit: support claims are based on measured feature capabilities rather than successful processing of a small set of part-family fixtures.

Acceptance checklist:

- [ ] `M9-AC1` every supported capability has at least five geometrically representative source drawings.
- [ ] `M9-AC2` supported capabilities meet gates calculated from observed provider and worker results.
- [x] `M9-AC3` sanitized real-provider recordings replay deterministically without network access.
- [x] `M9-AC4` API and UI expose every capability and coverage status before export.
- [x] `M9-AC5` one documented local `uv` command runs the complete capability regression suite without Docker.

## Milestone 10 — Trusted-Pipeline Cleanup

- [x] Make the Feature Graph and trusted compiler the canonical architecture in the specification.
- [x] Remove automatic Docker detection and Docker worker execution.
- [x] Remove generated-CadQuery fallback and mark unsupported operations for review.
- [x] Remove executable source from model generation and repair contracts.
- [x] Remove unreachable whole-part stabilizers and generated-source sanitization.
- [x] Remove PY export and source-oriented frontend controls.
- [x] Restrict source validation to backend-compiled invariants; do not describe it as render validation.
- [x] Remove synthetic capability metrics and report insufficient evidence until observed outcomes exist.
- [x] Run the complete local regression suite without Docker.

Milestone exit: every supported model is produced from validated Feature Graph operations by trusted backend code;
no request path executes model-generated Python or changes runtime because a Docker image happens to exist.

## Validation And Tests

- [x] `make test` local test runner.
- [x] Unit tests for project/source validation.
- [~] Unit tests for expression evaluation.
- [~] Security tests for forbidden generated-code samples.
- [x] Unit tests for AI plan normalization.
- [x] Unit tests for mocked repair flow.
- [x] Unit tests for automatic repair loop.
- [x] Recorded real LLM fixture replay tests.
- [x] Integration smoke for fixture STL/STEP generation.
- [x] Real provider e2e test for fixture images.
- [ ] HTTP smoke for upload inspect and export routes.

## Security Hardening

- [ ] Clamp worker timeout config to an allowed range.
- [ ] Sanitize provider error bodies before returning to UI.
- [ ] Add decompression-bomb protection for image uploads.
- [x] Ensure source code references only declared parameters.
- [x] Ensure parameter ids are valid `snake_case`.
- [x] Ensure number parameter `min <= value <= max`.
