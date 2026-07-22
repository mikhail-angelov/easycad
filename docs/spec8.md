# EasyCAD Spec 8: Planner Evaluation Harness

## Status

Proposed implementation specification. Companion to SPEC 7; consumes its lint
rules (Part A) and run metrics (A4) for scoring but has no runtime coupling
to the served application. Gates the SPEC 7 Part C default flip.

## Goal

Make planner prompt, model, and tool-schema changes measurable instead of
anecdotal: replay recorded etalon cases against named variants, score every
trial deterministically, and report stability per variant. A prompt rule that
cannot show a stability delta on the corpus does not merge.

## Motivating evidence

The 2026-07-16 sessions changed planner prompts five times; each change was
validated by re-running a paid, nondeterministic e2e and eyeballing one
outcome. The same sessions produced everything an offline harness needs:
recorded vision analyses, scripted user inputs, failure-context dumps
(`logs/planner_context_*.json`), and resolved-geometry assertions that are
robust to model naming variance.

## Language

- **Planner case**: a recorded etalon — one drawing analysis, scripted user
  inputs per round, and expected outcomes asserted on resolved values.
- **Variant**: a named, immutable planner configuration under test (model,
  geometry rules, prompt suffix, tool-schema flavor, temperature, replan
  mode).
- **Trial**: one execution of a case's script under one variant.
- **Invocation**: one planner call sequence inside a trial — the initial
  draft, or one scripted replan round. A trial is a fixed series of
  invocations.
- **Stability**: passed trials / total trials for a case × variant.

## Case format

`tests/fixtures/planner_cases/<name>/case.json`:

```
{
  "analysis":  {...},                     # recorded vision output, verbatim
  "rounds":    [{"user_inputs": {...}}],  # scripted review rounds, in order
  "expected": {
    "features": [                         # resolved-value matching only
      {"match": {"type": "cylinder", "operation": "add"},
       "resolved": {"radius": 30, "origin": [48, 30, 0], "plane": "XY"}}
    ],
    "forbidden": [{"match": {...}}],
    "max_questions_initial": 3,
    "allowed_question_field_ids": ["..."]  # optional
  }
}
```

Rules: assertions resolve dimension ids through `validate_specification` and
never match on dimension or feature names (observed naming variance:
`base_radius` vs `r_base_30`). Case files contain no provider keys, no
absolute paths, no personal data.

Each case may also commit provider transcripts under
`tests/fixtures/planner_cases/<name>/replays/`. Two committed forms:

- **Replay trial** — `replays/<label>/manifest.json` binding ordered
  transcripts to the case script and to an immutable variant identity:

  ```
  {"schema_version": 1, "case": "<name>", "label": "<label>",
   "recorded_at": "<iso8601>",
   "variant": {"name": "<variant>", "replan_mode": "full|incremental",
               "config_fingerprint": "<hash of all PlannerConfig fields>",
               "pairing_fingerprint": "<hash of PlannerConfig minus replan_mode>"},
   "invocations": [
     {"stage": "initial", "transcript": "initial.json"},
     {"stage": "round_1", "transcript": "round_1.json"}]}
  ```

  Fingerprint inputs are canonical JSON of `PlannerConfig` fields; the
  provider key never participates — only the key *source name*. The second
  hash exists because an opaque full-config hash cannot answer "identical
  except `replan_mode`": gate pairing compares `pairing_fingerprint` for
  exact equality and requires the two `replan_mode` values to differ.

  A **passing** replay trial additionally commits
  `final_specification.json` — the recorded live run's final draft as
  canonical JSON with generated identity fields (the specification's `id`)
  excluded; replay compares its result against this snapshot. Failure
  trials omit it (their recorded truth is the failure itself).

  Replaying a trial reconstructs the live flow (see Replay mode) and scores
  the final draft with the full trial-pass condition — this is what
  produces offline stability and gate outcomes.
- **Loose transcript** — `replays/<label>.json`, a single invocation used
  for builder-contract regression only; it cannot produce a trial verdict.

Compatibility metadata lives where one value genuinely applies.
Per-transcript (in each transcript file): `{schema_version, stage,
tool_schema, planner_model, recorded_at, outcome, turns_used}` — `outcome`
uses the SPEC 7 A4 vocabulary (`completed | turn_limit | provider_error |
planner_stopped`), the single outcome enum. Manifest-level:
`schema_version, case, label, recorded_at, variant` as above.

**Terminal outcomes.** `outcome`/`turns_used` record what happened live and
make replay unambiguous even after turn caps or contracts change. Capture
integrity is checked at attach time and at replay load: a `completed`
transcript must contain its successful `finish_draft` result; a transcript
with any non-completed outcome must not — violations fail
with `corrupt_transcript` (a truncated capture can never pose as a
success). Replay then reports two states side by side: the *recorded*
outcome and the *replayed* terminal state under current code. Scoring uses
the replayed state; when the two disagree in class — e.g. a historical
`turn_limit` sequence now reaches a successful finish because a contract
was relaxed — the replay reports `outcome_divergence` and the smoke run
fails with an actionable message ("historical failure no longer
reproduces; re-record or retire the fixture") instead of a silently
shifting verdict. Replay validates compatibility
before executing and fails with a distinct `incompatible_transcript` error —
never a misleading scoring failure — when a schema version or tool-schema
flavor does not match the current contracts. Nothing in the harness assumes
`logs/` exists (clean CI).

`scripts/record_planner_case.py --from artifacts/real-user-flow-<x> --name
<case>` builds a case from saved session artifacts;
`--attach-replay <planner_context.json> --stage <stage> --label <label>`
copies a scrubbed transcript (with metadata, including the recorded
`outcome` and `turns_used`) into the case's `replays/`, creating or
extending the label's manifest; when the attached trial is complete and
passing it also snapshots `final_specification.json` from the session
artifacts. So that passing runs have transcripts at all (today the app
dumps planner context only on failure), `EASYCAD_DUMP_PLANNER_CONTEXT=1`
makes `_dump_planner_context` also write the dump on successful
completion — the recorder's uniform source for both outcomes. Initial corpus: the two
recorded sessions (L-bracket, open box with rim), committed with at least
one complete passing replay trial and one historical-failure replay (trial
or loose) between them.

## Runner

`scripts/planner_eval.py`:

- Inputs: `--cases`, `--variants variants.yaml`, `--runs N` (default 5),
  `--stages initial|full` (default full), `--max-calls` (default 200).
- `variants.yaml` entry: `{name, planner_model, geometry_rules:
  default|<path>, prompt_suffix: optional, tool_schema: default|strict,
  temperature, replan_mode: full|incremental}`. Exactly one variant is
  marked `baseline: true`. Replan mode is a variant field so the SPEC 7 C
  gate can hold everything else constant.

**Variant isolation.** The planner gains an explicit configuration object:
`PlannerConfig` (frozen dataclass: model, provider url, key source, geometry
rules text, prompt suffix, tool-schema flavor, temperature, replan mode,
turn caps), passed as an optional parameter to
`plan_draft_specification`; production callers build one from the
environment at request time, the harness builds one per variant. The
harness never mutates `os.environ`, and an isolation test runs two variants
interleaved and asserts each invocation received exactly its own config.

**Trial flow.** A trial executes the case script as a fixed invocation
sequence, in-process (no HTTP server), provider keys from `.env`:

1. *Initial invocation*: `plan_draft_specification(analysis)` with no
   previous specification.
2. Under `--stages full` (default), one *replan invocation per scripted
   round, in order*: `plan_draft_specification(analysis,
   previous=current_draft, user_inputs=round)`, using the variant's
   `replan_mode`. Under `--stages initial`, rounds are skipped.
3. Any invocation ending in a non-completed SPEC 7 A4 outcome
   (`turn_limit`, `provider_error`, `planner_stopped`) fails the trial
   immediately (`failed_at: initial | round_<n>`); remaining rounds do not
   run and no retries occur.
4. Expected assertions are evaluated once, on the final draft of the
   executed script. Per-invocation metrics (turns, tool errors) are
   recorded for every invocation that ran; a trial's `turns` is their sum.

- Per-trial capture reuses the SPEC 7 A4 record plus: expected-assertion
  results, questions asked, and the failure-context dump on non-completion.
- Trial pass = every invocation completed ∧ zero lint errors ∧ all expected
  assertions hold ∧ zero forbidden matches ∧ question limits respected ∧
  the final draft passes `validate_specification` (the raising build-path
  check: internally consistent, all values resolved). The final-validation
  conjunct is waived only under `--stages initial`, whose purpose is
  scoring initial-question quality on drafts that legitimately contain
  open questions and `needs_input` values.
- Output: `artifacts/planner-eval/<utc-timestamp>/report.json` (schema
  versioned in the file) and `report.md` — a variant × case table of
  stability, median turns, and failure taxonomy counts. Median turns is
  computed over **passing trials only** (failed trials count against
  stability, not turns).
- Budget guard: the projected worst case is computed exactly —
  `Σ over (case, variant) of runs × Σ over the case's invocations of
  turn_cap(invocation)` — where the initial-invocation cap and the
  variant's replan-mode cap (SPEC 7: 48 full, 16 incremental) are used per
  invocation. The runner refuses to start when the projection exceeds
  `--max-calls` and prints the projection either way before the first
  request. The projection is a deliberate ceiling, not a typical cost
  (observed runs use 1–15 turns per invocation); callers size `--max-calls`
  from the printed projection. Worked example, initial corpus (two cases,
  one scripted round each) × a full/incremental gate pair × 3 runs:
  full-variant trial cap 48+48 = 96, incremental 48+16 = 64, projection
  2 × 3 × (96+64) = 960 — the default `--max-calls 200` correctly refuses
  this; the live acceptance run must pass `--max-calls 960` (or higher)
  explicitly.

## Gate (`--gate`)

The gate is a paired comparison with an explicit unit: same cases, same
`--runs` (≥ 5), same `--stages full`, and two variants identical in every
field except the one under test. For the SPEC 7 Part C flip that field is
`replan_mode`; the runner constructs the pair itself from the baseline
variant (`--gate replan_mode`), so accidental drift in any other field is
impossible.

Verdict per case: the candidate passes when its stability is **greater
than** the baseline's, or equal with median passing-trial turns ≤ the
baseline's. Zero passing trials on either side makes turns incomparable:
candidate 0 / baseline > 0 fails; baseline 0 / candidate > 0 passes on
stability alone; both 0 fails with a distinct `not_comparable` reason.
Exit code 1 when any case fails the verdict; the report names the failing
cases and reasons.

**Offline gate inputs.** `--gate` also accepts committed replay trials in
place of live runs: trials are grouped by case × manifest `variant`; the
two groups must share an identical `pairing_fingerprint` and differ in
`replan_mode` (hard error otherwise, `mismatched_gate_pair`), and each side
of a case needs the same trial count ≥ 1, else that case is
`not_comparable`. Stability and median turns are then computed from the
grouped verdicts exactly as for live trials — this is how the gate's edge
cases are proven without a provider.

## Replay mode (offline, free)

Two replay entry points, both provider-free:

- `planner_eval.py --replay <transcript.json>` re-executes one invocation's
  recorded assistant tool calls through the `DraftBuilder` — the
  builder-contract regression check. Accepts a committed loose transcript
  or an ad-hoc `logs/planner_context_<id>.json` from a live session (local
  debugging only — CI never reads `logs/`).
- `planner_eval.py --replay-trial <manifest.json>` replays a committed
  replay trial end to end and emits the same per-trial record, verdict,
  and gate inputs as a live trial. It reconstructs the live flow exactly —
  a **fresh builder per invocation**, never one builder continued across
  invocations:

  1. *Initial invocation*: construct `DraftBuilder(analysis)` from the
     case's analysis and replay the initial transcript's tool calls into
     it; retain the resulting draft.
  2. *Each round, in manifest order*: derive the next builder from the
     retained draft, the case's scripted inputs for that round, and the
     manifest variant's `replan_mode` — in `full` mode the fresh builder
     plus the current derivation of required/preserved sets, in
     `incremental` mode `DraftBuilder.seed(previous, locked_items)` with
     locks derived by the current code — then replay that round's
     transcript into the new builder and retain its resulting draft.
  3. Scripted inputs always come from `case.json` (single source of
     truth); transcripts contribute only the recorded assistant tool
     calls. Derivations (locks, preserved sets) intentionally run on
     *current* code so contract changes are exercised against historical
     model behavior.

**Manifest validation** (before any replay): the invocation stages must be
a contiguous *prefix* of the case script — `initial`, then `round_1 …
round_k` with no gaps, duplicates, reordering, or stages the case script
does not define. A full-length manifest (k = N) may score pass or fail. A
proper prefix (k < N) is only meaningful as a recorded early failure: its
final transcript's recorded `outcome` must be non-completed (any SPEC 7 A4
failure outcome), and the replayed terminal state must agree — a
disagreement is `outcome_divergence` per the Terminal outcomes rule, and a
trial that silently omits scripted rounds can never score as a passing
trial. Structural violations fail with a distinct `invalid_manifest`
error, never a scoring result.

Both entry points validate transcript compatibility metadata first (see
Case format).
This unifies with the existing recorded-replay mechanism
(`test_l_bracket_groove_replay`) behind one utility, and is how
builder-contract and scoring changes are checked against historical runs.

Make targets: `planner-eval` (live, never in default CI) and
`planner-eval-smoke` (replay-only: every committed replay trial and loose
transcript of every case; runs inside `make test`, < 30 s, no network).

## Acceptance criteria

1. `make planner-eval-smoke` passes offline over the committed replay
   trials and loose transcripts only; the test asserts no network client is
   constructed and does not read `logs/`.
2. A live run of 2 cases × 2 variants × 3 runs, invoked with `--max-calls`
   at or above the printed projection (960 for the worked example — the
   default 200 refuses it by design), produces `report.md` and a
   schema-stable `report.json`.
3. Scoring and gating are proven deterministically from replay trials: a
   committed historical-failure replay scores as a failed trial (including
   a trial whose assertions pass but whose final draft fails
   `validate_specification`), a committed passing replay trial scores as
   passed and matches its committed `final_specification.json` (canonical
   JSON, generated identity fields excluded), and an offline `--gate` over
   grouped replay trials
   exits 1 — with `mismatched_gate_pair` proven for manifests whose
   `pairing_fingerprint`s differ. (Live-run regression
   sensitivity — e.g. removing a geometry rule lowering stability — is an
   operational expectation, not an acceptance test, because a live model
   may still guess correctly.)
4. A transcript with a mismatched schema version or tool-schema flavor
   fails with `incompatible_transcript`, not a scoring failure. A manifest
   whose stages are not a contiguous prefix of the case script — or a
   proper-prefix manifest whose final transcript is not marked with a
   non-completed outcome — fails with `invalid_manifest`, not a
   scoring result. A transcript marked `completed` that lacks its
   successful `finish_draft` result (or a failure-marked one that contains
   it) fails with `corrupt_transcript`. A historical failure that reaches
   a successful finish under current contracts fails smoke with
   `outcome_divergence` and the re-record/retire message, never a silent
   verdict flip.
5. The trial flow is reproducible: for a committed multi-round case the
   runner reports one initial invocation plus one replan invocation per
   round in order, and an injected round-2 `turn_limit` fails the trial
   with `failed_at: round_2` and runs nothing further. Replay parity: each
   replay invocation constructs a fresh builder (unit test observes two
   distinct builder instances for a two-invocation trial), and replaying a
   round transcript against a continued initial builder is impossible by
   construction.
6. The budget projection equals the documented formula for a corpus with
   mixed replan modes, and the runner refuses to start when it exceeds
   `--max-calls` (unit test with a mock provider counting nothing).
7. Gate edge cases: candidate 0-pass vs baseline >0 fails; both 0 fails
   with `not_comparable`; equal stability with fewer candidate turns
   passes. Median turns provably excludes failed trials.
8. Variant isolation: two variants executed interleaved each receive
   exactly their own `PlannerConfig`; the harness performs no `os.environ`
   writes (asserted).
9. Adding a new case requires no runner code changes.
10. Case files and committed transcripts pass a secret/path scan in the
    recorder (no keys, no absolute paths).
11. The SPEC 7 Part C flip criterion is computable from one
    `--gate replan_mode` run per the Gate section's verdict rules.

## Out of scope

Vision-stage evaluation (analyses are recorded inputs), CI-paid provider
runs, statistical significance testing beyond run-count stability, UI.
