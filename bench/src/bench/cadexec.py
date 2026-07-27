"""Execute a CadQuery script and export STEP + STL at fixed tessellation.

Used to build reference models (`bench spec`) and to re-run saved generated code
(`offline` mode). Tessellation is pinned (bench-SPEC §6.3): deflection drives
watertight, mesh volume and surface distance, so it must never drift.

Reference code is trusted (authored in-repo); it runs in-process. Generated code
in a *live* run goes through the product's isolated worker, never here.
"""

from __future__ import annotations

from pathlib import Path

from easycad_geom.facts import TESSELLATION


class CadError(Exception):
    """A reference/offline script that failed to execute or export."""


def run_and_export(code: str, step_path: Path, stl_path: Path) -> object:
    """Exec `code`, export its `result` to STEP and STL, return the result.

    Raises CadError with a typed message on any failure (no `result`, exec
    error, export error) so callers can classify it.
    """
    import cadquery as cq

    namespace: dict = {}
    try:
        exec(code, namespace)  # noqa: S102 — trusted, in-repo reference code
    except Exception as exc:  # noqa: BLE001
        raise CadError(f"exec: {type(exc).__name__}: {exc}") from exc

    result = namespace.get("result")
    if result is None:
        raise CadError("no 'result' variable defined")

    try:
        cq.exporters.export(result, str(step_path))
        cq.exporters.export(
            result, str(stl_path),
            tolerance=TESSELLATION["linear_deflection"],
            angularTolerance=TESSELLATION["angular_deflection"],
        )
    except Exception as exc:  # noqa: BLE001
        raise CadError(f"export: {type(exc).__name__}: {exc}") from exc
    return result
