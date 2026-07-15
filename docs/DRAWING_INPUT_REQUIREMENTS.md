# Drawing Input Requirements

## Purpose

These rules make the drawing-to-CAD pipeline reproducible. A clear image alone is not enough: the model must be able to determine the size, position, direction, and depth of every printable feature.

## Input Modes

### Engineering drawing

Use this mode for a model expected to export without manual geometry correction. The drawing must include:

1. Front, top, and right orthographic projections.
2. An isometric view for complex or ambiguous geometry.
3. A single unit system, normally millimetres.
4. Overall X, Y, and Z dimensions.
5. Position dimensions for holes, pockets, slots, and other critical features.
6. Feature dimensions and direction: diameter/radius, length, width, depth, and the face or axis from which a cut is made.
7. Centre lines and symmetry marks where a position is implied by symmetry.
8. A section view when internal, hidden, or depth geometry is not unambiguous from the projections.

The upload quality gate requires the author to confirm the first six items before calling the LLM. It does not claim to verify linework or dimensions from pixels; doing that deterministically would require a drawing parser. The confirmation makes the input contract explicit and keeps the pre-LLM check reproducible.

### Sketch or photo

This mode accepts a single sketch, photograph, crop, or incomplete drawing. It is suitable for exploration but produces a `needs_review` warning by default. Ambiguous dimensions, cut directions, hidden geometry, and feature positions must be reviewed before printing.

## Feature Annotation Rules

- Holes: give diameter, axis, depth or `THRU`, and X/Y position from named datums.
- Pockets and top slots: give length, width, depth, top/bottom face, and cut direction.
- Rounded ends, notches, fillets, chamfers, and countersinks: give radius or angle and the affected edge/face or centre.
- Repeated holes and perforations: give count, pitch, pattern axis or polar centre, and first-feature location.
- Text: give exact content, size, face, orientation, and whether it is engraved or embossed.

## L-Bracket Example

For an L-bracket, specify base length/width/height; upright thickness and total height; the upright's position from a base datum; each hole centre; and the top-slot footprint plus depth from the upper face. Include a right-side projection or section that proves the top cut goes downward into the upright.

## Gate Outcomes

- `engineering`: missing confirmations block generation before Gemini or DeepSeek is called, returning `input_quality` with the missing items.
- `sketch`: generation proceeds with an explicit warning recorded in the project.
