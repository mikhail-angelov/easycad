import cadquery as cq

result = (
    cq.Workplane("XY")
    .box(80, 50, 25, centered=(True, True, False))
    .faces(">Z").shell(-2)
)
