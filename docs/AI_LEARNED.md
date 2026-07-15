## 2026-07-11 — Local CadQuery worker testing with uv

### Goal

Run and verify EasyCAD fixture preview/export generation locally without Docker.

### Golden path

1. Create the local venv with Python 3.11:
   `uv venv --python /opt/homebrew/bin/python3.11 .venv`
2. Install dependencies with a workspace-local uv cache:
   `UV_CACHE_DIR=.uv-cache uv pip install --python .venv/bin/python -r requirements.txt cadquery`
3. Force the backend runner to skip Docker and allow enough time for CadQuery startup:
   `CADQUERY_WORKER_IMAGE=easycad-no-docker CADQUERY_WORKER_TIMEOUT_SECONDS=180 XDG_CACHE_HOME=/Users/ma/repo/easycad/.cache PYTHONDONTWRITEBYTECODE=1 .venv/bin/python <smoke-script>`
4. Start the local app with Docker disabled:
   `CADQUERY_WORKER_IMAGE=easycad-no-docker CADQUERY_WORKER_TIMEOUT_SECONDS=180 .venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8852`

### Verification

- `curl -sS http://127.0.0.1:8852/api/health` returned `{"status":"ok",...}` while uvicorn was running.

### Failure pattern avoided

Local generation fails if the runner falls back to system Python without CadQuery, producing `worker_import: No module named 'cadquery'`. First local CadQuery startup can also exceed the previous hardcoded 35-second worker timeout.

### Ruled-out approaches

- Tried system Python 3.9; failed because `cadquery` was not installed and Python 3.9 is a poor target for current CadQuery wheels.
- Tried running `uv pip install` inside the sandbox; failed because uv panicked in macOS system-configuration access before dependency resolution.
- Tried Docker worker verification; rejected for this workflow because local testing without Docker was requested.

### Notes

When the sandbox blocks localhost binding, start uvicorn with approval outside the sandbox.

## 2026-07-12 — CadQuery text engraving API

### Goal

Add or debug generated CadQuery projects that need engraved or embossed text.

### Golden path

Use `Workplane.text(txt, fontsize, distance, combine=...)`:

- `combine="cut"` for engraved/recessed text. On a top face, pass negative distance into the solid, for example `.text(label, size, -depth, combine="cut")`.
- `combine="a"` for embossed/raised text.

Expose visible drawing text as normal EasyCAD parameters such as `text_content`, `text_mode`, and `text_size`, then read them from `PARAMETERS` in generated code.

For image-to-CAD tests, draw recessed text as an orthographic technical drawing, not as perspective artwork:

- show a top view with the text on the top face;
- show a section view with the recess depth;
- label that the result is for 3D printing and must be solid printable geometry, not a laser/engraver path.

### Diagnostic sequence

When recessed text output looks wrong, work through this sequence:

1. Inspect the input drawing first. If the text looks like a perspective label or overlay, redraw the input as orthographic top/front/section views.
2. Add explicit drawing notes: target is 3D printing, text is recessed into the top face, recess depth is a real solid cut.
3. Run the normal `/api/projects/generate` pipeline and save the response JSON.
4. Inspect model logs/response for `text_content`, `text_mode`, text depth, and generated CadQuery `.text(...)`.
5. Validate the generated CadQuery API usage against the installed CadQuery signature. For this project, `cut=` is invalid; use `combine="cut"`.
6. Export STL through the backend export endpoint, not a one-off script.
7. Compare resulting volume against the full block volume. Equal volume means the cut did not remove material; for top-face recessed text, change positive text distance to negative distance.
8. Add or update regression tests so the worker export proves the STL volume decreases.

### Verification

`make test` passed after adding unit tests that run the local CadQuery worker and export STL from projects using `.text(..., combine="cut")`.

A generated recessed-text block was confirmed by comparing volume:

- full 60 x 40 x 30 mm block volume is `72000 mm3`;
- generated STL volume was `71898.99 mm3`, proving material was actually removed.

### Failure pattern avoided

The old backend used `.text(..., cut=True, combine=True)`, but the local CadQuery version rejects `cut` with `Workplane.text() got an unexpected keyword argument 'cut'`. Positive cut distance on a top face can also leave the solid volume unchanged because the text extrusion is outside the part.

Perspective drawings where the text looks like a label on top of a cube are ambiguous: the model may interpret them as printed surface text or place the text on the wrong plane. Orthographic top/section views avoid that.

### Ruled-out approaches

- Tried the old backend `.text(..., cut=True, combine=True)` call in a worker-backed test; failed because this CadQuery version does not accept `cut`.
- Tried a perspective cube drawing with text rendered on the top face; rejected because the text looked like an overlaid label rather than a recessed feature in the plane.
- Tried positive text cut depth on the top face; rejected because the exported solid kept the full block volume.

### Notes

The response processing should stay simple: no separate registry/tool-call pipeline is needed just to support lettering.
## 2026-07-12 — Local capability regression with uv-managed environment

### Goal

Run the complete capability regression locally with the project environment and CadQuery worker, without Docker.

### Golden path

Run `make test-capabilities`. The target verifies the installed `uv` version, then runs
`tests.capability_regression` with `.venv/bin/python`, the interpreter managed for this project by `uv`.

The runner discovers every `tests/test_*.py` module and writes the grouped machine-readable result to
`artifacts/capability-summary.json`.

### Verification

`make test-capabilities` completed 133 tests successfully in 136.6 seconds. The generated summary reported
`passed`. A later architecture review found that the initial capability metrics reused expected IDs as predictions
and hardcoded successful exports and zero dimension errors, so those values did not verify product quality. The
report now emits `insufficient_evidence`, `metrics: null`, and the actual observation count until independent
provider and worker outcomes exist.

### Failure pattern avoided

On the sandboxed macOS environment, `uv run` can panic in the Rust `system-configuration` crate while creating a
dynamic-store object, before Python starts. Offline mode, an isolated cache, and cleared proxy variables do not
prevent that launcher failure.

### Ruled-out approaches

- Tried `uv run --python .venv/bin/python`; failed with `Attempted to create a NULL object` in `dynamic_store.rs`.
- Tried `uv run --offline --no-project` with `UV_OFFLINE=1` and a workspace cache; failed with the same panic.
- Tried clearing HTTP, HTTPS, and ALL proxy variables; failed with the same panic.

### Notes

The fallback does not use system Python: it invokes the existing project `.venv/bin/python` directly after
confirming `uv` is installed.

Do not infer capability quality from the regression command's process exit status. Read each capability's
`evaluation_status` and require observed outcomes before calculating gates.
## 2026-07-12 — Structured provider output at the trusted compiler boundary

### Goal

Keep real model variability from bypassing or crashing the Feature Graph to trusted compiler pipeline.

### Golden path

1. Prompt the planning model for parameters and Feature Graph operations only; explicitly forbid Python/CadQuery source.
2. Normalize only unambiguous provider variants such as geometry fields nested under an `implementation` object and
   expression parameters supplied in a string `value` field.
3. Convert prose implementations, undeclared expressions, missing profiles/patterns, and unsupported targets to
   explicit `unsupported` operations.
4. Recompile every accepted project from its Feature Graph before worker execution and ignore request `cad.source`.
5. Treat invalid plan or repair JSON as `needs_review`; save the response for replay and do not return 500 or execute fallback code.
6. Run `make test-e2e-real`, then replay saved responses offline as part of `make test-capabilities`.

### Verification

`make test-e2e-real` completed successfully against the configured providers. `make test-capabilities` then passed
95 tests, including 35 independent capability drawings and 70 STL/STEP variant exports. Shipped fixture smoke also
exported both formats successfully.

### Failure pattern avoided

DeepSeek responses varied between strict Feature Graph fields, geometry nested in an `implementation` object,
expression parameters stored in `value`, prose operations, missing profiles, and repair updates outside declared
`repaired_feature_ids`. Passing these directly to Pydantic or CadQuery caused schema exceptions and failed requests.

### Ruled-out approaches

- Tried requiring every real provider response to produce STL immediately; rejected because unsupported structured
  output must be a controlled review outcome, not an excuse for executable fallback.
- Tried interpreting arbitrary expression strings and prose geometry; rejected because that recreates an unsafe,
  nondeterministic programming interface.
- Tried treating transformed copies of one drawing as independent capability evidence; replaced with five
  geometrically distinct technical drawings per supported capability.

### Notes

Capability worker evidence and provider-quality evidence are separate. Both are now measured from five independent
drawings per supported capability; future fixture additions must record provider outcomes before changing support labels.

## 2026-07-13 — Compiler-owned operation catalogue and repair boundary

### Goal

Keep the planner prompt, provider normalization, and trusted compiler aligned when adding CAD primitives.

### Golden path

1. Register every canonical operation type and compatibility alias in `app.feature_compiler.COMPILER_OPERATION_TYPES`.
2. Generate the planner's allowed-type list from that registry; only canonical names are offered to the LLM.
3. Normalize accepted aliases to their canonical type before Feature Graph validation.
4. In repair responses allow parameters, placement, profile, pattern, status, and assumption only; reject changes to
   type, boolean operation, target, dependencies, and source feature IDs.
5. When a provider supplies a non-scalar parameter that the Feature Graph cannot represent, remove that value and
   retain the operation as `unsupported` with an explicit assumption rather than sending invalid data to Pydantic.

### Verification

`make test` passed 107 tests plus STL and STEP smoke exports on 2026-07-13. The suite covers a compiled cylinder,
catalogue alias normalization, rejected repair type/target replacements, and a provider `edges: ["front_edge"]`
response becoming reviewable instead of producing HTTP 422.

### Failure pattern avoided

The compiler and planner maintained separate hand-written operation lists, while a DeepSeek repair response replaced a
`fillet` with a `cylinder` and another plan sent `edges` as an array parameter. The former changed model semantics and
the latter failed Pydantic validation before the controlled review path.

### Ruled-out approaches

- Allowed repair responses to replace an operation type; rejected because a repair can silently change the Feature Graph's geometry.
- Kept non-scalar provider fields in an operation marked unsupported; rejected because Pydantic validates field types before status is used.

## 2026-07-15 — Full specification replanning after clarification

### Goal

Refresh a drawing specification after user clarification without re-running vision analysis.

### Golden path

1. Run vision analysis once at upload and retain it in the browser's `DraftSpecification` session.
2. Preserve every question-scoped user clarification in client state.
3. On validation, send the original vision analysis, prior specification, and all user inputs to the draft planner in one request.
4. Replace the browser specification with the returned complete `DraftSpecification`; do not apply a model-generated patch.
5. Keep normalization technical only, and return specification diagnostics to the UI instead of HTTP 422 for an incomplete draft.

### Verification

`make test` and `npm run build` passed after API tests verified that multiple question clarifications are sent in one complete replan with the original analysis available to the planner.

### Failure pattern avoided

Provider responses naturally return full dimensions and feature records, not the artificially narrow patch shape. Rejecting these responses with HTTP 422 prevents the user from continuing review.

### Ruled-out approaches

- Tried a constrained patch contract; rejected because providers returned new dimensions and complete feature updates that did not fit the patch schema.
- Tried one planner request per question; rejected because related answers must be interpreted together.

## 2026-07-15 — Strict placement schema for the draft planner

### Goal

Prevent a DeepSeek draft response from reaching Build with unsupported placement keys.

### Golden path

1. Model `SpecificationFeature.placement` as the strict `FeaturePlacement` model, rather than a generic dictionary.
2. Send `DraftSpecification.model_json_schema()` through a strict function call and force that function choice.
3. Keep the one-time vision analysis authoritative when validating the planner response: merge the response first, then set `analysis` from the original vision result.
4. Run `.venv/bin/python -m unittest tests.e2e_structured_output_comparison.StructuredOutputComparisonE2E.test_1_deepseek_strict_tool_writes_result` with network access.

### Verification

On 2026-07-15, DeepSeek returned HTTP 200 and the real-provider E2E test passed. The raw tool arguments used only permitted placement fields (`origin` and `offsets`); the test rejects `offset`.

### Failure pattern avoided

DeepSeek previously produced `placement.offset`, which passed review but failed the CAD FeaturePlacement validation at Build. A real strict-tool response also returned its own `analysis` object in a different shape; merging it after the vision result caused DraftSpecification validation to fail.

### Ruled-out approaches

- Tried only validating placement at Build; rejected because users discover the provider-format failure too late.
- Tried merging the planner result over the original vision analysis; rejected because model-produced analysis is not the authoritative one-time vision result.

## 2026-07-15 — Real accepted-specification path to STL

### Goal

Verify that accepting every proposal in a real drawing review reaches a buildable STL, not merely a valid review response.

### Golden path

1. Run `tests.e2e_real_user_specification_flow.RealUserSpecificationFlowE2E.test_accepting_all_proposals_builds_and_exports_stl` with provider keys and network access.
2. The test uploads `fixtures/3.png`, accepts every returned feature and assumption, replans, builds, and exports `artifacts/real-user-flow-bracket/model.stl`.
3. Keep the draft planner restricted to feature kinds expressible by `DraftSpecification`; give the provider exact compiler parameter and coordinate contracts.
4. Unwrap DeepSeek function-call responses that place the submitted draft under `parameters`.

### Verification

On 2026-07-15 the real test produced a 45,684-byte STL. Validation had five retained features and zero questions; Build returned `success`.

### Failure pattern avoided

DeepSeek function calls can return the complete draft nested in `parameters`; treating that wrapper as the draft silently created Pydantic defaults with an empty feature graph. The generic planner catalogue also advertised `extrude`, despite DraftSpecification having no profile field required by that compiler operation.

### Ruled-out approaches

- Tried relying on prompt text alone to preserve a full graph; rejected because the provider could still return an empty wrapped payload.
- Tried validating only the review state; rejected because incorrect cylinder plane/origin coordinates were exposed only by semantic STL build validation.

## 2026-07-15 — Contract-driven draft operations

### Goal

Keep the LLM draft schema, specification validation, and trusted CadQuery compiler aligned.

### Golden path

1. Add a compiler operation to `OPERATION_CONTRACTS`.
2. Let the registry generate its planner description and strict Draft feature schema.
3. Run `tests.test_specification`, `tests.test_feature_compiler`, and the real bracket user-flow E2E.

### Verification

The bracket flow reached `artifacts/real-user-flow-3/model.stl` (59,184 bytes) after build diagnostics were supplied as a `build_repair` clarification.

### Failure pattern avoided

A valid JSON draft can still omit CadQuery-required profile/pattern data or use unsupported parameter names.

### Ruled-out approaches

- Duplicating a separate LLM-only operation catalogue; rejected because it can diverge from the compiler.

## 2026-07-15 — Stateful DeepSeek draft-builder E2E

### Goal

Generate a reviewable specification through operation-specific tools, accept user proposals, and export a real STL for both a bracket and an M16 bolt.

### Golden path

1. Run `.venv/bin/python -m unittest -v tests.e2e_real_user_specification_flow` for `fixtures/3.png`.
2. Run `EASYCAD_USER_FLOW_IMAGE=fixtures/2.jpg .venv/bin/python -m unittest -v tests.e2e_real_user_specification_flow` for the bolt.
3. The DeepSeek loop must add dimensions before features, reject inline geometry expressions in feature tools, and use `finish_draft` after the draft is structurally complete.
4. Let review resolve assumptions/questions; do not require a fully confirmed draft at `finish_draft`.

### Verification

On 2026-07-15, both provider-backed flows passed: `artifacts/real-user-flow-3/model.stl` (47,584 bytes) and `artifacts/real-user-flow-2/model.stl` (71,284 bytes).

### Failure pattern avoided

DeepSeek may use dotted `critical_fields` such as `parameters.distance` and `profile.points`; validation must recognize those structured paths. A modifier's `target` names the feature, while `placement.reference` is a CadQuery edge selector and must not repeat a feature ID.

### Ruled-out approaches

- Tried requiring `validate_specification` at `finish_draft`; failed because legitimate assumed values and questions are intentionally unresolved before user review.
- Tried allowing inline values such as `overall_width/2` in feature coordinates; failed because executable feature fields accept only numbers or declared dimension IDs.
