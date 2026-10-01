"""Compact CadQuery surface index for the generator prompt (SPEC23 W2).

One allow-list is the single source of truth for "what we support". Signatures
are read from the installed CadQuery by `inspect`, never typed by hand, so the
model sees real parameter names instead of recalling them from memory. The app
container has no CadQuery (execution lives in the worker), so the rendered index
is committed as `cq_index.txt` and regenerated with:

    .venv-poc/bin/python -m app.cq_index

`tests/test_spec23.py` fails when the committed file drifts from this module.
"""

import inspect
from pathlib import Path

INDEX_PATH = Path(__file__).with_name("cq_index.txt")

# (idiom, Workplane method, one-clause purpose). Order is the rendered order.
ALLOW_LIST: list[tuple[str, str, str]] = [
    ("2D sketch", "rect", "rectangle, centred on the workplane origin by default"),
    ("2D sketch", "circle", "circle by RADIUS, not diameter"),
    ("2D sketch", "polygon", "regular polygon; diameter of the circle through its vertices"),
    ("2D sketch", "slot2D", "obround slot; length is end to end"),
    ("2D sketch", "ellipse", "ellipse by radii"),
    ("2D sketch", "moveTo", "start an open profile"),
    ("2D sketch", "lineTo", "line segment to an absolute point"),
    ("2D sketch", "threePointArc", "arc through a mid point to an end point"),
    ("2D sketch", "polyline", "connected line segments through points"),
    ("2D sketch", "spline", "smooth curve through points"),
    ("2D sketch", "close", "close the current profile before extrude/revolve"),
    ("2D sketch", "offset2D", "offset closed wires outward (+) or inward (-)"),
    ("2D -> 3D", "extrude", "extrude pending wires by a distance (both=True: symmetric)"),
    ("2D -> 3D", "cutBlind", "cut pending wires to a depth (negative = into the solid)"),
    ("2D -> 3D", "cutThruAll", "cut pending wires through the whole solid"),
    ("2D -> 3D", "revolve", "revolve pending wires about an axis in workplane coords"),
    ("2D -> 3D", "twistExtrude", "extrude while rotating"),
    ("2D -> 3D", "loft", "loft between the pending wires of stacked workplanes"),
    ("2D -> 3D", "sweep", "sweep the pending profile along a path Workplane"),
    ("Primitives", "box", "box centred on the origin by default"),
    ("Primitives", "cylinder", "cylinder by height and RADIUS, centred by default"),
    ("Primitives", "sphere", "sphere by radius"),
    ("Primitives", "wedge", "tapered box"),
    ("Features", "hole", "through or blind hole by DIAMETER at each pending point"),
    ("Features", "cboreHole", "counterbored hole"),
    ("Features", "cskHole", "countersunk hole; cskAngle is the full cone angle, e.g. 82 or 90"),
    ("Features", "fillet", "round the selected edges"),
    ("Features", "chamfer", "bevel the selected edges"),
    ("Features", "shell", "hollow the solid, removing the selected faces; negative = inward"),
    ("Features", "text", "engrave (negative distance) or emboss (combine='a') text"),
    ("Placement", "workplane", "new workplane on the selected face"),
    ("Placement", "center", "shift the workplane origin"),
    ("Placement", "transformed", "rotate/offset the workplane"),
    ("Placement", "pushPoints", "pending points at explicit positions"),
    ("Placement", "rarray", "rectangular grid of pending points"),
    ("Placement", "polarArray", "circular pattern of pending points"),
    ("Selection", "faces", "select faces: '>Z', '<X', '|Z', '#Z', or a tag"),
    ("Selection", "edges", "select edges: '|Z', '>Z', combine with 'and'/'or'/'not'"),
    ("Selection", "vertices", "select vertices, e.g. as hole positions"),
    ("Selection", "tag", "name the current object for later selection"),
    ("Booleans", "union", "join another solid"),
    ("Booleans", "cut", "subtract another solid"),
    ("Booleans", "intersect", "keep the common volume"),
    ("Transforms", "translate", "move the whole object (moves its centre)"),
    ("Transforms", "rotate", "rotate about an axis through two points"),
    ("Transforms", "mirror", "mirror across a plane; union=True keeps both halves"),
]

# Words users type -> the API that does it. English and Russian, the product's
# two UI languages.
SYNONYMS: list[tuple[str, str]] = [
    ("bore, drill, through hole / отверстие, сверление", "hole"),
    ("counterbore / цековка", "cboreHole"),
    ("countersink, cone cut / зенковка", "cskHole"),
    ("round, rounded edge / скругление", "fillet"),
    ("bevel / фаска", "chamfer"),
    ("hollow, wall, enclosure / полый, стенка, корпус", "shell"),
    ("slot / паз", "slot2D + cutThruAll or cutBlind"),
    ("pocket, recess / карман, выемка", "rect + cutBlind"),
    ("boss, standoff / бобышка, стойка", "circle + extrude"),
    ("rib / ребро", "rect + extrude"),
    ("pattern, array / массив", "rarray or polarArray"),
    ("engrave, emboss / гравировка, надпись", "text"),
    ("split, slice / разрезать", "cut with an oversized box"),
    ("thread, helix / резьба", "the thread skill, not this index"),
]

# Parameters that only add noise to a one-line index.
_SKIP_PARAMS = {
    "self", "clean", "forConstruction", "tol", "glue", "makeWire", "includeCurrent",
    "scale", "parameters", "auxSpine", "normal", "transition", "sweepAlongWires", "fontPath",
}


def _param(p: inspect.Parameter) -> str:
    if p.default is inspect.Parameter.empty:
        return p.name
    default = p.default
    if hasattr(default, "toTuple"):  # cq.Vector reprs as "Vector: (...)"
        default = tuple(default.toTuple())
    return f"{p.name}={default!r}"


def signature(method: str) -> str:
    import cadquery as cq

    params = inspect.signature(getattr(cq.Workplane, method)).parameters.values()
    return f"{method}({', '.join(_param(p) for p in params if p.name not in _SKIP_PARAMS)})"


def build() -> str:
    lines = [
        "CadQuery Workplane methods supported here (real signatures from cadquery 2.8.0).",
        "Use only these and the idioms in the rules; do not invent methods or parameters.",
    ]
    idiom = None
    for group, method, purpose in ALLOW_LIST:
        if group != idiom:
            idiom = group
            lines.append(f"\n## {group}")
        lines.append(f"- {signature(method)} — {purpose}")
    lines.append("\n## User words -> API")
    lines.extend(f"- {words} -> {api}" for words, api in SYNONYMS)
    return "\n".join(lines) + "\n"


def load() -> str:
    return INDEX_PATH.read_text(encoding="utf-8")


if __name__ == "__main__":
    INDEX_PATH.write_text(build(), encoding="utf-8")
    print(f"wrote {INDEX_PATH} ({len(build())} chars)")
