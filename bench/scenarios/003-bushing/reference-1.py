import cadquery as cq

result = (
    cq.Workplane("XY")
    .circle(10)          # OD 20
    .extrude(15)
    .faces(">Z").workplane()
    .hole(8.2)           # through bore
)
