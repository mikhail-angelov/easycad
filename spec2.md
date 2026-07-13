# EasyCAD v2: Evidence-Backed Quality Specification

**Status:** Proposed

This is a focused follow-up to `spec.md`. It does not add CAD features. Its purpose is to make existing support claims falsifiable and to ensure that a completed task means a demonstrated product property, not merely that code and a positive-path test exist.

## 1. Problem statement

The implementation contains a Feature Graph, trusted compiler, worker, semantic checks, renders, and a capability report. Some completion criteria, however, measure activity rather than the claimed outcome:

- provider capability detection searches response keywords instead of normalized Feature Graph content;
- capability export success is derived from the whole test-suite result instead of each observed case;
- a real-provider E2E test can pass after safely reaching `needs_review`;
- the bracket fixture's active graph contains only a base although its analysis and legacy source describe more features;
- semantic evidence does not yet cover every compiler-supported operation with a paired negative fixture;
- render comparison is advisory and model-mediated, without deterministic checks for calibrated drawings.

This does not invalidate the pipeline. It means current `[x]` marks establish that code paths exist, but do not always establish that support claims can be disproved by a realistic failure.

## 2. Completion semantics

All v2 tasks use these evidence states. A task in `TASKS2.md` becomes `[x]` only after its acceptance criteria and regression tests pass.

| State | Meaning |
| --- | --- |
| `implemented` | Code exists but no paired negative proof establishes its effect. |
| `verified` | A deterministic positive fixture passes and its paired negative fixture fails for the expected reason. |
| `observed` | A real provider run was sanitized, captured, and replays deterministically. |
| `supported` | Both verified deterministic evidence and observed provider-success evidence exist. |
| `needs_review` | The system safely refused to claim verification; it is never a verified export. |

No status is inferred from prose model output, a test-suite-wide boolean, or the presence of a compiler branch.

## 3. Quality architecture

```text
source drawing
  -> provider analysis and strict Feature Graph validation
  -> expected/actual graph comparison
  -> trusted compiler and worker
  -> operation-specific measurements
  -> semantic gate
  -> verified export

parallel: calibrated orthographic render -> deterministic silhouette checks -> advisory visual comparison
```

The first path is deterministic after capturing provider output and forms the release gate. The visual path adds evidence; it cannot override semantic success or manufacture it.

## 4. Capability evaluation must test the evaluator

### 4.1 Canonical expected outcomes

Every supported drawing must declare a reviewed expected Feature Graph fragment and measurable geometry:

- operation type, target/dependencies, placement, and relevant parameter values;
- expected coverage state for every high-confidence feature;
- individual STL and STEP export result;
- relevant worker measurements and tolerances.

Expected graphs are fixture data, never reconstructed from provider prose.

### 4.2 Metrics and outcomes

Precision and recall compare normalized expected and actual graph features, not analysis keywords. Dimension error compares named expected dimensions with the corresponding graph parameters or worker measurements; it must never select the numerically nearest value anywhere in a response. Export rate uses each case's worker result, not the overall suite result.

The report exposes `contract_ok`, `needs_review`, `verified_export`, `invalid_plan`, `worker_failed`, and `semantic_failed`. It is `insufficient_evidence` whenever required observations are missing.

### 4.3 Evaluator mutation tests

Every metric gate requires paired false-positive fixtures. The initial suite proves failure for:

1. correct words and numbers in model text but wrong Feature Graph type or target;
2. correct bounds but an omitted required operation;
3. correct numeric value attached to the wrong named parameter;
4. one worker export failure among otherwise successful cases.

These fixtures prove the evaluator itself detects the error it claims to detect.

## 5. Bracket integration regression

`projects/bracket_fixture.json` must be a Feature Graph project. It represents base, rear upright, upper groove, through hole, and front notch with generic operations. The legacy `cad.source` field is not evidence and must not participate in the test.

The regression compares exact normalized operation IDs, types, dependency targets, placement, and parameters. Worker evidence verifies bounds, upright volume contribution, hole count and diameter, groove depth, and notch removal. Every protected feature has a negative mutation. This test runs in the normal local suite.

## 6. Real-provider E2E contract

Provider testing has two distinct purposes:

| Suite | Permitted successful outcomes | Meaning |
| --- | --- | --- |
| Resilience | `verified_export`, `needs_review`, `invalid_plan` | Provider variation is handled safely without unsafe execution or server errors. |
| Supported-corpus success | `verified_export` only | A supported case produced a valid graph and semantically verified STL and STEP. |

The supported-corpus suite declares minimum observation and `verified_export` rates per capability. Real calls run explicitly, such as nightly or manually; sanitized recordings replay in normal CI. Missing keys, provider outage, and malformed output are unavailable or failed evidence, never a passing quality result.

## 7. Semantic verification coverage

The compiler exposes one authoritative set of `supported` operation kinds. A coverage manifest maps each kind to a positive fixture, paired negative fixture, deterministic invariant, and worker measurements. A coverage-lint fails if a supported operation lacks this evidence.

Initial coverage includes holes, counterbores, countersinks, slots, pockets, ribs, patterns, shells, mirrors, fillets, chamfers, and planar text. Suitable invariants include material delta, counts, pitch, diameter, depth, bounds, symmetry, and bounded volume change. Where no reliable deterministic invariant exists, the operation is downgraded to `experimental` and must return `needs_review` before a project can be called verified.

## 8. Deterministic visual evidence

Silhouette comparison applies only to fixtures with known orthographic camera, scale, and visible/hidden-line policy. It compares expected and generated rasterized views using binary masks, occupied area, contour bounds, and symmetric difference.

It first protects regression fixtures with known cameras. It must not compare arbitrary uploads directly with CAD renders because perspective, annotations, line style, and unknown scale would create false failures. For arbitrary uploads, the existing visual model comparison remains advisory.

Each silhouette criterion has a positive fixture and negative variants for a missing feature and gross orientation or placement error.

## 9. Non-goals

This scope does not provide a manual Feature Graph operation editor. A reviewer may not add, delete, retarget, retype, or change placement/profile/pattern of an operation in this version. It also does not add a provider, an LLM normalization pass, Docker requirements, a new CAD kernel, hosted queues or persistence, or arbitrary-drawing visual matching. Those remain independent decisions in `docs/PROJECT_WEAKNESSES.md`.

## 10. Human review for incomplete or contradictory projects

When automatic generation ends in `needs_review`, the application shall create a structured review record from:

- validation, compiler, worker, and semantic errors, linked to feature and parameter IDs where available;
- every feature-coverage entry not `implemented`;
- all assumptions and inferred/default parameter values;
- the generated preview when one exists.

The review screen shall show these items alongside the existing parameter editor and model preview. A reviewer may:

1. change values of existing editable parameters within their declared constraints;
2. accept an assumption or reject a non-implemented feature with a required free-text reason;
3. request AI repair with selected issue IDs as context.

Accepting an assumption records reviewer identity or local actor label, timestamp, accepted issue IDs, and optional comment in project history. Rejecting a feature changes its coverage to `unsupported` or `unresolved` with the reviewer reason; it cannot silently disappear. Editing parameters creates a review-history entry containing only the changed values.

Every review action revalidates the project. A parameter change recompiles and reruns the worker plus semantic checks. An assumption acceptance does not turn `needs_review` into `verified`; verification is reached only when all high-confidence features have implemented coverage and the normal deterministic gates pass. A reviewer may export an explicitly unverified project only through an existing, clearly labelled review/export policy; the project JSON must retain its `needs_review` status and review history.

Review is deliberately limited to existing parameters and feature decisions. Correcting an operation's type, target, placement, profile, or pattern remains an AI-repair or future operation-editor task.

### Review acceptance criteria

1. A project with a missing or failed feature displays the linked issue, coverage state, and assumptions before a reviewer acts.
2. Invalid parameter edits are rejected by existing constraints and do not mutate the saved project.
3. A valid parameter edit creates history, recompiles, and receives fresh semantic status rather than reusing a prior success.
4. Rejected features retain a visible `unsupported` or `unresolved` record and a reviewer reason.
5. Accepted assumptions remain visible in project history and cannot alone make an unverified project `verified`.
6. Tests cover positive review actions and an invalid edit/rejected-feature mutation for each state transition.

## 11. Release criteria

This specification is complete only when:

1. every metric/support claim has mutation evidence that intentionally fails;
2. the complete bracket graph passes, and every protected feature has a negative mutation;
3. resilience and verified-export E2E outcomes are reported separately;
4. every compiler-supported operation is semantically covered or explicitly experimental;
5. calibrated silhouette tests catch missing-feature and orientation mutations;
6. normal local tests are deterministic and network-free, while recordings replay;
7. missing provider evidence never becomes a green support claim;
8. `needs_review` projects have a structured human review path that preserves all decisions and reruns validation after edits.
