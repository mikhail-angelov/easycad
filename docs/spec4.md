# EasyCAD Spec 4: Contract-Driven Draft Operations

## Status

Approved implementation specification. Extends SPEC 3 without changing its
specification-first user workflow.

## Goal

Make every operation that the trusted CadQuery compiler currently supports
available to the draft planner through one strict, shared operation contract.
The LLM tool schema, deterministic draft validation, planner instructions, and
trusted Feature Graph projection must agree on the same capabilities.

## Scope

Support the current compiler catalogue only:

- primitives: `box`, `cylinder`, `extrude`, `revolve`;
- cuts: `hole`, `through_hole`, `counterbore`, `countersink`, `slot`, `pocket`;
- construction: `rib`, `gusset`, `text`;
- modifiers: `fillet`, `chamfer`, `shell`, `mirror`;
- patterns: `hole_pattern`, `perforation_pattern`.

Threads and new CadQuery operations are out of scope.

## Shared Operation Contract

One declarative registry defines each canonical type's allowed operations,
required and optional parameters, target requirement, supported profile types,
and supported pattern types. It is the single source used by:

1. the LLM planner prompt;
2. the strict `submit_draft_specification` function schema;
3. deterministic specification validation; and
4. regression tests that compile representative operations through CadQuery.

Only canonical operation type names appear in the LLM contract. Compatibility
aliases remain accepted only when replaying already trusted Feature Graphs.

## Draft Representation

`SpecificationFeature` includes `profile` and `pattern`, matching the trusted
`FeatureOperation` representation. `project_from_specification` transfers both
unchanged into the Feature Graph.

### Profiles

`extrude`, `revolve`, and `gusset` require a profile of type `rectangle`,
`circle`, or `polyline`. A polyline has at least three two-dimensional points.
Patterns require a `circle`, `rectangle`, or `slot` profile.

### Patterns

The current compiler supports only `linear` and `polar` patterns. A linear
pattern requires `count`, `pitch`, and `axis`; a polar pattern requires
`count`, `angle_deg`, `axis`, and the feature's `radius` parameter.

## Strict LLM Tool Schema

The `features` item schema is a discriminated `oneOf` by canonical `type`.
Each variant forbids additional fields and declares its legal `operation`,
exact parameter names, and whether `profile` and `pattern` are required. The
outer tool call remains exactly `{ "specification": DraftSpecification }`.

## Validation And UX

Validation reports feature-linked diagnostics before Build when a feature
violates its operation contract. If a drawing omits a required modelling fact,
the planner must return `needs_input` plus a question, or an explicit
`assumed` proposal. After user input, the complete replan must supply the
required field; EasyCAD does not infer or repair engineering geometry itself.

## Acceptance Criteria

1. Every current compiler operation is present in the planner catalogue and
   strict tool schema.
2. A draft can express and project `profile` and `pattern` unchanged.
3. Invalid type/operation combinations, missing required parameters, invalid
   profile/pattern kinds, and extra parameter names fail before CadQuery.
4. Unit tests compile a representative feature for every registry entry.
5. Real DeepSeek user-flow tests reach STL for the existing bracket fixture and
   a second fixture whose geometry is expressible by the catalogue.
