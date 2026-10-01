import cadquery as cq

result = (
    cq.Workplane("XY").box(50, 50, 20, centered=(True, True, False))
    .faces(">Z").workplane().hole(16)
)
