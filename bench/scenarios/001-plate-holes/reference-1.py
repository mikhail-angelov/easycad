import cadquery as cq

result = (
    cq.Workplane("XY")
    .box(60, 40, 5, centered=(True, True, False))
    .faces(">Z").workplane()
    .rect(44, 24, forConstruction=True).vertices()
    .hole(5.5)
)
