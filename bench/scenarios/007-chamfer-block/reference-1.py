import cadquery as cq

result = (
    cq.Workplane("XY")
    .box(40, 40, 20, centered=(True, True, False))
    .faces(">Z").edges().chamfer(2)
)
