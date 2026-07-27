"""Mesh normalization and mesh-level facts (bench-SPEC §3.1, §3.2).

STL, by construction, does not share vertices between triangles, so a raw load
reports *every* model as non-watertight. We normalize first — merge vertices on
a fixed tolerance, drop degenerate/duplicate faces — then measure. Both raw and
normalized facts are kept so a surprising verdict can be traced.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import trimesh

# Merge tolerance for vertex welding. An order of magnitude below the 0.5 mm
# tolerance budget, so welding never moves a functional surface.
MERGE_TOL = 1e-4


def load_mesh(stl_path: str | Path) -> trimesh.Trimesh:
    """Load an STL with trimesh's own processing (welds coincident vertices)."""
    m = trimesh.load(str(stl_path), file_type="stl", process=True)
    if isinstance(m, trimesh.Scene):
        m = m.dump(concatenate=True)
    return m


def normalize(mesh: trimesh.Trimesh) -> trimesh.Trimesh:
    """Weld vertices, drop degenerate and duplicate faces.

    Without this, watertight is always False (STL never shares vertices) and the
    mesh volume is unreliable. Returns a copy; the input is left untouched.
    """
    m = mesh.copy()
    m.merge_vertices(merge_tex=True, merge_norm=True)
    m.update_faces(m.nondegenerate_faces())
    m.update_faces(m.unique_faces())
    m.remove_unreferenced_vertices()
    return m


def coordinate_contract(mesh: trimesh.Trimesh, tol_z: float = 0.1, tol_xy: float = 0.5) -> dict:
    """Z-up, part sitting on XY (z_min ≈ 0), centered on XY (bench-SPEC §3.1).

    Checked BEFORE shape comparison: a part lying on its side is a defect, not
    an alternative solution, and its own failure class must not be masked by a
    downstream shape_mismatch.
    """
    lo, hi = mesh.bounds
    cx, cy = (lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2
    return {
        "z_min_mm": float(lo[2]),
        "xy_center_mm": [float(cx), float(cy)],
        "ok": bool(abs(lo[2]) <= tol_z and abs(cx) <= tol_xy and abs(cy) <= tol_xy),
        "tol_z_mm": tol_z,
        "tol_xy_mm": tol_xy,
    }


def body_count(mesh: trimesh.Trimesh) -> int:
    """Number of connected components (disconnected bodies).

    This is the construction-independent answer to "несколько несвязанных тел"
    (bench-SPEC §3.2): connectivity is intrinsic to the shape, unlike the OCCT
    solid/shell typing, which flips with build order and exports an open-top box
    as a shell (0 solids). Grading `solids` off BRep topology would fail a valid
    alternative build for a bookkeeping difference (§4); component count doesn't.
    """
    try:
        return int(len(mesh.split(only_watertight=False)))
    except Exception:  # noqa: BLE001
        return 1 if len(mesh.faces) else 0


def mesh_facts(stl_path: str | Path) -> dict:
    """Raw and normalized mesh facts + coordinate contract."""
    raw = load_mesh(stl_path)
    norm = normalize(raw)
    return {
        "raw": {
            "watertight": bool(raw.is_watertight),
            "volume_mm3": float(abs(raw.volume)) if raw.is_volume else None,
            "faces": int(len(raw.faces)),
        },
        "normalized": {
            "watertight": bool(norm.is_watertight),
            "volume_mm3": float(abs(norm.volume)) if norm.is_volume else None,
            "faces": int(len(norm.faces)),
        },
        "watertight": bool(norm.is_watertight),
        "bodies": body_count(norm),
        "coordinate_contract": coordinate_contract(norm),
    }
