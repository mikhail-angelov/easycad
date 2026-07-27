import cadquery as cq

# Flanges built in a corner frame, then recentered on XY by the bounding box.
base = cq.Workplane("XY").box(60, 40, 5, centered=(False, True, False))    # X 0..60, Y -20..20, Z 0..5
upright = cq.Workplane("XY").box(5, 40, 40, centered=(False, True, False))  # X 0..5,  Y -20..20, Z 0..40
bracket = base.union(upright)

# Horizontal-flange holes: through Z at X=45, Y=±10 (15 mm from the far edge).
h_holes = (
    cq.Workplane("XY").workplane(offset=-1)
    .pushPoints([(45, 10), (45, -10)])
    .circle(2.75).extrude(7)
)
# Vertical-flange holes: through X at Y=±10, Z=25 (25 mm above the base).
v_holes = (
    cq.Workplane("YZ").workplane(offset=-1)
    .pushPoints([(10, 25), (-10, 25)])
    .circle(2.75).extrude(7)
)
result = bracket.cut(h_holes).cut(v_holes).translate((-30, 0, 0))
