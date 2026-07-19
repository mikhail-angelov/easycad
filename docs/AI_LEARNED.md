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

## 2026-07-15 — Interactive STL preview in the review panel

### Goal

Show the generated model beside the uploaded drawing without adding another model-generation endpoint.

### Golden path

1. Load the built `Project` into a Three.js `STLLoader` from `POST /api/projects/preview`.
2. Put the viewer behind a `3D model` tab in the existing drawing panel and automatically select it after Build succeeds.
3. Use `OrbitControls` for rotation, wheel zoom, and panning.
4. Run `npm run build` after frontend changes; it performs both TypeScript checking and Vite production build.

### Verification

On 2026-07-15, `npm run build` completed successfully after installing `three` and `@types/three`.

### Failure pattern avoided

The repository has no retained legacy 3D viewer to reuse. A static render artifact is not an interactive STL view; load the existing preview endpoint instead.

### Ruled-out approaches

- Tried finding an old OrbitControls/STL viewer in git history; none was present in the application history.

## 2026-07-16 — Verify the round-end bracket before STL export

### Goal

Prevent fixture 3 from producing a valid STL whose R30 end and concentric hole are placed on the side edge instead of the end face.

### Golden path

1. In the draft-planner coordinate contract, declare `base_end_center_y = base_width / 2` as a derived dimension.
2. Use the dimension ID in both the end-cylinder and concentric through-hole origins: `[straight_length, base_end_center_y, 0]`.
3. Make the real fixture-3 flow resolve those origins and assert `[48, 30, 0]` before Build.
4. Build the checked specification and export its STL.

### Verification

On 2026-07-16, a real DeepSeek response for `fixtures/3.png` produced a derived centre dimension and both features resolved to `[48, 30, 0]`. The checked response compiled and exported a 45,484-byte STL.

### Failure pattern avoided

An STL can be syntactically and semantically buildable while an additive end cylinder and its through-hole use the full width as Y, placing the round end on a side edge.

### Ruled-out approaches

- Tried asserting only that Build returned `success`; it missed the side-edge geometry.
- Tried allowing `width/2` directly in `placement.origin`; tool contracts reject executable expressions there.

## 2026-07-16 — Map the fixture-3 top groove to the upright end face

### Goal

Keep the R12 top groove in `fixtures/3.png` centred on the upright end face instead of cutting a side edge.

### Golden path

1. Use the drawing's coordinate convention: upright thickness 28 is X depth; width 60 is Y.
2. Declare `groove_center_y = overall_width / 2`.
3. Compile the groove as a cylinder cut on plane `YZ` with origin `[0, groove_center_y, overall_height]` and `height=upright_thickness`.
4. In the real user-flow fixture, resolve and assert plane, origin, and height before Build.

### Verification

On 2026-07-16, a real DeepSeek tool loop returned `plane=YZ`, origin `[0, groove_center_y, overall_height]`, and height `upright_thickness`; the resulting fixture-3 STL was exported successfully.

### Failure pattern avoided

Using plane `XZ` makes the cylinder run through the 60 mm width instead of the 28 mm upright depth, producing a groove on a side edge or in a corner even though the CAD build succeeds.

### Ruled-out approaches

- Tried treating the visible top groove as an XZ/Y-axis cut; the drawing shows its semicircle on the upright end face, which requires YZ/X-axis extrusion.

## 2026-07-16 — Keep legacy provider adapters out of runtime modules

### Goal

Avoid keeping obsolete planner-payload compatibility paths in the production image-to-draft module.

### Golden path

Move compatibility normalization for recorded provider payloads into `tests/provider_payloads.py`. Build test projects directly with public models and `compile_project_feature_graph`, rather than importing a legacy `project_from_plan` adapter from `app/ai_generation.py`.

### Verification

`.venv/bin/python -m unittest -q tests.test_http_api tests.test_source_images tests.test_specification tests.test_validation` passed (55 tests).

### Failure pattern avoided

Test-only compatibility code in the application module leaves obsolete direct-planner behavior visible as a production API and makes the active draft pipeline harder to audit.

### Ruled-out approaches

- Kept test projects on `project_from_plan`; rejected because that preserved the entire legacy adapter chain in runtime code.

## 2026-07-17 — Lint follows the explicit feature target

### Goal

Keep pre-build extent lint aligned with the draft specification's explicit feature graph.

### Golden path

1. Resolve primitive extents in feature order.
2. Evaluate an additive or cut feature against the primitive extent named by its `target`.
3. Point a cut at the additive feature whose material it actually intersects; for example, the L-bracket top groove targets `upright`.
4. Check `negative_origin` against the resolved placement origin rather than the primitive bounding box.
5. Replay `tests.test_l_bracket_groove_replay` and run the provider-free draft-lint tests.

### Verification

On 2026-07-17, `tests.test_draft_lint` and `tests.test_l_bracket_groove_replay` passed, including a sibling-overlap regression and the YZ top-groove cut targeting the additive upright.

### Failure pattern avoided

Using the accumulated union of sibling features can hide an incorrect `target`: a feature may intersect a sibling while missing the primitive it explicitly names.

### Ruled-out approaches

- Tried comparing against the accumulated connected-body union; rejected because sibling overlap made disconnected features appear connected and obscured incorrect targets.
- Tried targeting the L-bracket groove at `base_body`; failed because the groove intersects the upright primitive, not the base primitive.

## 2026-07-17 — Stop full-replan alias loops early

### Goal

Prevent a planner from spending the full turn budget creating `feature_vN` aliases after a confirmed feature must be restored with its original ID.

### Golden path

1. On a full-replan `finish_draft` rejection, retain the expected source-feature IDs for each changed confirmed feature.
2. Reject a later `add_*` call that uses those same source IDs under a different feature ID, and state the exact replacement ID.
3. Stop the run as `planner_stopped` after three identical `finish_draft` rejections.

### Verification

`tests.test_run_metrics.RunMetricsTests.test_full_replan_stops_alias_loop_without_waiting_for_turn_cap` replays the alias pattern and stops after three turns; `make test` passed with 172 tests and fixture smoke generation.

### Failure pattern avoided

The planner repeatedly created `central_bore_feat_vN` while `complete_replan` required a replacement of `central_bore_feat`, consuming 21 turns and 20 tool errors.

### Ruled-out approaches

- Relied on the 48-turn global cap; rejected because it only terminates the loop after unnecessary provider calls.
- Tried deriving `negative_origin` from the feature bounding-box minimum; failed because centered circular profiles legitimately extend below their placement origin on an in-plane axis.

## 2026-07-17 — Real-provider freeform model revision E2E

### Goal

Verify that freeform text edits the currently rendered body and reaches a printable STL through the actual LLM provider.

### Golden path

1. Run `make test-e2e-freeform-real` with the configured provider credentials.
2. The test starts from the stable `minimal_body` fallback plate and sends the freeform instruction through `/api/specifications/validate`.
3. It requires two edge cutouts and a centred through-hole, then applies a second freeform request for a 10 x 10 x 10 centred top boss.
4. It requires the box origin `[25, 15, 10]`, renders a draft preview, completes a semantic build, and exports `artifacts/real-freeform-delta/model.stl`.

### Verification

On 2026-07-17, the real Gemini planner created two `pocket` cuts and a 10 mm central through-hole, then created `boss_half_size`, `boss_origin_x`, and `boss_origin_y` before adding the centred boss. The test completed the CAD build and STL export successfully.

### Failure pattern avoided

A provider-mocked test can only replay a chosen tool sequence; it cannot prove that the configured LLM follows freeform instructions or that its actual response is renderable.

### Ruled-out approaches

- Tried `FreeformCutoutsE2ETests` with a mocked `httpx.AsyncClient`; rejected because it was not an E2E test and could not validate the live provider.
- Tried exporting directly from `mode=draft`; rejected by the API because export correctly requires a semantically validated build.

## 2026-07-17 — Filter lint-invalid features before a draft preview

### Goal

Keep the first draft preview renderable even when the planner returns a confirmed feature with an invalid target placement.

### Golden path

1. Build the structural minimal model from confirmed supported features.
2. Run deterministic draft lint on that model.
3. Omit the feature reported first by each lint error, then omit any feature whose target is no longer present.
4. Use the fallback box only if no confirmed additive body remains.

### Verification

`tests.test_minimal_model` reproduces a top pocket located at Z=30..45 but targeting a base at Z=0..10; the pocket is omitted. `tests.test_spec7_api` verifies that `build?mode=draft` then returns `draft_preview` and calls the renderer.

### Failure pattern avoided

Checking only feature contracts and target IDs allows a confirmed cut that never intersects its target to reach the build lint gate, producing a 422 before draft fallback can run.

### Ruled-out approaches

- Tried leaving lint errors for `build?mode=draft` to handle; rejected because that endpoint intentionally returns 422 before entering its CAD-worker fallback path.

## 2026-07-17 — Preserve CAD coordinates in the interactive viewer

### Goal

Let users position a rendered model against an explicit CAD coordinate reference instead of an arbitrary viewer-centred mesh.

### Golden path

1. Do not call `geometry.center()` after loading an STL in `ModelViewer`.
2. Translate only its visual Z minimum to zero, then render a Z-up camera, XY grid, X/Y/Z arrows, and millimetre tick labels in the same Three.js scene.
3. Frame the isometric camera around the positioned mesh and coordinate guides.

### Verification

`npm run build` passed and `tests.test_static_ui` verifies the built UI exposes the `XY plane at Z=0` coordinate-ruler contract.

### Failure pattern avoided

`geometry.center()` destroys the CAD origin, so a user cannot tell how the visible model relates to X, Y, or the Z=0 build plane.

### Ruled-out approaches

- Kept the generic Three.js grid helper in its default XZ/Y-up orientation; rejected because EasyCAD coordinates use XY as the ground plane and Z as vertical.

## 2026-07-17 — Reject cuts that do not enter material

### Goal

Ensure a visually requested cut reaches the CAD solid instead of merely touching an outside face or using a degenerate slot profile.

### Golden path

1. Treat `placement.origin` as the start of the positive cutting extrusion; lint requires a non-zero overlap with the target along that extrusion axis.
2. Reject a slot whose length is not greater than its width, because it has no straight section in CadQuery.
3. Return the deterministic lint error to the incremental planner so it can choose a valid supported primitive before rendering.

### Verification

The real `test_combined_edge_cutouts_and_boss_render_from_one_freeform_instruction` first produced two degenerate slots, then received the lint error and replaced them with pockets in its second real planner turn. The semantic build and `artifacts/real-freeform-delta/combined-model.stl` export succeeded.

### Failure pattern avoided

A positive cut beginning on a target's top face extrudes away from the material, while a `slot2D` with equal length and width makes CadQuery fail with `BRep_API: command not done`.

### Ruled-out approaches

- Accepted boundary contact as cut intersection; rejected because it can render no removed material.
- Allowed an equal-length-and-width slot; rejected because CadQuery treats it as a failed degenerate operation.

## 2026-07-17 — Preserve and fuse raised Unicode text

### Goal

Render freeform raised text on an exterior CAD plane as part of one printable solid.

### Golden path

1. Preserve the confirmed string dimension referenced by a `text` feature's `content` parameter when creating the minimal reliable draft.
2. Generate text with left/bottom alignment so its declared origin is the visible text start.
3. For additive text, add a 0.01 mm opposite-direction overlap before the final boolean union; translate the complete primitive expression.
4. Run `tests.e2e_real_freeform_delta.RealFreeformDeltaE2E.test_outward_text_on_xz_surface_is_preserved_and_rendered` against the configured provider.

### Verification

On 2026-07-17, the real Gemini planner returned an additive `Привет` text feature on XZ at X=10/Z=10 with 10 mm size and 1 mm depth. The E2E semantic build succeeded and exported `artifacts/real-freeform-delta/text-model.stl`.

### Failure pattern avoided

Numeric-only dimension filtering silently removed text content, and face-tangent glyphs could remain separate solids even though the text was visibly touching the base.

### Ruled-out approaches

- Used CadQuery's `glue=True` union; rejected because it left Cyrillic glyphs as separate solids.
- Translated a composed text expression without parentheses; rejected because only the final anchor operand moved.

## 2026-07-17 — Make engraved text enter an exterior face

### Goal

Ensure a freeform text cut removes material when the named exterior surface has either workplane-normal direction.

### Golden path

1. Compile every `text` feature with `operation=cut` as the union of equal positive and negative depth text volumes around its supporting plane.
2. Subtract that symmetric volume from the target; the half that enters the target removes the requested depth.
3. Verify both depth signs in `tests.test_feature_compiler` and run the real-provider `test_inset_text_on_xz_surface_removes_material` E2E.

### Verification

On 2026-07-17, the real planner produced `operation=cut`, XZ placement at X=10/Z=10, and positive 1 mm depth for `2Привет`. The semantic build passed, recorded a negative text volume delta, and exported `artifacts/real-freeform-delta/text-cut-model.stl`.

### Failure pattern avoided

CadQuery's XZ normal sent a positive text extrusion away from the Y=0 exterior face, so subtracting it visibly changed nothing.

### Ruled-out approaches

- Required the LLM to choose a plane-specific depth sign; rejected because the actual planner correctly understood “вдави” but emitted a conventional positive depth.

## 2026-07-18 — Three-action model API smoke path

### Goal

Verify the simplified interactive flow: image upload, freeform model change, then STL download.

### Golden path

1. POST an image to `/api/model/image`; use the returned `description`, `specification`, and base64 `model_stl` to display the model.
2. POST that specification and a freeform prompt to `/api/model/refine`; replace both the displayed text and STL with the response.
3. POST the latest specification to `/api/model/stl` to download the printable STL.

### Verification

On 2026-07-18, a live run using `fixtures/3.png` returned all four fields from image and refinement (`description`, `specification`, `model`, `model_stl`) and returned a non-empty `model/stl` response from export.

### Failure pattern avoided

The previous UI required separate validate, schematic, draft-build, preview, full-build, and export calls, so it could show a stale or missing 3D model while a user was editing.

### Ruled-out approaches

- Kept a separate preview endpoint; rejected because the first two actions can return the exact STL consumed by the viewer.

## 2026-07-18 — Tolerate provider tool-call spelling variants

### Goal

Prevent a valid planner batch from looping when the provider serializes otherwise valid CAD fields in common textual forms.

### Golden path

1. In `DraftBuilder`, normalize `millimeter` and `millimeters` to `mm`.
2. Treat root targets written as `none` or `null` as JSON null.
3. Decode a placement JSON string or `origin: [x, y, z]` form before Pydantic validation, converting numeric coordinates to numbers.
4. Stop after three identical rejected tool results, or the planner turn cap, and pass the accumulated draft to `minimal_reliable_draft`, which guarantees a renderable body.

### Verification

On 2026-07-18, the real provider run `02b2b94f6244` completed in six turns after previously repeating `add_box` with renamed IDs. Direct replay of the rejected placement and target payload succeeded, and Python compilation plus the UI build passed.

### Failure pattern avoided

`placement="origin: [0, 0, 0]"` was interpreted as three dimension IDs, while `target="none"` was interpreted as a missing feature. The provider then changed only the feature ID and repeated the rejected call for 25 turns.

### Ruled-out approaches

- Added a special case for `add_box`; rejected because any feature can use the same provider spelling variants.

## 2026-07-18 — Restore caret position with useLayoutEffect, not requestAnimationFrame

### Goal

Let a user pick a feature from the `@mention` dropdown (SPEC 9 Part D) and
keep typing immediately without corrupting the freeform instruction text.

### Golden path

1. On mention selection, update the controlled `<textarea>`'s `value` via
   `setPrompt` and stash the target caret offset in a ref instead of calling
   `setSelectionRange` inline.
2. Restore focus and the caret position inside a dependency-free
   `useLayoutEffect`, which Preact runs synchronously right after the new
   `value` is committed to the DOM and strictly before the browser can
   process any subsequently queued input event.
3. Verified with a Playwright script that types the next word immediately
   (1-5ms per keystroke) after clicking a mention — the adversarial case a
   human fast-typer or any automated driver will hit.

### Verification

On 2026-07-18, `verify_mention_fix.py` (real browser, real upload) asserted
the post-type textarea value equals the expected clean string
(`"@A make it 5mm taller"`); the full `verify_spec9.py` end-to-end pass
(real provider calls) also produced clean prompt text and a correct
scoped-refine round trip, with zero console errors.

### Failure pattern avoided

Deferring `setSelectionRange` with `requestAnimationFrame` while calling
`.focus()` synchronously raced the browser's real input event queue: the
first keystroke landed correctly, the rAF callback then fired and reset the
caret mid-word, and every following keystroke inserted before the
already-typed character instead of after it — e.g. typing "make it 5mm
taller" landed as "ake it 5mm tallerm". A human typing at normal speed
right after a click could hit this on a slow or busy frame, not only fast
synthetic input.

### Ruled-out approaches

- Deferred the caret restore with `requestAnimationFrame`; rejected because
  it runs after paint, with no guarantee it beats the next real input event.

## 2026-07-18 — A real worker failure must never reach the user as an error

### Goal

Guarantee `/api/model/image`, `/api/model/refine`, and `/api/model/stl` never
return a build error, especially on the first upload — `minimal_reliable_draft`
only proved a draft was schema-valid, not that the real CadQuery worker could
build it.

### Golden path

1. Extract the existing "reduce everything to the fallback box" branch out of
   `minimal_reliable_draft` into a public `fallback_draft(draft)`
   (`app/minimal_model.py`) so a caller outside that function can invoke the
   same guaranteed-safe reduction.
2. Add `_build_or_fallback(draft)` (`app/main.py`): attempt the real
   `project_from_specification` + `run_project` build once; on `RunnerError`
   or `SpecificationValidationError`, reduce via `fallback_draft` and retry
   exactly once before ever raising. Every endpoint that builds
   (`/api/model/image`, `/api/model/refine`, `/api/model/stl`) routes through
   this instead of calling `run_project` directly.
3. Move `_description()` computation inside `_model_response`, computed from
   the *final* draft (post-sanitization, post-fallback) instead of a
   pre-computed string passed in — otherwise a recovered response would
   describe features that got traded away for the fallback box, and the
   pre-existing `/api/model/refine` path was already describing the
   unsanitized draft.
4. Drop the now-redundant `minimal_reliable_draft` call inside
   `generate_draft_specification_from_image` (`app/ai_generation.py`) —
   `_model_response` already guarantees sanitization, so the old call only
   paid for a second schema check on every upload.

### Verification

`tests/test_build_fallback.py`: a mocked first-attempt `RunnerError` recovers
on retry without raising (asserting the fragile feature became `unsupported`
with a reason and `minimal_body` is present); a first-try success never
retries; two consecutive failures still raise. Confirmed the recovery path
builds for real (no mocks) — `fallback_draft` on a deliberately broken
feature, run through the actual `project_from_specification` + `run_project`,
produced valid non-empty STL bytes. Four consecutive real-provider uploads of
`fixtures/3.png` all returned HTTP 200.

### Failure pattern avoided

A real session (2026-07-18) got a 422 on `/api/model/image` when the planner
chose an `add_fillet` construction that passed `minimal_reliable_draft`'s
schema-level `_compiles()` check but failed the actual CadQuery kernel build
— exactly the first-upload experience this app exists to guarantee never
happens.

### Ruled-out approaches

- Made `_compiles()` itself call the real worker instead of only
  `project_from_specification`; rejected because that pays the full build
  cost twice on every single successful request (the overwhelming majority)
  to guard a rare failure — retrying only after a real failure keeps the
  success path at one build.

## 2026-07-18 — A stale `minimal_body` id can silently defeat its own fallback

### Goal

Make the "guaranteed-safe fallback" in `fallback_draft`/`minimal_reliable_draft`
(`app/minimal_model.py`) actually unconditional, closing a gap found by manual
review of the fallback-retry fix above.

### Golden path

1. Never special-case "does a feature with id `minimal_body` already exist" as
   a reason to skip inserting a fresh one — its *status* is what matters, and
   a `minimal_body`-id feature omitted by an earlier, unrelated pass (or
   echoed back by the planner from a previous round's fallback, since a
   confirmed fallback box becomes part of `previous_specification`) is not
   safe to keep.
2. `_insert_fallback_box` strips any existing `minimal_body`-id feature first,
   then unconditionally inserts a fresh, confirmed one — one call site,
   reused by all three places `minimal_reliable_draft`/`fallback_draft`
   used to insert the fallback box ad hoc.

### Verification

`tests/test_build_fallback.py::FallbackDraftReentrancyTests` feeds
`fallback_draft` a draft whose only features are an already-`unsupported`
`minimal_body` and one other feature; asserts exactly one `minimal_body`
survives and it is `confirmed`. Confirmed this fails against the prior
logic (`if not any(item.id == "minimal_body"...)` finds the stale one and
skips the insert, leaving zero confirmed features — the exact guarantee
this module exists to uphold).

### Failure pattern avoided

`if feature.id != "minimal_body": _omit(...)` explicitly skipped omitting a
`minimal_body`-id feature regardless of its status, and the guard before
inserting a fresh one only checked *presence*, not *confirmed status* — so a
draft could reach the end of the guaranteed-safe reduction with zero
confirmed features, exactly the class of bug this function exists to
prevent.

### Ruled-out approaches

- Patching only `fallback_draft`'s check to also require `status ==
  "confirmed"`; rejected because the identical unguarded pattern
  (`result.features.insert(0, _fallback_box())`) appears twice more in
  `minimal_reliable_draft` itself — fixing the insertion helper once removes
  the whole class of bug instead of one instance of it.

## 2026-07-18 — Alias hygiene: filter by the live roster, not the alias map

### Goal

Close two gaps a manual review found in SPEC 9 Part D's `@mention` handling:
a hand-typed `@<alias>` referencing a feature id from a superseded round
could resolve to a dead id, and the literal `@<alias>` syntax reaching the
LLM in the freeform prompt text was needless.

### Golden path

1. `resolveMentions` (`frontend/src/main.tsx`) now checks resolved ids
   against `state.features` (the current roster) in addition to
   `featureAliases` (which is deliberately sticky and never shrinks, per
   Part B) — an alias for a feature no longer in the roster no longer
   resolves, even if a user types it by hand outside the dropdown.
2. The same pass replaces each resolved `@<alias>` token with the feature's
   real label before the prompt is sent — the user still sees and edits
   `@A` in the textarea, but the wire payload and the LLM only ever see
   natural language plus the separately-carried `referenced_feature_ids`.
3. Extracted the alias generator into a dependency-free `frontend/src/alias.ts`
   so it can be unit-tested with Node's built-in test runner
   (`frontend/src/alias.test.ts`, `npm test`) without dragging in `zustand`'s
   React-path resolution (which only resolves under Vite's Preact-compat
   aliasing, not plain Node).
4. Added the real-provider E2E `tests/test_e2e_scoped_refine.py` that
   spec9.md's own Verification section called for but the original
   implementation pass substituted with an unrecorded, one-off Playwright
   script — opt-in via `EASYCAD_RUN_REAL_E2E=1` so the fast suite stays
   network-free.

### Verification

`npm test` (3/3). Live Playwright check confirmed the wire payload:
typing `@A please make it 5mm taller` in the textarea sent
`"prompt":"Base Plate Main Body please make it 5mm taller"` with
`"referenced_feature_ids":["base_box"]`. `EASYCAD_RUN_REAL_E2E=1 .venv/bin/python
-m unittest tests.test_e2e_scoped_refine` passed against the real provider in
~55s, and the planner's second call visibly touched only the referenced
feature's dimension.

### Failure pattern avoided

`resolveReferencedFeatureIds` only checked `featureAliases`, which by Part
B's own design keeps every alias ever assigned in the session — a user
typing a remembered letter for a feature two rounds ago would silently
resolve to a dead id (correctly dropped server-side, but with zero
client-side signal that the reference did nothing).

## 2026-07-19 — Multi-panel dimension triangulation, promoted from a spike

### Goal

Reduce single-photo geometric ambiguity for sketches that already draw
multiple orthographic views on one page, without adding any cost or risk to
the far more common single-view upload.

### Golden path

1. `app/multiview_triangulation.py:detect_panel_layout` — one cheap vision
   call classifies the upload as single- or multi-panel and, if multi-panel,
   returns rough (left, top, right, bottom) fractions per panel. Returns
   `None` (normal path, zero extra cost) for an ordinary photo.
2. Each detected panel gets its own dimension-reading vision call (never a
   holistic read of the flattened image) plus an independent Tesseract OCR
   pass (`ocr_panel_dimensions`, digit-only whitelist) on the same pixels.
3. `reconcile()` — pure Python, no LLM — groups all readings (from every
   panel and both reading methods) by numeric value; a dimension counts as
   verified only when 2+ different `(panel, method)` sources agree.
4. The verified facts are formatted as plain grounding text and passed as
   `instructions` into the existing, unmodified
   `generate_draft_specification_from_image` — no new tool, no change to
   the compiler.
5. Wired into `POST /api/model/image` (`app/main.py`) inside a function that
   never raises (`build_grounding_instructions`): any failure — no
   multi-panel layout, missing tesseract binary, a malformed vision
   response — degrades to `""` and the upload proceeds exactly as before.

### Verification

Real run on `fixtures/a3b.jpg` (a genuine 4-panel hand sketch):
`detect_panel_layout` correctly classified it and named its own panels
(`front`/`bottom`/`side`/`top`); reconciliation cross-verified 5 dimensions,
including one (30mm) confirmed by an LLM read **and** an independent OCR
read on the same crop — two unrelated reading methods agreeing, not just
two views. `POST /api/model/image` (real endpoint, `TestClient`, no
handwritten orchestration) built 6 confirmed features and a valid STL.
A same-session real upload of the unrelated single-view `fixtures/3.png`
produced the identical 4-feature L-bracket result seen throughout this
project's history — confirming the new gate adds nothing to that path but
one cheap classification call. `tests/test_multiview_triangulation.py`
(13 tests, `_chat_json` mocked) covers the gating logic without network
access; `tests/test_e2e_multiview_triangulation.py` is the opt-in
real-provider proof above.

### Failure pattern avoided

The first version of `detect_panel_layout` used a row/column mean-brightness
gap heuristic instead of a vision call. It failed its own unit test the day
it was written: false-positived on `fixtures/3.png` (uniformly bright
end-to-end — a lot of blank margin around one small drawing, no real panel
gap, but every band still cleared an absolute brightness threshold) and
false-negatived on `fixtures/a3b.jpg` itself (a camera photo, not a scan —
lighting falloff keeps even the genuine inter-panel gaps below a
scan-calibrated brightness floor). Absolute brightness isn't comparable
across a phone photo and a clean scan.

### Ruled-out approaches

- Pixel-brightness gap detection for panel-layout classification (see
  above) — replaced with one cheap vision call, which is the right tool for
  a holistic "one drawing or several panels" judgment.
- Classical arrowhead detection (OpenCV `HoughLinesP` + wedge-angle
  junctions) to mechanically separate dimension-line pixels from
  object-line pixels before either reading channel sees them: fires on
  every square corner and every hatching crossing as readily as on a real
  arrowhead on this hand sketch (38 candidates on one panel, real arrowheads
  buried in false positives) — not a usable filter without real shape/
  template matching or a trained detector. Replaced with an explicit prompt
  instruction telling the per-panel vision call to distinguish object lines
  from dimension/leader lines itself.
