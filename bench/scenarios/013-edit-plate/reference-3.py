import cadquery as cq

result = (
    cq.Workplane("XY").box(80, 60, 6, centered=(True, True, False))
    .edges("|Z").fillet(5)
    .faces(">Z").workplane()
    .rect(80 - 16, 60 - 16, forConstruction=True).vertices()
    .hole(4)
)
