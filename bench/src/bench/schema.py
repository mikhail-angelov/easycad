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
    rubric: list[str]
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
    if spec == "open":
        _require(bool(rubric), f"{scenario_id}: open scenario needs a rubric")
        _require(4 <= len(rubric) <= 6, f"{scenario_id}: rubric must have 4–6 items (§4.1)")
        _require(len(turns) == 1, f"{scenario_id}: open scenarios are single-turn (§4.1)")

    tol = dict(DEFAULT_TOLERANCES)
    tol.update(raw.get("tolerances") or {})

    return Scenario(
        id=scenario_id, title=raw.get("title", scenario_id), spec=spec,
        tags=raw.get("tags") or [], timeout_s=int(raw.get("timeout_s", 120)),
        turns=turns, tolerances=tol, rubric=rubric, dir=d,
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
