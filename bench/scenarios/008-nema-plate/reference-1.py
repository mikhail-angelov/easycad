import cadquery as cq

result = (
    cq.Workplane("XY")
    .box(45, 45, 5, centered=(True, True, False))
    .faces(">Z").workplane()
    .hole(22)                                     # central bore
    .faces(">Z").workplane()
    .rect(31, 31, forConstruction=True).vertices()
    .hole(3.4)                                    # four mounting holes
)
