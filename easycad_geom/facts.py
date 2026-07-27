"""The cheap superset of facts written to `facts.json` (bench-SPEC §2.8).

Grading criteria change weekly, generation costs money and is nondeterministic;
recomputing a verdict from a saved run must be free. So we measure a generous
superset now — anything cheap — because there is nowhere to fetch it from later.

`compute_facts` takes the CAD-native STEP (exact BRep facts) and the STL (mesh
facts). Surface deviation, which needs the *reference*, is not here — it belongs
to grading, which has both models.
"""

from __future__ import annotations

from pathlib import Path

from . import measure, mesh

# Fixed tessellation the harness assumes for exported STL (bench-SPEC §6.3).
# Recorded so a deflection change can never move watertight/volume/distance
# silently.
TESSELLATION = {"linear_deflection": 0.05, "angular_deflection": 0.3}


def compute_facts(step_path: str | Path, stl_path: str | Path) -> dict:
    """Full fact set for one turn's model. BRep facts from STEP, mesh facts from
    STL. Any failure to load either surfaces as an `error` rather than raising —
    an unmeasurable model must reach the grader as a fact, not an exception."""
    facts: dict = {"tessellation": TESSELLATION}
    try:
        shape = measure.load_shape(step_path)
        facts.update(measure.brep_facts(shape))
    except Exception as exc:  # noqa: BLE001 — a bad STEP is a measurable failure
        facts["brep_error"] = f"{type(exc).__name__}: {exc}"
    try:
        facts.update(mesh.mesh_facts(stl_path))
    except Exception as exc:  # noqa: BLE001
        facts["mesh_error"] = f"{type(exc).__name__}: {exc}"
    return facts
