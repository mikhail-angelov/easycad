"""BRep facts from an OCCT shape (bench-SPEC §3.2).

All facts are read off the CAD-native BRep (loaded from a STEP file), not the
tessellated mesh: the STEP is exact, the mesh is an approximation whose error
depends on deflection. bbox/volume/solids/valid therefore live here; watertight
and the coordinate contract, which are mesh properties, live in `mesh.py`.
"""

from __future__ import annotations

from pathlib import Path

from OCP.Bnd import Bnd_Box
from OCP.BRepBndLib import BRepBndLib
from OCP.GProp import GProp_GProps
from OCP.BRepGProp import BRepGProp
from OCP.BRepCheck import BRepCheck_Analyzer


def load_shape(step_path: str | Path):
    """Load a STEP file and return its OCCT `TopoDS_Shape`.

    Uses cadquery's importer so the exact same code path serves reference and
    generated models — the harness must never measure one differently.
    """
    import cadquery as cq

    wp = cq.importers.importStep(str(step_path))
    return wp.val().wrapped


def bounding_box(shape) -> tuple[float, float, float]:
    """Tight axis-aligned bbox size (X, Y, Z) in mm.

    AddOptimal gives a tight box; plain Add is inflated by the shape tolerance,
    which at a ±0.5 mm budget eats a visible slice of it (bench-SPEC §3.2).
    """
    box = Bnd_Box()
    BRepBndLib.AddOptimal_s(shape, box, True, False)
    xmin, ymin, zmin, xmax, ymax, zmax = box.Get()
    return (xmax - xmin, ymax - ymin, zmax - zmin)


def bbox_bounds(shape) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
    """Tight bbox as ((xmin,ymin,zmin), (xmax,ymax,zmax)) in mm."""
    box = Bnd_Box()
    BRepBndLib.AddOptimal_s(shape, box, True, False)
    xmin, ymin, zmin, xmax, ymax, zmax = box.Get()
    return ((xmin, ymin, zmin), (xmax, ymax, zmax))


def volume_mm3(shape) -> float:
    """Solid volume in mm³, taken as a modulus.

    An inverted body reports a negative mass; abs() keeps the fact meaningful
    instead of silently comparing a negative number to the reference.
    """
    props = GProp_GProps()
    BRepGProp.VolumeProperties_s(shape, props)
    return abs(props.Mass())


def count_solids(shape) -> int:
    """Number of solids in the shape. `solids != 1` is the most common way to
    get a visually-right but unprintable model (bench-SPEC §3.2)."""
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopAbs import TopAbs_SOLID

    exp = TopExp_Explorer(shape, TopAbs_SOLID)
    n = 0
    while exp.More():
        n += 1
        exp.Next()
    return n


def is_valid(shape) -> bool:
    """BRepCheck validity — catches broken BRep geometry."""
    return BRepCheck_Analyzer(shape).IsValid()


def brep_facts(shape) -> dict:
    """All BRep facts for one shape as a JSON-ready dict."""
    (xmin, ymin, zmin), (xmax, ymax, zmax) = bbox_bounds(shape)
    return {
        "bbox_mm": [xmax - xmin, ymax - ymin, zmax - zmin],
        "bbox_bounds_mm": [[xmin, ymin, zmin], [xmax, ymax, zmax]],
        "volume_mm3": volume_mm3(shape),
        "solids": count_solids(shape),
        "valid": is_valid(shape),
    }
