import cadquery as cq

result = (
    cq.Workplane("XY")
    .box(50, 50, 10, centered=(True, True, False))
    .faces(">Z").workplane()
    .cboreHole(6, 10, 5)     # Ø6 through, Ø10 counterbore 5 mm deep from the top
)
