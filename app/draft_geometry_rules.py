"""Shared drawing-to-feature geometry rules for the draft planner."""

from __future__ import annotations


DRAFT_GEOMETRY_RULES = """
Geometry interpretation rules:
* A workplane is the plane of the feature profile; its extrusion axis is
  perpendicular to that plane: XY -> Z, XZ -> Y, YZ -> X.
* Choose the profile plane from the drawing view that visibly shows the
  feature profile. A profile visible on an end face is extruded through the
  part thickness normal to that end face; do not choose an axis solely because
  the feature touches a top face.
* A feature centred across a span needs a declared derived midpoint dimension.
  Use that dimension ID in placement.origin; never put arithmetic directly in
  executable feature coordinates.
* For a circular or semi-circular cut that opens onto a material face, place
  its circle centre on that face and extrude it through the required thickness.
  The cut depth is the span along the extrusion axis, not the circle radius.
* If the drawing does not establish the profile face or extrusion span, return
  needs_input or an assumed proposal with a question. Do not silently choose
  an axis or placement.
""".strip()


def draft_geometry_rules() -> str:
    return DRAFT_GEOMETRY_RULES
