import cadquery as cq

result = (
    cq.Workplane("XY").box(60, 40, 5, centered=(True, True, False))
    .faces(">Z").workplane().circle(6).extrude(10)
    .faces(">Z").workplane().hole(5)
)
