import cadquery as cq

result = (
    cq.Workplane("XY")
    .box(50, 30, 6, centered=(True, True, False))
    .edges("|Z").fillet(5)
)
