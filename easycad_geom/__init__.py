"""Shared geometry measurement for EasyCAD (app review-block + bench harness).

One implementation of the facts so the product and the benchmark never drift
(bench-SPEC §2.7). The bench imports this package; the app can too, for the
future "compare with previous step" review block.

Public surface:

* `measure`  — BRep facts from a STEP file (bbox, volume, solids, valid).
* `mesh`     — STL load + normalize, watertight, coordinate contract.
* `compare`  — two-sided surface-to-surface distance.
* `facts`    — `compute_facts(step, stl)` orchestrates the cheap superset
               written to `facts.json` (bench-SPEC §2.8).
"""

from .facts import compute_facts
from .compare import surface_deviation

__all__ = ["compute_facts", "surface_deviation"]
