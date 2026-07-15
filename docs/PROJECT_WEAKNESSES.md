# Project Weaknesses And Mitigation Options

This document records known weaknesses in the current EasyCAD implementation. It is a risk register, not an
extension of the product specification: a mitigation becomes required only after it is selected in `spec.md` and
scheduled in `TASKS.md`.

## Priority guide

- **P0:** can produce an incorrect printable model while reporting success.
- **P1:** materially reduces reliability, security, or recoverability.
- **P2:** limits scale, offline use, or maintainability without corrupting the current result.

## 1. Capability metrics can overstate model quality

**Priority:** P0

**Status (2026-07-13):** Partially mitigated. Capability quality gates now compare normalized Feature Graphs,
named parameters, and individual STL/STEP outcomes. The legacy real-provider keyword evaluator still requires
replacement before provider quality can be claimed.

The real-provider capability evaluator detects features by searching response text for keywords. Dimension accuracy
is estimated by matching an expected dimension to the nearest number anywhere in the response. A response can
therefore receive perfect precision, recall, or dimension error without producing the correct Feature Graph or
geometry.

### Mitigation options

1. Compare normalized expected and actual Feature Graph operations by operation type, target, parameters, and
   tolerances. This tests the contract before geometry compilation.
2. Compare worker measurements from the exported solid, including bounds, counts, diameters, pitch, margins, volume,
   and connected-body count. This tests the actual result but requires reliable measurement support per operation.
3. Keep keyword scoring only as a prompt-diagnostics metric and remove it from release gates. This is inexpensive but
   does not replace product-level validation.

**Recommendation:** combine options 1 and 2. Report text scoring separately and never use it to claim geometry
correctness.

## 2. Real E2E can pass with `needs_review`

**Priority:** P0

**Status (2026-07-13):** Partially mitigated. Resilience and strict supported-corpus targets now record explicit
outcomes. A strict live run recorded `worker_failed` for `1.jpg` and `invalid_plan` for `2.jpg`; neither was treated
as a successful export.

The current real-provider test accepts a controlled `needs_review` outcome. This usefully proves that malformed model
output is handled safely, but it does not prove successful image-to-STL conversion.

### Mitigation options

1. Split outcomes into `contract_ok`, `needs_review`, and `verified_export`, and gate releases on a minimum
   `verified_export` rate for supported fixtures.
2. Create separate resilience and success suites. The resilience suite may accept `needs_review`; the success suite
   must produce a semantically verified STL and STEP.
3. Require every fixture to export successfully. This is strict but makes the suite fragile when a remote model changes
   and obscures whether failure handling itself remains correct.

**Recommendation:** option 2, with the three explicit outcome counters from option 1.

## 3. The bracket fixture no longer covers its important features

**Priority:** P0

**Status (2026-07-13):** Resolved for the integration fixture. The Feature Graph now covers base, upright, groove,
through-hole, and notch; removal mutations and worker measurements run in the default local suite.

`projects/bracket_fixture.json` currently represents only a base box. It no longer protects the upright, hole, groove,
and notch behavior that made the fixture useful.

### Mitigation options

1. Restore the complete bracket using generic Feature Graph operations and assert its semantic measurements.
2. Replace it with several small fixtures, one per feature. These are easier to diagnose but do not test feature
   interaction.
3. Keep both a complete integration fixture and focused operation fixtures.

**Recommendation:** option 3. The complete bracket is the regression case; focused fixtures locate failures.

## 4. Provider output is less stable than the strict Feature Graph contract

**Priority:** P0

Providers may return prose operations, expression strings, incomplete profiles, implementation dictionaries, or
repair updates outside the requested feature IDs. The adapter safely rejects many bad forms, but valid drawings can
still end in `needs_review` because the provider did not follow the schema.

### Mitigation options

1. Use provider-native structured output or JSON Schema enforcement where available.
2. Add a deterministic normalization layer for a small, documented set of equivalent representations.
3. Add another LLM normalization pass. This may recover more responses but adds cost, latency, and another source of
   nondeterminism.
4. Reduce each prompt to one bounded transformation and validate after every stage.

**Recommendation:** options 1 and 4, with option 2 limited to representations observed in recorded fixtures. Avoid a
general-purpose compatibility parser.

## 5. Worker isolation is incomplete

**Priority:** P1

The CadQuery worker runs in a separate local process with timeout and process-group termination, but it has no hard
memory, CPU, file-system, or network limits. Pathological geometry can exhaust machine resources.

### Mitigation options

1. Apply OS resource limits in the subprocess on supported platforms and use a dedicated temporary working directory.
2. Add a separately configured container runner for untrusted multi-user deployments.
3. Keep local subprocess execution but enforce request concurrency, geometry complexity, and artifact-size limits.

**Recommendation:** options 1 and 3 for the local-first product. Keep containers deployment-specific, not a required
development or test path.

## 6. Trusted compilation still executes generated Python text

**Priority:** P1

The backend owns the compiler output, but it still serializes CadQuery Python and the worker executes it. This retains
an unnecessary code-generation boundary and makes validation and diagnostics harder than direct interpretation.

### Mitigation options

1. Implement a direct Feature Graph interpreter that calls a fixed set of CadQuery operations.
2. Keep generated Python, strengthen compiler snapshot tests, and treat it as an internal intermediate representation.
3. Use a declarative geometry engine or another CAD kernel API directly. This would be a major rewrite.

**Recommendation:** option 1 incrementally, operation by operation. Keep option 2 until feature parity is reached;
option 3 is not justified by current needs.

## 7. Semantic verification is uneven across operation types

**Priority:** P0

**Status (2026-07-13):** Partially mitigated. Compiler operation kinds have a semantic-evidence manifest and
runtime checks now reject no-op modifiers. Per-operation negative fixtures still need broadening before every kind
can be considered equally verified.

Counts and dimensions are verified well for some holes and patterns, but confidence is weaker for modifiers,
placement, shelling, text, mirror operations, countersinks, and counterbores. A model can export while a subtle feature
is reversed, misplaced, or missing.

### Mitigation options

1. Define operation-specific invariants and worker measurements for every supported operation.
2. Mark operations without reliable verification as `needs_review` instead of `verified`.
3. Use rendered-image comparison as the primary verifier. This detects visible differences but cannot reliably prove
   internal geometry or exact dimensions.

**Recommendation:** options 1 and 2. Use image comparison only as additional evidence.

## 8. Visual comparison is nondeterministic and incomplete

**Priority:** P1

**Status (2026-07-13):** Partially mitigated. Golden binary masks now protect calibrated bracket and asymmetric-rib
renders, including missing-feature and wrong-projection mutations. Arbitrary uploads remain advisory by design.

LLM render comparison can miss orientation, placement, and topology errors. There is no deterministic silhouette or
projection comparison to catch obvious discrepancies before asking a model.

### Mitigation options

1. Render fixed orthographic views and compare masks, contours, occupied area, and symmetric differences.
2. Compare extracted edges and known hole centers against drawing annotations where calibration is available.
3. Keep multimodal comparison for ambiguous differences and explanation.

**Recommendation:** option 1 first, followed by option 3. Add option 2 only for drawings with trustworthy scale and
view correspondence.

## 9. Source images and projects are not fully persistent

**Priority:** P1

Uploaded source images are held in memory and disappear after restart. Full project reload and migration remain
incomplete, so a generated result is not yet a durable editable project.

### Mitigation options

1. Store project JSON, source image, renders, and exports in a local project directory using stable relative paths.
2. Use SQLite for metadata and the file system for binary artifacts.
3. Use an object store and database. This supports hosted deployments but is unnecessary for the local prototype.

**Recommendation:** option 1 with a schema version and explicit migrations. Introduce SQLite only when querying many
projects becomes a current requirement.

## 10. Model logs may expose sensitive drawing content

**Priority:** P1

Full provider requests and responses are valuable for debugging and fixture capture, but they can contain drawings,
dimensions, filenames, instructions, or proprietary text.

### Mitigation options

1. Disable full payload logging by default and enable it through an explicit development setting.
2. Redact secrets, binary image data, authorization headers, and user-identifying metadata before logging.
3. Add retention limits and a user-visible command to clear diagnostic data.
4. Encrypt logs. This adds key-management complexity and does not replace minimization.

**Recommendation:** options 1, 2, and 3. Treat recorded fixtures as reviewed test assets, not automatic log copies.

## 11. Capability drawings are too synthetic

**Priority:** P1

The generated capability drawings are clean and regular. They do not cover noisy photographs, perspective distortion,
mixed line weights, partial dimensions, handwritten annotations, occlusion, or uncommon feature combinations.

### Mitigation options

1. Add a curated regression corpus of real, permission-cleared drawings with expected Feature Graphs and measurements.
2. Apply deterministic degradations to synthetic drawings: blur, perspective, noise, shadows, compression, and crop.
3. Generate more synthetic combinations. This improves breadth but not realism by itself.

**Recommendation:** combine options 1 and 2, preserving synthetic fixtures for fast, precise unit coverage.

## 12. The capability suite is slow

**Priority:** P2

The full capability suite takes several minutes, which discourages frequent local execution and slows feedback.

### Mitigation options

1. Split fast contract/compiler tests from export, render, and real-provider suites.
2. Cache immutable fixture exports keyed by graph, parameters, compiler version, and CadQuery version.
3. Parallelize independent worker cases with a conservative process limit.

**Recommendation:** option 1 first, then option 3. Cache only deterministic intermediate artifacts to avoid hiding
regressions.

## 13. Frontend depends on CDN-hosted Three.js

**Priority:** P2

A CDN dependency conflicts with the local-first goal and makes the viewer unavailable without network access or when
the CDN changes.

### Mitigation options

1. Pin and bundle Three.js and loaders with the application.
2. Vendor the exact browser modules in the repository.
3. Keep the CDN and add a local fallback. This duplicates loading paths and complicates testing.

**Recommendation:** option 1 if a frontend build already exists; otherwise option 2 is the simplest current solution.

## 14. Project schema migration is not established

**Priority:** P1

As the Feature Graph evolves, saved projects can become unreadable or silently change meaning without explicit schema
versioning and migration tests.

### Mitigation options

1. Add a required schema version and small, sequential pure-data migrations.
2. Support only the current schema and reject old projects clearly. This is simple but conflicts with durable projects.
3. Preserve every historical model class. This creates long-term maintenance overhead.

**Recommendation:** option 1. Keep migrations narrow and test them using immutable old-version fixtures.

## 15. Operational controls are missing

**Priority:** P1 for hosted use, P2 for single-user local use

There is no complete job queue, cancellation flow, request rate limit, or global concurrency control. Multiple uploads
can create overlapping LLM calls and CAD workers, causing high cost or resource exhaustion.

### Mitigation options

1. Add an in-process bounded queue, per-job cancellation, and separate limits for provider and worker concurrency.
2. Use an external queue and worker service. This is appropriate for multi-instance hosted deployment, not the current
   local-first scope.
3. Allow only one active generation. This is very simple and fits a single-user prototype but limits future UX.

**Recommendation:** option 1 with small defaults. Option 3 is acceptable as an initial implementation if cancellation
is included.

## Recommended implementation order

1. Replace capability release gates with Feature Graph and worker-measurement comparison.
2. Separate resilience outcomes from successful, verified image-to-export E2E outcomes.
3. Restore the complete bracket regression fixture and add operation-specific semantic checks.
4. Add deterministic orthographic silhouette checks.
5. Persist complete versioned projects and source images.
6. Add log minimization, redaction, and retention controls.
7. Add worker resource and application concurrency limits.
8. Move from Python-text execution toward direct Feature Graph interpretation.
9. Expand the corpus with real and degraded drawings.
10. Bundle frontend dependencies and optimize test-suite execution.

## Decision rule

A mitigation should move into the product specification only when it has:

1. a concrete failure or product requirement;
2. an acceptance criterion that measures the resulting behavior;
3. a focused implementation task;
4. a regression test that would fail without the mitigation.
