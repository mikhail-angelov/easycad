import cadquery as cq

box = (
    cq.Workplane("XY")
    .box(100, 60, 40, centered=(True, True, False))
    .faces(">Z").shell(-2)
)
lid = cq.Workplane("XY").box(100, 60, 2, centered=(True, True, False)).translate((0, 0, 50))
result = box.add(lid)
