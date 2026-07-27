"""Render an STL to PNGs from four viewpoints for visual validation (§4.3).

Uses matplotlib's offscreen Agg backend — no display, no GPU, always available
in CI. These renders exist so a human can accept or reject a reference; when in
doubt the validator opens the STL itself (bench-SPEC §4.3), so shaded accuracy
here matters less than getting four honest angles cheaply.
"""

from __future__ import annotations

from pathlib import Path

# Four azimuth/elevation pairs: front-ish, back-ish, top, and a low angle.
VIEWS = [
    ("iso", 45, 25),
    ("iso-back", -135, 25),
    ("top", -90, 89),
    ("front-low", 0, 8),
]


def render_stl(stl_path: Path, out_dir: Path) -> list[Path]:
    """Write `<view>.png` for each viewpoint; return the paths. Best-effort:
    on any failure returns []."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from mpl_toolkits.mplot3d.art3d import Poly3DCollection
        import trimesh
    except Exception:  # noqa: BLE001
        return []

    try:
        mesh = trimesh.load(str(stl_path), file_type="stl", process=True)
        if isinstance(mesh, trimesh.Scene):
            mesh = mesh.dump(concatenate=True)
    except Exception:  # noqa: BLE001
        return []

    out_dir.mkdir(parents=True, exist_ok=True)
    tris = mesh.triangles
    lo, hi = mesh.bounds
    span = float((hi - lo).max()) or 1.0
    ctr = (lo + hi) / 2

    written: list[Path] = []
    for name, azim, elev in VIEWS:
        fig = plt.figure(figsize=(4, 4))
        ax = fig.add_subplot(111, projection="3d")
        coll = Poly3DCollection(tris, alpha=1.0, edgecolor=(0, 0, 0, 0.15))
        coll.set_facecolor((0.6, 0.7, 0.85))
        ax.add_collection3d(coll)
        for axis, c in zip("xyz", ctr):
            getattr(ax, f"set_{axis}lim")(c - span / 2, c + span / 2)
        ax.set_box_aspect((1, 1, 1))
        ax.view_init(elev=elev, azim=azim)
        ax.set_axis_off()
        path = out_dir / f"{name}.png"
        fig.savefig(path, dpi=80, bbox_inches="tight", pad_inches=0)
        plt.close(fig)
        written.append(path)
    return written
