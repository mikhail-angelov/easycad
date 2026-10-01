import cadquery as cq

result = cq.Workplane("XY").box(60, 40, 5, centered=(True, True, False))
