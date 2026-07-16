# EasyCAD Spec 6: Geometry Interpretation Rules

## Status

Approved implementation specification. Extends SPEC 4 and SPEC 5 without
adding compiler operations.

## Goal

Keep drawing interpretation rules explicit, generic, and independently
reviewable. The planner prompt must not contain fixture names, part names, or
hard-coded coordinates from regression drawings.

## Rule source

`app/draft_geometry_rules.py` is the single implementation source for these
rules. `plan_draft_specification` includes it as planner context; operation
schemas and compiler contracts remain defined by SPEC 4.

## Rules

1. A feature's workplane is its profile plane. Its extrusion is normal to that
   plane: XY -> Z, XZ -> Y, YZ -> X.
2. The drawing view that visibly shows a profile determines its workplane. A
   profile shown on an end face must be extruded through the thickness normal
   to that face.
3. A centred placement uses a named derived midpoint dimension. Expressions
   are not executable placement coordinates.
4. A circular or semi-circular cut open to a material face has its centre on
   that face. Its depth spans the material along the selected extrusion axis;
   it is not inferred from the circle radius.
5. If evidence does not establish a profile face or extrusion span, the draft
   must ask a user question or present an assumed proposal. It may not silently
   select an axis.

## Acceptance criteria

1. No planner prompt contains a fixture filename, a named test part, or a
   hard-coded fixture coordinate.
2. The real drawing flow verifies the resolved workplane, origin, and depth
   for any feature whose orientation is essential to the fixture.
3. The direct image-to-Feature-Graph planner is absent; DraftSpecification is
   the only LLM-owned intermediate representation, as required by SPEC 3.
