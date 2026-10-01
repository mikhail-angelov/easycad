import cadquery as cq

body = (
    cq.Workplane("XY")
    .box(100, 60, 40, centered=(True, True, False))
    .faces(">Z").shell(-2)
)
# Opening through the front (-Y) wall only: the tool spans the 2 mm wall with margin.
opening = cq.Workplane("XY").box(20, 6, 10).translate((0, -30, 15))
result = body.cut(opening)
