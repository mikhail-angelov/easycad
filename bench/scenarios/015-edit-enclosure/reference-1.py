import cadquery as cq

result = (
    cq.Workplane("XY")
    .box(100, 60, 40, centered=(True, True, False))
    .faces(">Z").shell(-2)
)
