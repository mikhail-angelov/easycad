"""Surface-to-surface distance between two meshes (bench-SPEC §2.2, §3.3).

Shape is compared by distance between surfaces, not by volume: distance is
linear in millimetres — the same language the task's tolerances are written in —
whereas volumetric IoU is weighted by volume, which functional significance is
not correlated with (a missing Ø5.5 hole is <4% of a plate).
"""

from __future__ import annotations

import numpy as np
import trimesh


def surface_deviation(
    gen_mesh: trimesh.Trimesh,
    ref_mesh: trimesh.Trimesh,
    tol_mm: float = 0.5,
    samples: int = 20000,
    seed: int = 0,
) -> dict:
    """Two-sided surface-to-surface distance. Both meshes must be watertight.

    Sampled on the surface, so every point is weighted by area — there is no
    hidden effective-n. One direction catches what the generated model is
    missing, the other what it has in excess.
    """

    def one_way(src: trimesh.Trimesh, dst: trimesh.Trimesh) -> np.ndarray:
        pts, _ = trimesh.sample.sample_surface(src, samples, seed=seed)
        _, dist, _ = dst.nearest.on_surface(pts)
        return dist

    d = np.concatenate([
        one_way(ref_mesh, gen_mesh),   # what is missing from the generated model
        one_way(gen_mesh, ref_mesh),   # what is extra in it
    ])
    return {
        "tol_mm": tol_mm,
        "frac_over_tol": float((d > tol_mm).mean()),
        "p99_mm": float(np.percentile(d, 99)),
        "max_mm": float(d.max()),
        "mean_mm": float(d.mean()),
        "samples": 2 * samples,
    }


def deviation_after_align(
    gen_mesh: trimesh.Trimesh,
    ref_mesh: trimesh.Trimesh,
    tol_mm: float = 0.5,
    samples: int = 20000,
    seed: int = 0,
) -> dict:
    """Surface deviation after snapping the generated model's XY-center and
    z_min onto the reference's (bench-SPEC §3.1).

    Diagnostic only — never affects the verdict. It answers "the shape is right
    but placed wrong" vs "the shape is actually wrong" when the coordinate
    contract fails, so a contract violation isn't mistaken for a shape defect.
    """
    g_lo, g_hi = gen_mesh.bounds
    r_lo, r_hi = ref_mesh.bounds
    shift = np.array([
        (r_lo[0] + r_hi[0]) / 2 - (g_lo[0] + g_hi[0]) / 2,
        (r_lo[1] + r_hi[1]) / 2 - (g_lo[1] + g_hi[1]) / 2,
        r_lo[2] - g_lo[2],
    ])
    aligned = gen_mesh.copy()
    aligned.apply_translation(shift)
    out = surface_deviation(aligned, ref_mesh, tol_mm=tol_mm, samples=samples, seed=seed)
    out["applied_shift_mm"] = [float(x) for x in shift]
    return out
