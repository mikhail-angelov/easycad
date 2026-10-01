import cadquery as cq

result = (
    cq.Workplane("XY").box(80, 50, 5, centered=(True, True, False))
    .faces(">Z").workplane()
    .pushPoints([(-50 / 2, 0), (50 / 2, 0)])
    .hole(6)
)
