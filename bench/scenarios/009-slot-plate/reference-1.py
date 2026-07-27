import cadquery as cq

result = (
    cq.Workplane("XY")
    .box(80, 30, 4, centered=(True, True, False))
    .faces(">Z").workplane()
    .slot2D(30, 6, angle=0)      # 30 mm overall length, 6 mm width, rounded ends
    .cutThruAll()
)
