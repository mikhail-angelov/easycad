# EasyCAD Spec 5: Stateful Draft Builder Tools

## Goal

Replace the single large `submit_draft_specification` tool call with narrow,
typed calls that assemble a server-owned `DraftSpecification` transaction.

## Tool hierarchy

- Draft: `set_draft_metadata`, `add_dimension`, `add_assumption`,
  `add_question`, `add_annotation`, `finish_draft`.
- Primitives: `add_box`, `add_cylinder`, `add_extrude`, `add_revolve`.
- Cuts and construction: `add_hole`, `add_counterbore`, `add_countersink`,
  `add_slot`, `add_pocket`, `add_rib`, `add_gusset`, `add_text`.
- Modifiers and patterns: `add_fillet`, `add_chamfer`, `add_shell`,
  `add_mirror`, `add_pattern`.

Each tool has flat strict arguments for exactly one operation. In particular,
`add_extrude`, `add_revolve`, and `add_gusset` require a profile in their own
argument schema; an operation cannot be appended without it.

## Transaction

The server creates a draft session, applies successful calls immediately, and
returns a short acknowledgement or a field-specific tool error. `finish_draft`
performs complete graph validation and returns the accumulated JSON.

On replan, a new session begins with the previous draft. Addressable replace
tools modify only an existing feature/dimension and preserve user-confirmed
items unless a user clarification explicitly supersedes them.

## Errors

Malformed LLM tool arguments are provider-contract errors, not user questions.
Engineering uncertainty remains a user-facing dimension, assumption, or
question. No feature is silently dropped.

## Acceptance

1. A hex head must be created through `add_extrude` with a polyline profile.
2. An invalid tool call does not mutate the draft.
3. `finish_draft` returns the assembled complete draft only after graph checks.
4. Real bolt E2E reaches STL using logged narrow tool calls.
