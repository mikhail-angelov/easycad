# EasyCAD Spec 9: Feature Roster and Scoped Refinement

## Status

Proposed implementation specification. Builds on the minimal 3-endpoint flow
that replaced SPEC 3–7 (`app/main.py`, `app/minimal_model.py`,
`frontend/src/main.tsx`, 2026-07-18). Does not reintroduce SPEC 7's forced
multi-round structured review, lint-tier UI, exclusion records, or
incremental-replan locking — the "upload → immediate render → freeform
refine" loop stays the default interaction. Geometric constraints between
features (align, mate, offset-with-solver) are explicitly deferred; see
Non-goals.

## Goal

Today a user sees one opaque rendered solid and one freeform textarea.
`specification.features[]` already carries a per-feature id, label, type,
and status from the vision analysis and planner stages, and
`minimal_reliable_draft` (`app/minimal_model.py`) already decides, per
feature, whether it survived into the render — none of this reaches the
user. Two consequences: (1) a feature the planner silently dropped is
invisible, so the next freeform prompt is a guess rather than a targeted
fix; (2) a freeform instruction that should apply to one of several similar
features (two holes, three ribs) is resolved by the LLM from prose alone,
which is exactly the failure class `docs/AI_LEARNED.md` has spent two days
patching one incident at a time (mistargeted cuts, wrong feature edited).

This spec surfaces the feature list the backend already computes, gives
each feature a short browser-session name for navigation, and lets a
freeform instruction be scoped to specific feature IDs — turning "guess
which feature the LLM meant" into "the client already told it."

## Motivating evidence

- `app/minimal_model.py:_omit` already records why a feature was dropped
  (contract violation, unresolved reference, orphaned target, lint error,
  compile failure) but only as a string suffix appended to `label`; no
  endpoint or UI reads it today (confirmed: no test or frontend code
  references the `"— omitted:"` string).
- `frontend/src/types.ts` types `DraftSpecification.features` as
  effectively opaque (`[key: string]: unknown` on the parent interface);
  `ModelResponse` exposes only a single `description` string
  (`app/main.py:_description`) built from confirmed feature labels alone.
- `/api/model/refine` (`app/main.py:96-112`) sends the entire specification
  plus one prompt string with no reference to which feature(s) the user
  means; disambiguation is 100% implicit in the LLM's reading of the prompt
  against the full drawing analysis.
- The selection-sync interaction this spec proposes (click a list row,
  highlight it in the render, and back) already shipped once, in the
  SPEC 7 `ReviewWorkspace`/`DrawingPanel` (`selectedId` in
  `frontend/src/store.ts`, `data-feature-id` markers), before that UI was
  removed for unrelated reasons (forced structured review, not the
  selection mechanic itself). This spec revives that mechanic only, on top
  of the minimal render-first loop.

## Non-goals

- No geometric constraint solver, degrees-of-freedom tracking, or
  align/mate/offset relationships between features. Relationships a user
  wants (align, same size, offset from another feature) are expressed as a
  precisely-scoped freeform sentence referencing feature ids and resolved
  by the existing planner call, not by a symbolic solver. Deferred by
  agreement; a future spec may revisit if scoped freeform text proves
  insufficient.
- No true per-feature isometric 3D sub-renders. `Part C` uses axis-aligned
  bounding-box overlays computed from `resolved_feature_extent`
  (`app/draft_lint.py:96`), reusing the existing single STL render. A richer
  per-feature render would need named sub-meshes in the export (glTF
  instead of flat STL) or per-feature CAD-worker calls — out of scope here.
- No drag-to-edit, resize gizmos, or direct manipulation. Editing stays
  text-driven; this spec only makes the text precisely addressable.
- No return of SPEC 7's blocking review gate. The roster is an always-visible
  enhancement layer; a user can still type a fully unscoped freeform
  instruction exactly as today.

## Language

New CONTEXT.md terms this spec would introduce:

- **Feature roster**: the ordered list of every specification feature —
  confirmed or omitted — shown to the user with its alias, label, status,
  and (when resolvable) extent. Derived, never trusted as build input.
- **Feature alias**: a short, client-assigned display name (`A`, `B`, …)
  for one specification feature id, scoped to one browser session. Never
  interpreted by the server or the LLM directly — the client always
  resolves an `@<alias>` mention against its live alias map into a real
  feature id before that id reaches `referenced_feature_ids`; only the
  resolved id is meaningful past the browser, even though the literal
  `@<alias>` token may still appear in the submitted prompt text.
- **Scoped refinement**: a freeform instruction submitted together with one
  or more referenced feature ids, narrowing which feature(s) the planner
  should treat the instruction as primarily addressing.

## Part A: Feature roster

New module `app/feature_roster.py` (keeps `minimal_model.py` focused on
"produce a compilable draft"; roster-building is a read-only presentation
concern over the same draft):

```
FeatureRosterEntry:
  id                str
  label             str                       # unchanged, human label only
  status            "confirmed" | "unsupported"
  omission_reason   str | None                # see model change below
  extent            {minimum: [x,y,z], maximum: [x,y,z]} | None
```

`feature_roster(draft: DraftSpecification, values: dict[str, float]) ->
list[FeatureRosterEntry]` iterates `draft.features` in list order (already
stable — planner-assigned, or roster order after `minimal_reliable_draft`),
calling the existing `resolved_feature_extent(feature, values)` for each.
`values` comes from the existing `resolve_dimension_values(draft)`
(`app/specification.py`) — no new dimension-resolution logic.

**Model change**: add `SpecificationFeature.omission_reason: str | None =
None`. Change `app/minimal_model.py:_omit` to set the field instead of
mangling `label`:

```python
def _omit(feature: SpecificationFeature, reason: str) -> None:
    feature.status = "unsupported"
    feature.omission_reason = reason
```

No test currently asserts on the `"— omitted:"` label string (checked), so
this is a clean field split, not a breaking rename.

**Wiring**: `_model_response` in `app/main.py` computes
`values = resolve_dimension_values(draft)` (already available at that point
via `app/specification.py`) and calls `feature_roster(draft, values)` after
`minimal_reliable_draft`, adding `"features": [entry.__dict__ for entry in
roster]` to the response. `ModelResponse` (`frontend/src/types.ts`) gains a
typed `features: FeatureRosterEntry[]`.

**UI**: a new roster list in `Workspace` (`frontend/src/main.tsx`), each row
showing alias + label; unsupported rows show `omission_reason` and are
visually distinct (e.g. struck-through / muted), not hidden. This
subsumes the "surface what got silently dropped" gap identified before this
spec — same data, same list.

## Part B: Stable feature aliasing

Aliases are assigned **client-side**, not by the server, because the app is
intentionally stateless server-side (CONTEXT.md "Project session"). The
client keeps an alias while its feature ID remains in the roster. Planner
preservation of a changed feature ID is desirable but not required: if a
feature receives a new ID, the client assigns it a new alias and never tries
to infer identity from label or geometry.

Store `featureAliases: Record<string /* feature id */, string /* alias */>`
in the zustand store. On every `setModelResponse`, for each roster entry
whose `id` is not yet a key, assign the next unused letter (`A`–`Z`, then
`AA`, `AB`, … on overflow — 26 features before it matters). Never reassign
or garbage-collect an existing mapping within a session; a removed-then-
re-added feature gets a new id from the planner anyway (ids are planner-
assigned), so staleness is bounded to "an alias nobody's list shows
anymore," which is harmless.

**Hard rule**: the server never parses or resolves an alias — resolution is
one deterministic client-side lookup against the live alias map, done once
per `@<alias>` token before a request is built. A bare "B" typed without
`@` is never treated specially. This keeps zero new LLM-facing ambiguity in
the mechanism itself: the server always receives real feature ids in
`referenced_feature_ids`, independent of whatever the raw prompt text says.

## Part C: Selection sync (list ↔ 3D overlay)

For each roster entry with a non-null `extent`, render a translucent
`THREE.Box3Helper` (or `LineSegments` from `BoxGeometry` edges) in the
existing `ModelViewer` scene, positioned from `extent.minimum`/`maximum`,
tagged `userData.featureId`. This adds zero CAD-worker calls — the boxes are
drawn over the one STL mesh already being rendered.

- Hover/click a roster row → corresponding overlay highlights (color
  change); click a roster row → `selectedId` set, camera does not move.
- Raycast click against overlay meshes in the 3D view → resolves to a
  feature id → highlights + scrolls the matching roster row into view. Cast
  against an invisible filled `Mesh` proxy per feature (same box, drawn
  transparent), not against the `Box3Helper`/`LineSegments` itself —
  picking thin wireframe edges is a known-fiddly three.js `Raycaster`
  case; a filled proxy makes clicking anywhere inside the box register
  reliably, cheap even with 20+ overlays.
- Entries with `extent: None` (non-computable feature types) are list-only:
  no overlay, still selectable from the list, still usable in Part D.

This is the same `selectedId`-driven two-way sync SPEC 7's
`DrawingPanel`/`ReviewWorkspace` already had; this spec narrows it to
bounding-box overlays on the current single-render loop instead of a
separate schematic SVG endpoint.

## Part D: Scoped refine

Extend `PromptRequest` (`app/main.py`) with
`referenced_feature_ids: list[str] = []` (default empty — fully backward
compatible with today's unscoped freeform behavior).

`Workspace` uses a plain `<textarea>` — no contenteditable or rich-text
editor, keeping this on par with the rest of the spec's low-complexity
budget. Tracking the caret position, a regex over the text before the caret
(`/@(\w*)$/`) detects an open `@` mention; while it matches, a dropdown
lists current roster entries (`alias — label`) filtered by the typed prefix
against alias or label. Choosing an entry replaces the partially-typed `@…`
span with the literal token `@<alias> ` (e.g. `@B `) — the alias itself,
never the label — so the raw string stays a stable, greppable reference
instead of becoming indistinguishable plain prose.

Resolution happens once, at submit time, not at insertion: the client scans
the full prompt string for `@<token>` candidates and keeps only the ones
matching a key in the current `featureAliases` map (a stray `@` matching
nothing — an email, a pasted handle — is left as plain text, harmless).
Matches map to real ids, deduplicated, into `referenced_feature_ids`.
Because resolution and the submitted `specification` are built from the
same live client state in one synchronous action, and the UI allows only
one in-flight request at a time, an alias cannot go stale between being
typed and being resolved in this architecture — there is no background
roster update that could shift what "B" means mid-edit.

Roster rows and 3D overlays (Part C) are inspection and highlight controls
only; they never create request scope — only an `@<alias>` mention does.
Selection/hover is optional on every request, never required. A successful
refine clears the textarea.

`refine_model` repeats a cheap defensive check —
`id in {f.id for f in request.specification.features}`, not a roster
recompute — and drops anything that fails it before building
`user_inputs`. This guards a malformed or hand-crafted request, not a path
a well-behaved client should ever hit given the paragraph above, so
silently dropping there (rather than surfacing an error) is acceptable. It
passes the surviving `referenced_feature_ids` into
`plan_draft_specification`'s existing `user_inputs` dict (already carries
`freeform_instruction`; this is a sibling key, not a new mechanism). In
`app/ai_generation.py`, alongside the existing `if freeform_instruction:`
prompt-append block, add one conditional sentence when the list is
non-empty:

```
"The user's instruction specifically concerns feature id(s) {ids} "
"(labels: {labels}). Prefer editing only these unless the instruction "
"clearly names a different feature or a dependent feature must change to "
"keep the model valid."
```

This is additive to the current single-prompt construction — no new tool,
no new endpoint, no change to `draft_builder_tools`.

## Risks / open questions

- **Overlapping extents**: a hole fully inside its parent box has two
  overlays occupying the same space; a 3D click there is ambiguous. The
  roster list remains the reliable selection path in that case — the 3D
  click is a convenience, not the only way to select.
- **Extent tolerance**: `resolved_feature_extent`'s existing 0.5 mm
  tolerance is adequate for selection but the overlay should not be
  presented to the user as a precise outline of the feature.
- **Alias overflow / churn**: bounded (see Part B) but worth a small UI
  affordance (e.g. "24 more features" collapse) if a session accumulates
  many add/remove cycles — not blocking for v1.
- **Verification**: add unit tests for `_omit`, roster construction, and
  client alias generation. Add a real-provider E2E that uploads a drawing,
  submits a scoped refinement using selected real feature IDs, and confirms
  that the returned STL and roster reflect the change. Do not mock network
  calls in this E2E.

## Suggested rollout order

Part A (roster + omission surfacing) is independently valuable and has no
frontend selection complexity — ship first. Part B (aliasing) is a
prerequisite for Part D's `@mention` picker, but since Part D no longer
routes request scope through roster/overlay selection, Part C (3D overlay
sync) and Part D (scoped refine) are independent of each other and can be
built in parallel once B lands.
