import cadquery as cq

box = (
    cq.Workplane("XY")
    .box(100, 60, 40, centered=(True, True, False))
    .faces(">Z").shell(-2)
)
lid = cq.Workplane("XY").box(100, 60, 2, centered=(True, True, False)).translate((0, 0, 50))
# Lip outer = box inner (96 × 56) minus 0.2 mm clearance per side; 2 mm thick ring, 3 mm tall.
outer_x, outer_y = 96 - 0.4, 56 - 0.4
lip = (
    cq.Workplane("XY").rect(outer_x, outer_y).rect(outer_x - 4, outer_y - 4)
    .extrude(3).translate((0, 0, 47))
)
result = box.add(lid.union(lip))
