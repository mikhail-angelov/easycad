# EasyCAD Spec 3: Specification-First Drawing To CAD

## Status

Proposed replacement architecture. This specification supersedes the direct image-to-Feature-Graph generation and all LLM auto-repair paths.

## Goal

Build printable CAD models from ambiguous drawings through a user-confirmed specification, rather than attempting to infer and export a model in one pass.

## Non-Goals

- No automatic geometry repair after a CadQuery failure.
- No fallback to generated CadQuery source or legacy repair endpoints.
- No revision history, undo stack, or collaborative editing in the first version.
- No 3D preview or STL export while critical specification fields are incomplete or conflicted.

## User Workflow

```text
Upload drawing
  -> Vision analysis
  -> DraftSpecification
  -> Review form and 2D annotation preview
  -> User confirmations, values, selections, and optional free text
  -> Deterministic specification validation
  -> Feature Graph compilation
  -> CadQuery validation and 3D preview
  -> User refinement
  -> Revalidate and rebuild
  -> STL/STEP export
```

## DraftSpecification

`DraftSpecification` is the only model-owned intermediate representation between AI analysis and Feature Graph compilation.

### Fields

- `units`: canonical unit, initially millimetres.
- `dimensions[]`: `id`, label, value or expression, unit, source evidence, confidence, status, critical flag, allowed range.
- `features[]`: `id`, kind, operation, dimensions, placement, direction, target, source evidence, confidence, status, critical fields.
- `assumptions[]`: proposed value, rationale, affected IDs, status.
- `questions[]`: missing, ambiguous, or contradictory critical fields with alternatives and an optional numeric/text input.
- `annotations[]`: 2D marker placement, linked to a dimension, feature, or question.
- `free_text`: most recent user clarification.

### Statuses

- `confirmed`: extracted or user-entered value accepted for modelling.
- `needs_input`: critical value absent or ambiguous; blocks modelling.
- `assumed`: proposed value requiring explicit acceptance before modelling.
- `conflicted`: inconsistent values or geometry; blocks modelling.

Only `confirmed` values and explicitly accepted `assumed` values may be compiled.

## AI Calls

### Vision analysis

One vision-model request extracts observations from the image: dimensions, views, labels, candidate features, evidence, and confidence. It must not return CAD code or a final model.

### Draft planner

One planner-model request converts observations into `DraftSpecification`. It must identify missing and contradictory critical data rather than silently choosing values.

### Clarification patch

Structured edits are applied locally. A planner-model request is made only when the user enters free text or a structured edit creates a semantic conflict. It receives the current specification and user changes and returns a constrained specification patch with rationale. It may not return CAD code or mutate fields outside the patch.

## Review UI

The first post-upload screen is the specification review, not a 3D model.

- Show the source drawing with deterministic 2D annotations.
- Show all dimensions, features, assumptions, conflicts, and missing fields in one form.
- Provide proposed values, alternatives, units, numeric entry, and free-text clarification.
- Highlight all blockers simultaneously.
- `Validate specification` validates locally and requests a clarification patch only when needed.
- `Build 3D` is disabled until no critical field is `needs_input` or `conflicted` and no assumption remains unaccepted.

After a successful build, keep the same specification form alongside the 3D preview. Any edit returns the project to validation before another build.

## Frontend Architecture And UX

The web client is a desktop-first single-page application built with Vite, TypeScript, Preact, and Zustand. It replaces the current monolithic static HTML client. Mobile editing is out of scope for version one.

### Design baseline

Use `easycad-review.html` as the visual and interaction baseline: a clean, paper-toned workspace, a stable source-drawing panel on the left, a review panel on the right, numbered links between annotations and review items, and a persistent bottom action bar. Preserve this direct, calm visual style; do not introduce a separate product-dashboard shell.

The UI is for 3D-print hobbyists who understand dimensions but may not know CAD terminology. Prefer plain-language labels such as `Rounded corner` with optional technical detail such as `fillet`; never require CAD vocabulary to resolve a blocker.

### Review workspace

- Keep the source drawing visible while the user reviews the specification. Selecting an annotation highlights and scrolls to its review item; selecting a review item highlights its annotation.
- Order the review panel by action: missing inputs, conflicts, proposed critical values requiring acceptance, then non-blocking details.
- Give missing inputs, proposed values, conflicts, confirmed values, and omitted features distinct text labels and visual treatments. Color must not be the only status signal.
- Show confirmed values as read-only summaries with an explicit `Edit` action. Editing a model-affecting value marks the current build stale.
- Show automatically omitted unsupported features in a non-blocking `Not included in this model` warning section. Explain in plain language that EasyCAD cannot model that feature yet.
- A free-text clarification remains scoped to its linked unresolved question. While the planner patch is pending, show a local loading state; returned changes remain proposed for review and may not silently change unrelated fields.

### Action states

The persistent action bar always explains the next step in plain language.

- Before validation, it reports the exact blocker count and types. `Build 3D` is disabled with a visible reason.
- `Validate specification` runs the deterministic pre-build check and displays field-linked diagnostics in the relevant review items.
- After successful validation, `Build 3D` becomes the primary action.
- A successful build adds a 3D preview alongside the same review workspace and enables STL export.
- Specification diagnostics return focus to the relevant review item. System diagnostics preserve the specification and offer retry/reporting without implying that the user entered something wrong.

### Client state

Zustand owns the active browser-only project session: the `DraftSpecification`, source-drawing URL and selection state, local edits, validation result, build artifacts, and async request state. The browser is the canonical owner of the editable specification; it sends the current specification with validation and build requests. Reloading the page discards the session.

Keep the store small and explicit. Do not persist it, add revision history, or duplicate derived Feature Graph state in the client. API responses replace the relevant validated or built state so the UI always renders the current specification.

## Deterministic Validation

Validation must run without an LLM and report field IDs.

- Critical dimensions, feature sizes, positions, directions, and targets are present.
- Expressions resolve to finite values and compatible units.
- Lengths, thicknesses, radii, and depths are positive and within declared ranges.
- Feature placements are geometrically possible within their target body.
- Operation types come from the compiler capability registry.
- No duplicate IDs, unknown parameter references, cyclic expressions, or contradictory dimensions.
- Each supported feature has enough data to compile a Feature Graph operation.

## Build And Export

After specification validation, deterministic code converts `DraftSpecification` to the existing trusted Feature Graph. Existing compiler, worker, printable-solid, measurement, semantic, and export checks remain required.

Worker or semantic errors are converted into specification-linked diagnostics. They return the user to review; no LLM repair loop is invoked.

## Removal Requirements

Delete, rather than deprecate:

- `/api/projects/repair` and its frontend action.
- `repair_project`, `plan_repair`, `apply_repair_plan`, repair prompts, and repair history fields used only by that path.
- `finalize_project_with_auto_repair` and automatic retry logic.
- repair-only fixtures, tests, Make targets, and documentation claims.

## Acceptance Criteria

1. Upload produces a persisted-in-memory `DraftSpecification` and 2D annotations before any Feature Graph or CAD worker run.
2. A drawing with a missing critical dimension shows that field and cannot enable `Build 3D`.
3. A user can resolve all blockers in one form submission; simple structured edits require no additional LLM call.
4. A free-text clarification produces a constrained patch or an explicit unresolved question; it cannot silently alter unrelated confirmed fields.
5. A fully confirmed specification compiles to Feature Graph, validates, renders one solid, and exports STL.
6. Editing a confirmed parameter after preview invalidates the current build and rebuilds through the same specification validation path.
7. No repair endpoint, auto-repair prompt, or repair fallback remains in the application or tests.
8. Real and recorded fixtures cover a complete clarified drawing and an intentionally incomplete drawing that remains blocked.
