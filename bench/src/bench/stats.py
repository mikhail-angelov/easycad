"""Reporting statistics (bench-SPEC §5.3).

One attempt per scenario in the headline metric, so the Wilson interval applies
directly with n = number of scenarios.
"""

from __future__ import annotations

import math


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson score interval for k successes out of n."""
    if n == 0:
        return (0.0, 1.0)
    p = k / n
    denom = 1 + z * z / n
    centre = p + z * z / (2 * n)
    spread = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return ((centre - spread) / denom, (centre + spread) / denom)


def format_rate(k: int, n: int, extra: str = "") -> str:
    """`65% (CI 43–82, n=20)` — whole percents only (bench-SPEC §5.3)."""
    if n == 0:
        return f"—  (n=0{', ' + extra if extra else ''})"
    lo, hi = wilson(k, n)
    tail = f", {extra}" if extra else ""
    return f"{round(100 * k / n)}%  (CI {round(100 * lo)}–{round(100 * hi)}, n={n}{tail})"
