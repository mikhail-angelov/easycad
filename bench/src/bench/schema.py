"""Scenario loading and schema validation (bench-SPEC §4).

A scenario is a directory `scenarios/<id>/` with a `scenario.yaml`, one
`reference-N.py` per turn (for `spec: complete`), and a generated `expected/`.
Validation here is what CI's schema check (bench-SPEC §12) runs.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from . import paths

DEFAULT_TOLERANCES = {
    "bbox_mm": 0.5,
    "volume_rel": 0.03,
    "tol_mm": 0.5,
    "max_mm": 2.0,
    "frac_over_tol": 0.005,
}

# The measures an open-scenario `check` may assert against. Schema owns this
# vocabulary so a typo fails the CI schema gate, not silently at judge time
# (a mis-typed measure would otherwise read as a model failure). Kept in sync
# with `judge._measure` by `bench/tests/test_judge.py`.
CHECK_MEASURES = {
    "bodies", "volume_mm3", "largest_dim_mm", "smallest_dim_mm",
    "x_mm", "y_mm", "z_mm", "z_min_mm", "watertight", "valid",
}
BOOL_MEASURES = {"watertight", "valid"}   # true/false measures — only `eq` a bool
_NUMERIC = (int, float)


class SchemaError(ValueError):
    """A scenario.yaml that violates the schema (bench-SPEC §4)."""


@dataclass
class Turn:
    prompt: str
    reference: str | None            # reference-N.py filename (complete only)
    preserve: list[str] = field(default_factory=list)  # diagnostic only (§2.4)


@dataclass
class Scenario:
    id: str
    title: str
    spec: str                        # "complete" | "open"
    tags: list[str]
    timeout_s: int
    turns: list[Turn]
    tolerances: dict
    rubric: list[str]                # open: VISUAL items graded by the vision judge
    checks: list[dict]               # open: geometric assertions auto-checked from facts
    dir: Path

    @property
    def is_complete(self) -> bool:
        return self.spec == "complete"


def _require(cond: bool, msg: str) -> None:
    if not cond:
        raise SchemaError(msg)


def load_scenario(scenario_id: str) -> Scenario:
    d = paths.scenario_dir(scenario_id)
    yml = d / "scenario.yaml"
    _require(yml.exists(), f"{scenario_id}: missing scenario.yaml")
    raw = yaml.safe_load(yml.read_text())
    return _parse(scenario_id, raw, d)


def _parse(scenario_id: str, raw: dict, d: Path) -> Scenario:
    _require(isinstance(raw, dict), f"{scenario_id}: scenario.yaml is not a mapping")
    _require(raw.get("id") == scenario_id,
             f"{scenario_id}: id field '{raw.get('id')}' != directory name")
    spec = raw.get("spec")
    _require(spec in ("complete", "open"), f"{scenario_id}: spec must be complete|open")

    raw_turns = raw.get("turns") or []
    _require(bool(raw_turns), f"{scenario_id}: at least one turn required")
    turns: list[Turn] = []
    for i, t in enumerate(raw_turns, 1):
        _require("prompt" in t and t["prompt"].strip(), f"{scenario_id} turn {i}: empty prompt")
        ref = t.get("reference")
        if spec == "complete":
            _require(bool(ref), f"{scenario_id} turn {i}: complete scenario needs a reference")
            _require((d / ref).exists(), f"{scenario_id} turn {i}: reference {ref} not found")
        turns.append(Turn(prompt=t["prompt"].strip(), reference=ref,
                          preserve=t.get("preserve") or []))

    rubric = raw.get("rubric") or []
    _require(isinstance(rubric, list) and all(isinstance(r, str) and r.strip() for r in rubric),
             f"{scenario_id}: rubric must be a list of non-empty strings "
             "(a bare string would be graded character-by-character)")
    checks = raw.get("checks") or []
    # A measure's TYPE determines its legal operators & value shape (bool subclass
    # of int means untyped validation would pass nonsense like `watertight ge 1`).
    _NUM_OPS = {"eq", "ge", "le", "gt", "lt", "between", "approx"}

    def _isnum(v) -> bool:  # numeric, but NOT a bool (which is an int subclass)
        return isinstance(v, _NUMERIC) and not isinstance(v, bool)

    for j, c in enumerate(checks, 1):
        _require(isinstance(c, dict), f"{scenario_id}: check {j} must be a mapping")
        measure = c.get("measure")
        _require(measure in CHECK_MEASURES,
                 f"{scenario_id}: check {j} measure '{measure}' not in {sorted(CHECK_MEASURES)}")
        _require("value" in c, f"{scenario_id}: check {j} needs a 'value'")
        op, val = c.get("op"), c["value"]
        if measure in BOOL_MEASURES:
            _require(op == "eq" and isinstance(val, bool),
                     f"{scenario_id}: check {j} boolean measure '{measure}' takes only "
                     "op 'eq' with a true/false value")
            continue
        _require(op in _NUM_OPS, f"{scenario_id}: check {j} op '{op}' not in {sorted(_NUM_OPS)}")
        if op == "between":
            _require(isinstance(val, list) and len(val) == 2 and all(_isnum(v) for v in val)
                     and val[0] <= val[1],
                     f"{scenario_id}: check {j} 'between' value must be [lo, hi] numeric with lo<=hi")
        else:
            _require(_isnum(val), f"{scenario_id}: check {j} '{op}' value must be a number (not bool)")
            if op == "approx":
                tol = c.get("tol", 0.5)
                _require(_isnum(tol) and tol >= 0,
                         f"{scenario_id}: check {j} 'tol' must be a non-negative number")
    if spec == "open":
        # A hybrid rubric: visual items (judge) + geometric checks (auto from facts).
        # Absolute size/topology aren't legible in an unscaled render, so they live
        # in `checks`, not `rubric`. Together they form the 4–6 item checklist (§4.1).
        _require(bool(rubric), f"{scenario_id}: open scenario needs at least one visual rubric item")
        total = len(rubric) + len(checks)
        _require(4 <= total <= 6, f"{scenario_id}: rubric + checks must total 4–6 items (§4.1)")
        _require(len(turns) == 1, f"{scenario_id}: open scenarios are single-turn (§4.1)")

    tol = dict(DEFAULT_TOLERANCES)
    tol.update(raw.get("tolerances") or {})

    return Scenario(
        id=scenario_id, title=raw.get("title", scenario_id), spec=spec,
        tags=raw.get("tags") or [], timeout_s=int(raw.get("timeout_s", 120)),
        turns=turns, tolerances=tol, rubric=rubric, checks=checks, dir=d,
    )


def all_scenario_ids() -> list[str]:
    if not paths.SCENARIOS.exists():
        return []
    return sorted(p.name for p in paths.SCENARIOS.iterdir()
                  if p.is_dir() and (p / "scenario.yaml").exists())


def sha256_file(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def validate_all() -> list[str]:
    """Load every scenario, returning the list of ids (raises on the first bad
    one). Used by the CI schema check."""
    ids = all_scenario_ids()
    for sid in ids:
        load_scenario(sid)
    return ids
