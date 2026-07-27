import cadquery as cq

outer = (
    cq.Workplane("XY").circle(16)          # Ø32 bottom
    .workplane(offset=40).circle(12.5)     # Ø25 top
    .loft(combine=True)
)
inner = (
    cq.Workplane("XY").circle(14)          # Ø28 bore bottom
    .workplane(offset=40).circle(10.5)     # Ø21 bore top
    .loft(combine=True)
)
result = outer.cut(inner)
