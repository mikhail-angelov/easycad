"""Shared drawing-to-feature geometry rules for the draft planner."""

from __future__ import annotations


DRAFT_GEOMETRY_RULES = """
Geometry interpretation rules:
* Use one global coordinate frame with the origin at the part's minimum-extent
  corner: the root feature's placement.origin is [0, 0, 0] and every other
  feature is located from that same corner, so no feature has a negative
  resolved coordinate. A drawing dimension measured from a part edge locates
  geometry from the matching face of the root feature in this frame, and the
  finished part must span exactly the declared overall dimensions on each axis.
* A workplane is the plane of the feature profile; its extrusion axis is
  perpendicular to that plane: XY -> Z, XZ -> Y, YZ -> X.
* Choose the profile plane from the drawing view that visibly shows the
  feature profile. A profile visible on an end face is extruded through the
  part thickness normal to that end face; do not choose an axis solely because
  the feature touches a top face.
* A feature centred across a span needs a declared derived midpoint dimension.
  Use that dimension ID in placement.origin; never put arithmetic directly in
  executable feature coordinates.
* A box placement.origin is its minimum-extent corner. Circle, slot, and
  pocket profiles are centred on placement.origin. Do not use a box-corner
  coordinate for a centred cut profile.
* When a user asks for an additive box at the centre or middle of a plane,
  that describes the box's geometric centre, not placement.origin. Declare
  derived coordinates equal to the plane centre minus half the box span on
  each in-plane axis, then use those coordinates as the box origin.
* For a pair of edge features, "centred on X" means each feature's geometric
  centre has the part's X midpoint, so the pair belongs on the opposite Y
  edges. Conversely, "centred on Y" places the pair on the opposite X edges.
  Apply the primitive's own origin convention when converting those centres
  to placement coordinates.
* For a circular or semi-circular cut that opens onto a material face, place
  its circle centre on that face and extrude it through the required thickness.
  The cut depth is the span along the extrusion axis, not the circle radius.
* When the drawing states that circular features are concentric, give them the
  same complete placement origin. A radius is a size, never a coordinate; use
  the dimension locating the shared centre on each in-plane axis.
* Never reuse a locating dimension from one coordinate axis for another axis.
  When a centre is symmetric across a known span, declare and use that span's
  derived midpoint dimension for the transverse coordinate.
* An outline drawn with a rounded or semi-circular end (an end arc of radius R
  about a centre, often shared with a concentric hole) must keep that arc in
  the model: add an additive cylinder with radius equal to the end arc radius
  and height equal to the plate thickness, centred on the arc centre and
  targeting the straight body it extends. Never flatten a drawn arc end into a
  plain box end and never drop it silently; if the arc centre or radius is
  unreadable, keep the feature with status needs_input and ask a required
  question.
* Additive bodies and cuts share one global coordinate frame. Place every
  feature so a cut actually intersects the material of its target: a cut whose
  origin lies outside its target's occupied extent is a placement error, not a
  valid interpretation. placement.origin is the start of the cutting solid;
  its positive extrusion must penetrate the target by a non-zero distance.
  Do not start a positive-depth cut on the outside face and extrude away from
  the material.
* If the drawing does not establish the profile face or extrusion span, return
  needs_input or an assumed proposal with a question. Do not silently choose
  an axis or placement.
* In particular, when a groove or cut is only described as centred on a span
  and its run direction or extrusion span is not dimensioned, it is ambiguous:
  add a required question with alternatives for running along that span or
  through the perpendicular material thickness. Do not mark either reading
  confirmed until the user answers.
* The run direction of a semi-circular channel or groove counts as dimensioned
  only when the drawing states a dimension along its axis. A narrative hint in
  the analysis (an axis word, "horizontal", or a construction strategy
  sentence) is an observation, not a dimension: keep the groove feature with
  your best-guess placement, but mark it assumed — never confirmed — and add a
  required question offering the possible run directions.
""".strip()


def draft_geometry_rules() -> str:
    return DRAFT_GEOMETRY_RULES
