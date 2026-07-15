# EasyCAD v2 Evidence Tasks

Status legend: `[ ]` not started, `[~]` in progress, `[x]` verified. A task cannot be marked `[x]` from a positive-path test alone; it needs the stated paired negative evidence.

## Milestone 0 — Make completion evidence explicit

- [x] Add `implemented`, `verified`, `observed`, and `supported` evidence definitions to capability-report documentation.
- [x] Change report schema so missing observations yield `insufficient_evidence`, never synthetic success.
- [x] Add report-schema tests for all outcome counters and missing-evidence behavior.

Acceptance: a report with no observations cannot say `passed`; invalid plans, worker failures, and `needs_review` each have a distinct counter.

## Milestone 1 — Replace weak capability metrics

- [x] Extend each supported capability case with expected normalized Feature Graph fragments and named measurements.
- [x] Compare expected and actual graph operation type, target, placement, parameter bindings, and coverage state.
- [x] Compute dimension error from matching named graph parameters or worker measurements.
- [x] Record individual STL and STEP results for every case; remove suite-wide export-rate substitution.
- [x] Add evaluator mutations for wrong type/target, omitted operation, wrong named parameter, and one failed export among successes.
- [x] Assert that every mutation makes the capability gate fail.

Acceptance: correct feature words and numbers paired with a wrong graph target or operation fail the quality gate.

## Milestone 2 — Restore the bracket as a blocking integration regression

- [x] Replace the base-only graph in `projects/bracket_fixture.json` with generic base, upright, groove, through-hole, and front-notch operations.
- [x] Remove stale compiled source from the fixture, or prove it is ignored and absent from all assertions.
- [x] Assert exact normalized graph IDs, operation types, targets, placement, and parameter bindings.
- [x] Assert worker evidence for bounds, upright contribution, hole count/diameter, groove depth, and notch material removal.
- [x] Add one negative mutation fixture per protected bracket feature.
- [x] Include these regressions in the default local test command.

Acceptance: removing, retargeting, or changing dimensions of any protected bracket feature fails with the related operation ID.

## Milestone 3 — Split real-provider outcomes

- [~] Introduce `contract_ok`, `needs_review`, `verified_export`, `invalid_plan`, `worker_failed`, and `semantic_failed` counters.
- [~] Split the current real-provider test into resilience and supported-corpus success suites.
- [~] Require semantically verified STL and STEP for each `verified_export` case.
- [ ] Add configurable per-capability minimum observation and verified-export-rate gates.
- [ ] Report missing keys, outages, and malformed output as unavailable/failed evidence rather than passing quality evidence.
- [ ] Keep sanitized recordings and replay them in the network-free suite.

Acceptance: a run consisting only of `needs_review` can pass resilience checks but fails the supported-corpus quality gate.

## Milestone 4 — Close semantic operation coverage

- [x] Enumerate compiler-supported operation kinds from one authoritative manifest.
- [x] Create semantic evidence manifest entries for positive fixture, negative fixture, measurements, and invariant.
- [x] Add coverage lint that fails for a supported kind without paired semantic evidence.
- [x] Implement or verify invariants for counterbores, countersinks, shells, mirrors, fillets, chamfers, and planar text.
- [ ] Downgrade operations lacking reliable deterministic evidence to `experimental` and require `needs_review`.
- [ ] Add positive and targeted negative tests for every listed operation.

Acceptance: adding a compiler-supported operation without paired evidence fails CI; each negative fixture fails semantic validation for its operation ID.

## Milestone 5 — Add calibrated silhouette regression checks

- [x] Define fixture-only camera/projection metadata and expected render-mask artifacts.
- [x] Implement deterministic mask, contour-bound, occupied-area, and symmetric-difference comparisons.
- [x] Add positive silhouette fixtures for the bracket and one asymmetric capability case.
- [x] Add negative renders with a missing feature and wrong orientation or placement.
- [x] Document why arbitrary-upload comparison remains advisory.

Acceptance: known-good renders pass; each negative render exceeds its threshold; uncalibrated uploads are not rejected by silhouette checks.

## Milestone 6 — Evidence review and release gate

- [x] Run the complete local deterministic suite through `uv` without Docker or network access.
- [~] Run the real-provider supported corpus, sanitize recordings, and archive the machine-readable result.
- [ ] Verify every supported capability has positive, negative, and observed evidence.
- [ ] Update `docs/PROJECT_WEAKNESSES.md` with resolved items and residual limitations.

Acceptance: the release report distinguishes deterministic verification from provider observations, and no capability is labelled supported without both forms of evidence.

## Milestone 7 — Human review MVP

- [ ] Define a versioned review-history record with local actor label, timestamp, action, linked issue/feature IDs, changed parameter values, and comment.
- [ ] Build a backend review summary from generation error, non-implemented coverage entries, assumptions, inferred/default parameters, and available preview metadata.
- [ ] Expose the review summary in the existing assumptions/status UI alongside the model preview and parameter editor.
- [ ] Allow reviewer changes only to existing editable parameter values, enforcing current bounds, types, and expressions.
- [ ] Allow reviewers to accept an assumption or reject a non-implemented feature with a mandatory reason; preserve it as `unsupported` or `unresolved` coverage.
- [ ] Allow AI repair requests to include selected review issue IDs as scoped context.
- [ ] Recompile, rerun worker, and rerun semantic validation after a valid parameter change; do not carry forward stale success status.
- [ ] Preserve `needs_review` after an assumption is accepted unless all normal verification gates subsequently pass.
- [ ] Add API and UI tests for review display, valid edit/reverification, invalid edit rejection, assumption acceptance, feature rejection, and immutable audit history.

Acceptance: a reviewer can understand every unresolved issue, correct an existing dimension, or explicitly record an acceptance/rejection without editing Feature Graph structure. Every decision remains visible in project history, and no review action bypasses compilation or semantic verification.

Out of scope: manually adding, deleting, retargeting, retyping, or changing placement/profile/pattern of Feature Graph operations.
