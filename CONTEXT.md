# EasyCAD

EasyCAD turns a user-reviewed drawing specification into printable CAD models. The specification is the editable product state; CAD artifacts are derived from it.

## Language

**DraftSpecification**:
The canonical editable description of a drawing-derived CAD model, including extracted facts, user decisions, and unresolved items. A build derives a Feature Graph and CAD artifacts from this state.
_Avoid_: Project model, CAD project

**Build artifact**:
A non-editable result derived from a validated DraftSpecification, such as a Feature Graph, CAD result, preview, or export.
_Avoid_: Editable model, source of truth

**Project session**:
The browser-memory lifetime of one user’s DraftSpecification and its derived build artifacts. It ends when the browser page reloads; server-held source images also disappear when the server process restarts.
_Avoid_: Saved project, persistent project

**Critical input**:
A specification value required to create a valid CAD model. Model-extracted critical inputs require explicit user acceptance before they can be compiled.
_Avoid_: Automatically trusted dimension, implicit modelling decision

**Readiness prediction**:
A planner’s non-authoritative estimate that a specification can be built. Its usefulness must be established against observed build outcomes before it affects the user flow.
_Avoid_: Build approval, build gate

**Supported feature**:
A requested CAD feature whose Feature Graph operation has deterministic compilation and semantic evidence. Unsupported features are disclosed rather than approximated.
_Avoid_: Best-effort feature, inferred substitute

**Specification replan**:
A complete DraftSpecification returned by the planner after free text. It uses the original vision analysis and all session user inputs, replacing the prior DraftSpecification.
_Avoid_: Clarification patch, automatic geometry repair

**Acceptance**:
An explicit user decision to allow a model-extracted critical input to be compiled. Each proposal is accepted individually, whether retained or edited, though several decisions may be submitted together.
_Avoid_: Bulk acceptance, implicit approval

**Specification validation**:
The deterministic pre-build check of a DraftSpecification’s completeness and internal consistency. It does not establish whether the resulting geometry can be built.
_Avoid_: Geometry validation, render validation

**Build validation**:
The deterministic post-compilation checks that establish actual geometry, printable-solid, measurement, semantic, and export validity.
_Avoid_: Pre-build validation

**Specification diagnostic**:
A build or validation failure attributable to identified DraftSpecification fields or features, so the user can resolve it in review.
_Avoid_: Generic failure

**System diagnostic**:
A build failure independent of the DraftSpecification, such as worker unavailability or an internal compiler failure. It preserves the specification for retry or reporting.
_Avoid_: User-correctable specification error

**Model-affecting edit**:
A change to accepted dimensions, feature definitions, assumptions, or supported-operation choices that makes existing build artifacts stale. Changes to evidence and presentation do not affect the model.
_Avoid_: Cosmetic edit, annotation edit

**Print export**:
The STL artifact produced from a successfully validated build for use by a 3D-print slicer. STEP is outside the version-one scope.
_Avoid_: CAD-exchange export

**Measurement input**:
Version one accepts direct numeric values only: millimetres for lengths, degrees for angles, and unitless positive integers for counts. Radians, conversions, and formulas are rejected.
_Avoid_: Automatic unit conversion, mixed-unit input, expressions

**Feature contract**:
A generic feature record with a supported kind, operation, target, parameters, and placement. The compiler capability registry defines which fields are valid for each kind.
_Avoid_: Ad-hoc feature shape, parallel feature hierarchy

**Omitted feature**:
A specification feature excluded from the rendered model because it cannot be compiled reliably. It remains visible and addressable in the feature roster, never substituted or silently dropped.
_Avoid_: Unsupported approximation, hidden omission

**Feature roster**:
The ordered presentation list of every specification feature, including rendered and omitted features, with its status, omission reason, and optional spatial extent.
_Avoid_: Build input, feature graph

**Feature alias**:
A short browser-session display name assigned to one feature ID for roster navigation. It is UI-only and never leaves the browser; it expires when that feature ID disappears.
_Avoid_: Feature ID, server identifier

**Scoped refinement**:
A freeform model-change instruction accompanied by selected feature IDs. The client creates that selection only through `@` feature completion; the roster and 3D overlays are inspection-only. The client filters selections against the current roster and the server silently ignores stale IDs; omitted features may be selected even when they have no spatial overlay. Scope is advisory: the planner may also alter dependencies required for a valid model.
_Avoid_: Constraint solver, review gate

**Feature identity preservation**:
The planner should normally retain a changed feature's ID, but this is not required for refinement correctness. If an ID changes, the client assigns the replacement feature a new alias rather than guessing identity from label or geometry.
_Avoid_: Required ID lock, matching features by label or geometry

**Refinement scope lifetime**:
Feature chips selected through `@` completion apply to one submitted instruction only. A successful refinement clears both the instruction and its selected feature IDs.
_Avoid_: Persistent selection, implicit scope for the next instruction

**Reviewed specification**:
The supported, user-reviewed portion of a DraftSpecification that is compiled and checked for semantic completeness. Omitted features remain visible warnings but are outside its required geometry.
_Avoid_: Unreviewed drawing interpretation, implicit full-drawing coverage

**Confidence**:
Evidence context shown to help a user review an extracted item. It does not automatically approve, block, or omit supported model inputs.
_Avoid_: Automatic approval threshold, build authority

**Geometry dimension**:
A named editable numeric CAD input recorded in `dimensions[]`. Features reference its ID rather than owning a separate numeric field definition.
_Avoid_: Feature-local numeric field, duplicated dimension metadata

**Lint issue**:
A deterministic pre-build finding about a DraftSpecification, tied to specific feature IDs and classified as an error or warning.
_Avoid_: Provider geometry opinion, unnamed validation failure

**Review plan**:
The server-computed ordering of unresolved review items by build impact.
_Avoid_: UI-only sorting, cosmetic ordering

**Locked item**:
A confirmed or user-accepted specification item that incremental replan cannot change without a superseding clarification or exclusion.
_Avoid_: Prompt-only preservation request

**Item identity**:
The typed pair `{item_type, id}` used when IDs are only unique inside their specification collection.
_Avoid_: Ambiguous bare ID across collections

**Draft preview**:
A rendered build artifact created with semantic post-build gates skipped. It is informational and cannot be exported.
_Avoid_: Validated build, printable artifact
