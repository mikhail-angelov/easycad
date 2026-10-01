import cadquery as cq

result = cq.Workplane("XY").box(80, 60, 6, centered=(True, True, False))
