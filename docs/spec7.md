# EasyCAD Spec 7: Progressive Review Loop

## Status

Proposed implementation specification. Extends SPEC 3–6 without adding
compiler operation types. The planner evaluation harness is SPEC 8 and is a
separate deliverable; only Part C's default flip depends on it.

## Goal

Move verification earlier and make each review round cheaper and more
legible. Four independently shippable parts:

- **A** — deterministic pre-build checks with feature-named diagnostics.
- **B** — geometry the user can see every review round.
- **C** — a replan that edits the stored specification instead of
  regenerating it.
- **D** — a review UI ordered by what actually blocks the build, with
  one-click structured actions.

## Motivating evidence

Recorded real-provider sessions of 2026-07-16 (`logs/llm_responses.jsonl`,
`logs/planner_context_*.json`, `artifacts/real-user-flow-{3,a3}/`) show five
recurring failure classes: drawn geometry silently dropped (R30 base end);
cuts placed outside their target body; replan deadlocks against byte-exact
preserved snapshots (48-turn loops); unanswerable review questions caused by
zero-valued placement dimensions; cosmetic questions drip-fed across rounds.
Every rule below closes one observed class. No rule may reference a fixture
name, part name, or fixture coordinate (SPEC 6).

## Language

New CONTEXT.md terms introduced by this spec:

- **Lint issue**: a deterministic pre-build finding about a resolved
  DraftSpecification, named to specific features, with severity `error`
  (blocks build) or `warning` (shown in review).
- **Review plan**: the server-computed ordering of unresolved review items by
  build impact.
- **Locked item**: a confirmed or user-accepted item the incremental replan
  may not modify; only a clarification or explicit exclusion unlocks it.
- **Item identity**: the pair `{item_type, id}` with `item_type` one of
  `dimension | feature | assumption | question | annotation`. Dimensions and
  features share one id namespace; the other collections do not, so every
  cross-collection reference in this spec uses the typed pair, never a bare
  id.
- **Draft preview**: a build artifact rendered with semantic validation gates
  skipped; informational, never exportable.

## Part A: deterministic pre-build checks

New module `app/draft_lint.py`.

```
LintIssue:
  rule         str            # stable snake_case rule id, e.g. "cut_misses_target"
  issue_id     str            # instance identity, see below
  severity     "error" | "warning"
  feature_ids  list[str]
  message      str            # names the feature(s) and the violated invariant
  suggestion   optional {feature_id, field_path, value}   # enables Part D4
```

`issue_id = "<rule>@<sorted feature_ids joined by '+'>"`, with a third
`@<qualifier>` segment that is **mandatory for rules able to emit multiple
findings for one feature set** and omitted otherwise. Each rule defines its
qualifier: `overall_extent_mismatch` → the axis (`x|y|z`), one finding per
violated axis; `negative_origin` → the axis of the offending coordinate,
one finding per coordinate. `issue_id` is the stable discriminator
`review_plan` (D1) references; two findings in one draft never share it.

### A1 Analysis coverage

**Identifier contract.** `_normalize_analysis_features` guarantees every
normalized analysis feature carries a non-empty snake_case `id`, unique
within the analysis: a missing or empty id becomes `feature_<index>` (its
list position), and a duplicate id gains a `_<n>` suffix in list order. The
same normalizer runs on recorded/replayed analyses, so legacy fixtures
satisfy the contract without migration. The coverage source set is exactly
the normalized ids.

`SpecificationFeature` gains `source_feature_ids: list[str]` (mirrors
`FeatureOperation.source_feature_ids`); the planner tool schema exposes it and
the planner prompt requires it. An analysis feature id is *covered* when:

1. some specification feature lists it in `source_feature_ids`, or
2. a specification feature has the same id, or
3. an assumption's `affected_ids` or a question's `field_id` equals it.

Uncovered analysis feature ids are a `finish_draft` rejection listing the ids
(the planner self-corrects before the user ever sees the draft). No text
matching; id equality only.

**Coverage lifecycle.** Clause 3 (assumption/question coverage) is a
*temporary review state*, valid only while the item is unresolved — otherwise
dropped geometry could hide forever behind an accepted assumption. At the
build gate the rule tightens: every analysis feature id must satisfy clause
1 or 2 — where the covering feature may have any status, including
`unsupported` (the existing omitted-feature record) — or appear in the
exclusion record (D3). A violation is a 422 diagnostic with stage
`analysis_coverage`.

**Exclusion record.** `DraftSpecification` gains `exclusions: [{feature_id,
source_feature_ids, reason}]`. Entries are *derived*, never trusted: only
the D3 validate path creates them (from the validated `excluded_feature_ids`
action; no planner tool exposes the field), and every endpoint that accepts
a client-submitted specification re-validates the record as untrusted input —
each entry's `source_feature_ids` must be a subset of the normalized
analysis feature ids, its `feature_id` must not name a live specification
feature, and `reason` must be non-empty; any violation is a 422. Valid
entries count as covered-by-exclusion and surface in review as omission
warnings.

**Trust boundary** (explicit, because the app is stateless by design —
CONTEXT.md project session): the client owns the round-tripped document,
including `analysis` itself, so this gate defends *pipeline integrity*
(planner and UI flows cannot silently drop geometry), not against a hostile
API caller — who could equally edit the analysis feature list. Consistency
of the exclusion record is the enforceable property; authenticity would
require persisting or signing drafts, which SPEC 7 deliberately does not
introduce.

### A2 Geometry lint rules

**Value resolution.** A new non-raising partial resolver
`resolve_dimension_values(draft) -> (values: dict[str, float],
unresolved_ids: list[str])` is extracted from `validate_specification`'s
value loop (which keeps its raising behavior for build). Lint uses the
partial resolver: a rule fires for a feature only when every value that rule
needs is resolved; features touching unresolved values are reported in
`unevaluated_feature_ids`, never guessed.

**Extents.** `resolved_feature_extent(feature, values) -> Extents | None`
supports: box (plane XY), cylinder (any plane), hole/through_hole (cylinder
along the plane normal), pocket/slot (box). `None` (extrude, revolve, modify
types) skips the feature — rules only fire on computable extents, so they
cannot false-positive on unsupported shapes. Tolerance 0.5 mm everywhere.

**Footprint overlap fraction** (for cuts): on the plane perpendicular to the
cut's extrusion axis (plane → axis per SPEC 6), the fraction of the cut's 2D
bbox footprint area that overlaps the target's footprint. The extrusion axis
is deliberately excluded so intentional through-cuts and boundary grooves
(a cut centred on a face, half outside the body) score high; only an
in-plane misplacement scores low.

Each row below states the **invariant that must hold**; the issue fires when
the invariant is violated on evaluable extents. (Example: `cut_misses_target`
fires when the intersection is empty; `cut_mostly_outside_target` fires when
the footprint overlap fraction is below 0.25.)

| Rule id | Severity | Invariant (issue fires when violated) |
|---|---|---|
| `cut_misses_target` | error | a cut's 3D extent intersection with its target's extent is non-empty |
| `cut_mostly_outside_target` | warning | a cut's footprint overlap fraction ≥ 0.25 |
| `additive_disconnected` | error | every non-root additive extent intersects **its target's** extent (the product contract is one connected solid — root is the first feature, every later additive names a target; multi-body parts are out of scope, consistent with the post-build `solid_count == 1` check) |
| `negative_origin` | error | resolved origin coordinates ≥ −tolerance (SPEC 6 frame rule) |
| `overall_extent_mismatch` | warning | union of additive extents matches the declared overall dimensions per axis (flags both overflow and underfill) |
| `through_hole_short` | warning | through_hole depth spans its target's thickness along the hole axis |

**Overall-dimension mapping** (for `overall_extent_mismatch`).
`SpecificationDimension` gains an optional `role: "overall_x" | "overall_y" |
"overall_z" | null`, exposed in the tool schema and requested by the planner
prompt for the drawing's declared overall sizes. Axis mapping prefers `role`;
when absent it falls back to the id lists already used by
`_number_parameter` (`overall_length`/`length` → x, `overall_width`/`width`/
`depth`/`overall_depth` → y, `overall_height`/`height` → z). The rule is
skipped for an axis with no mapping.

`cut_mostly_outside_target`, `overall_extent_mismatch`, and
`through_hole_short` are promoted to `error` only after SPEC 8 shows a
false-positive rate of zero on the case corpus.

### A3 Integration points

1. `DraftBuilder` finish path: lint **errors** on evaluable features reject
   `finish_draft` with the issue messages (same self-correction contract as
   reference issues). Unevaluated features never block the planner.
2. `/api/specifications/analyze` and `/api/specifications/validate` responses
   gain `lint: {issues: [LintIssue], unevaluated_feature_ids: [str]}`. On
   analyze — where unresolved critical values are expected and legitimate —
   this is the partial-resolver path; nothing about lint changes the
   endpoint's existing success behavior.
3. `/api/specifications/build` returns a 422 specification diagnostic with
   stage `draft_lint` when errors exist — before compile, before the worker.
   At build time all values are resolved, so `unevaluated_feature_ids` must
   be empty; a non-empty set at build is itself a diagnostic.

### A4 Run metrics

New module `app/run_metrics.py` appends one JSON line per planner run to
`logs/planner_runs.jsonl`, written on every exit path of `_run_draft_builder`:

```
{created_at, planner_run_id, planner_mode, model,
 outcome: completed | turn_limit | provider_error | planner_stopped,
 turns_used, tool_calls, tool_errors, finish_rejections: {reason: count},
 lint_errors, lint_warnings, duration_ms}
```

Every exit path maps to exactly one outcome: `completed` — `finish_draft`
accepted; `turn_limit` — the turn cap raised; `provider_error` — transport
exception, HTTP ≥ 400, or a malformed/unparseable provider response;
`planner_stopped` — a response with no tool calls before `finish_draft`.
Invalid JSON tool arguments are not an exit path — they increment
`tool_errors` and the run continues. This enum is the single outcome
vocabulary; SPEC 8 transcripts reference it rather than defining their own.

## Part B: per-round geometry

### B1 Schematic (no worker, no LLM)

New module `app/draft_preview.py`: `draft_schematic(draft) -> {front, top,
right: svg}` composed from resolved extents — additive features as filled
shapes (box → rect, cylinder → circle in-plane / rect in side views), cuts as
dashed outlines. Every SVG element carries its feature id.

Non-computable features (extent `None`): when the feature's origin fully
resolves, a hatched fixed-size square (10 model units) anchored at that
origin, drawn in sorted feature-id order with overlaps permitted — no
collision layout, so snapshots are deterministic; when the origin does not
resolve, no in-canvas element — the feature is listed in a per-view
`approximate` legend, also in sorted id order.

Endpoint `POST /api/specifications/schematic` → `{views, lint}`. Budget:
p95 < 100 ms; the test asserts no subprocess is spawned.

Frontend: a `Schematic` tab in `DrawingPanel` beside `source`/`model`;
hovering a review row highlights the matching SVG element via the existing
`selectedId` store field, and vice versa.

### B2 Draft preview build

`POST /api/specifications/build?mode=draft` runs the full deterministic
front half unchanged — specification validation, feature contracts, trusted
feature-graph compilation — and the CadQuery worker with
`render_views=True`. Only the three semantic post-build validators are
skipped: `validate_generation_geometry`, `validate_feature_measurements`,
`validate_feature_coverage`. A compile or worker failure returns the same
error shape as a normal build (`status`, `stage`, `diagnostics`,
`repair_hints`), never `draft_preview`.

Success responds `status: "draft_preview"` with renders, bounding box, and
per-feature measurements as informational fields, and sets
`project.generation.semantic_status = "draft_preview"`. Enforcement of
non-exportability lives in the export path: `/api/projects/export` rejects
any project whose `generation.semantic_status != "success"` with a 409
diagnostic — the status label alone is not the boundary.
Frontend: a `Preview` button in `ReviewWorkspace` available on every round.

## Part C: incremental replan

Mode flag `EASYCAD_REPLAN_MODE = full | incremental`; default `full` until
the SPEC 8 gate (below) passes, then `incremental`.

1. **Seeding.** `DraftBuilder.seed(previous, locked_items)` pre-populates the
   builder with the previous specification in original order. `locked_items`
   is a set of item identities `{item_type, id}` (assumption and question ids
   are only unique within their collections): confirmed or accepted items
   minus `_clarification_superseded_ids(...)` minus `excluded_feature_ids`
   (Part D3).
2. **Editing tools.** `add_*` with an existing unlocked id replaces it
   (existing semantics; the tool name fixes the collection, so the identity
   is unambiguous). `add_*` on a locked identity returns `ok=false` with a
   message naming the clarification requirement — enforcement is at call
   time, not at finish. New tools: `remove_item {item_type, id, reason}`
   (unlocked identities only; reason is recorded on the run metrics record)
   and `resolve_question {id}` (questions collection only, so a bare id is
   sufficient).
3. **Finish contract.** Every question whose clarification was provided is
   resolved or replaced; lint errors are absent. The byte-exact
   preserved-snapshot comparison and the `required_*_ids` completeness check
   do not exist in this mode — the server already holds every item.
4. **Context payload.** The replan request sends user inputs, the open and
   superseded items in full, and only an identity summary of locked items —
   a list of `{item_type, id}` pairs, matching the item-identity definition —
   not the complete previous specification.
5. **Turn cap** 16 (`MAX_INCREMENTAL_REPLAN_TURNS`).
6. **Gate.** The default flips to `incremental` only when a SPEC 8
   `--gate replan_mode` run passes on every case under SPEC 8's verdict
   rules (paired variants identical except `replan_mode`; stability first,
   passing-trial median turns as tie-break; zero-pass edge cases defined
   there).

## Part D: review triage and one-click actions

### D1 Review plan

`analyze` and `validate` responses gain `review_plan: [{tier, item_type,
item_id, reason}]`. `item_type` here is the item-identity enum **plus**
`lint_issue` (item_id = `LintIssue.issue_id`, so repeated firings of one
rule stay distinct and stable) **plus** `exclusion` (item_id = the excluded
`feature_id`), which is how A1's exclusion entries surface in review:

| Tier | Contents |
|---|---|
| 1 | required questions |
| 2 | critical dimensions in `needs_input`/`conflicted`; lint errors |
| 3 | assumed dimensions, features, and assumptions awaiting acceptance |
| 4 | lint warnings; omitted features; exclusion entries (omission warnings with their recorded `reason`) |
| 5 | informational (confidence context, non-critical items) |

`ReviewWorkspace` renders sections in plan order and adds one bulk action:
accept every tier-3 item (existing acceptance mechanics, one click).

### D2 Structured answers

`QuestionRow` renders `alternatives` as choice buttons (the
`answerAlternative` handler exists) with free text as the fallback, never the
primary input.

### D3 Explicit exclusion

`SpecificationEditRequest` gains `excluded_feature_ids: list[str]`. The
server treats exclusions as supersessions (removed from preservation and
requirement sets in `full` mode; unlocked and removable in `incremental`
mode), records each in `DraftSpecification.exclusions` (A1 lifecycle), and
appends one deterministic prompt line naming the excluded ids.
Frontend: an `Exclude` action on every feature row.

**Dangling references.** Exclusion is applied server-side, deterministically,
before any replan, so the returned draft always passes
`review_reference_issues`:

- another feature's `target` names the excluded id → the request is rejected
  with a 422 listing all dependents (no cascade delete);
- questions whose `field_id` is the excluded id → removed with it (moot);
- annotations → the excluded id is dropped from `field_ids`; an annotation
  whose primary `field_id` is the excluded id is removed;
- assumptions → the excluded id is dropped from `affected_ids`; the
  assumption itself is retained (it may cover other items and stays
  informational when `affected_ids` empties).

`placement.reference` holds CadQuery selector text, never a feature id
(enforced by the builder), so it needs no rewrite.

### D4 Apply suggested fix

`SpecificationEditRequest` gains `feature_field_edits: {feature_id:
{field_path: value}}`, restricted to `placement.origin[i]` coordinates and
numeric parameter fields; values must be plain numbers (dimension-id
references are out of scope for v1). When a validate request contains only
`feature_field_edits` (no clarifications, no acceptances), the server applies
them directly to the specification and re-runs validation and lint — **no
planner call**.

Edit model: **atomic**. The request is validated in full first — every
feature id must exist, every field path must be on the whitelist and present
in that feature, every value numeric; any violation rejects the entire
request with a 422 listing *all* invalid edits, and no edit is applied. The
server operates on a deep copy of the submitted specification (the caller's
payload is never mutated in place) and the response returns the complete
updated specification plus fresh `lint`. Lint issues carrying a `suggestion`
render an `Apply` button that posts exactly this edit.

## Sequencing

A → B1 → D1/D2 → B2 → D3/D4 → C. Each part ships and is testable alone; no
part waits on SPEC 8 except the Part C default flip.

## Acceptance criteria

1. A cut whose extent never intersects its target is rejected before the
   worker runs, with a diagnostic naming the cut feature (unit test, no
   provider).
2. Geometry edge cases prove the footprint rule: a cut overlapping its
   target by under 25% of its footprint yields `cut_mostly_outside_target`
   (warning, not error); a through-cut whose depth exceeds the target
   thickness and a boundary groove centred on a target face yield **no**
   cut-placement issue.
3. An uncovered analysis feature id rejects `finish_draft` in a recorded
   tool-call replay (same mechanism as `test_l_bracket_groove_replay`);
   analyses with missing or duplicate feature ids are normalized to unique
   ids deterministically.
4. Coverage lifecycle: a draft whose analysis feature is covered only by an
   accepted assumption (no specification feature, no exclusion record) is
   rejected at build with stage `analysis_coverage`; the same draft with the
   feature recorded in `exclusions` builds.
5. Linting a draft with unresolved critical values (the normal `analyze`
   output) returns evaluable issues plus `unevaluated_feature_ids`, and
   never raises.
6. `/api/specifications/schematic` returns three SVGs for both recorded
   fixture specifications without spawning a subprocess; a specification
   containing a non-computable feature renders it as the deterministic
   placeholder (resolved origin) or legend entry (unresolved origin), and
   two consecutive calls produce byte-identical SVGs.
7. `?mode=draft` returns renders for a specification that fails
   overall-extent validation; a worker failure in draft mode returns the
   normal error shape; `/api/projects/export` rejects a `draft_preview`
   project with a 409.
8. `logs/planner_runs.jsonl` gains exactly one record per planner run, for
   every outcome class — completed, turn_limit, provider_error (transport,
   HTTP, and malformed-response variants), and planner_stopped — with no
   duplicates.
9. Replaying the recorded a3 rim clarification in incremental mode: locked
   items are byte-identical after replan (server-owned), the superseded rim
   feature is replaced or removed, the run stays within 16 turns, and no
   snapshot-equality code executes.
10. Item-identity collisions are handled: with a question and a feature
    sharing an id, locking and `remove_item` act only on the identity named
    by `{item_type, id}` (unit test).
11. Every unresolved item appears in `review_plan` exactly once; two lint
    issues from the same rule on different features appear as distinct
    entries keyed by `issue_id`, as do two findings of one rule on the same
    features that differ only by qualifier (`overall_extent_mismatch` on x
    and y); `ReviewWorkspace` renders sections in plan order.
12. `excluded_feature_ids` removes the feature in the returned specification
    without any free-text prose; excluding a targeted feature fails with the
    dependent ids; after an exclusion, `review_reference_issues` reports no
    dangling question, annotation, or assumption references. A
    client-submitted exclusion entry whose `source_feature_ids` are not
    normalized analysis ids, or whose `feature_id` names a live feature, is
    rejected with a 422 on both validate and build.
13. A suggestion-bearing lint issue is cleared by one `feature_field_edits`
    request with zero LLM calls; a request containing one invalid edit among
    valid ones applies nothing and lists every invalid edit.
14. No planner prompt contains a fixture filename, named test part, or
    hard-coded fixture coordinate.

## Out of scope

STEP export, project persistence, provider/prompt evaluation (SPEC 8), new
compiler operation types, changes to the CadQuery worker sandbox.
