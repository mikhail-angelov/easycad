"""`bench validate` — mandatory human acceptance of a reference (§4.3).

A reference is trusted only when a human has confirmed it *by renders*: numbers
read off a bad model are self-consistent and lie (§2.1). Acceptance is recorded
in `expected/validation.json` with the reference hash of each turn; any later
edit to `reference.py` changes the hash and silently invalidates it, so a
reference cannot be altered after acceptance without re-validation.
"""

from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path

from .schema import Scenario, load_scenario, sha256_file


def _validation_path(sc: Scenario) -> Path:
    return sc.dir / "expected" / "validation.json"


def validated_turns(sc: Scenario) -> set[int]:
    """Turn numbers whose recorded acceptance still matches the current
    reference hash. A hash mismatch means the reference was edited after
    acceptance → no longer trusted (§4.3)."""
    vp = _validation_path(sc)
    if not vp.exists():
        return set()
    try:
        doc = json.loads(vp.read_text())
    except Exception:  # noqa: BLE001
        return set()
    good: set[int] = set()
    for entry in doc.get("per_turn", []):
        i = entry.get("turn")
        ref = sc.turns[i - 1].reference if 0 < i <= len(sc.turns) else None
        if (entry.get("accepted") and ref
                and entry.get("reference_sha256") == sha256_file(sc.dir / ref)):
            good.add(i)
    return good


def is_validated(sc: Scenario) -> bool:
    """True iff every turn of a complete scenario is currently trusted."""
    if not sc.is_complete:
        return False
    return validated_turns(sc) == set(range(1, len(sc.turns) + 1))


def cmd_validate(args) -> int:
    sc = load_scenario(args.scenario)
    if not sc.is_complete:
        print(f"{sc.id}: open scenario — validated by rubric at review time, not here")
        return 0
    exp = sc.dir / "expected"
    if not (exp / "turn-1.json").exists():
        print(f"{sc.id}: no expected/ yet — run `bench spec {sc.id}` first")
        return 1

    print(f"\n{sc.id} — {sc.title}\n")
    for i, turn in enumerate(sc.turns, 1):
        renders = sorted((exp / f"turn-{i}" / "renders").glob("*.png"))
        print(f"  turn {i}: {turn.prompt[:70]}")
        print(f"    stl:     {exp / f'turn-{i}.stl'}")
        for r in renders:
            print(f"    render:  {r}")
    print()

    if not args.accept:
        print("Review the renders (open the STL if unsure), then re-run with --accept "
              "to record acceptance. Nothing was written.")
        return 0

    doc = {
        "validator": args.validator or os.getenv("USER", "unknown"),
        "date": dt.date.today().isoformat(),
        "per_turn": [
            {"turn": i, "reference_sha256": sha256_file(sc.dir / t.reference), "accepted": True}
            for i, t in enumerate(sc.turns, 1)
        ],
    }
    _validation_path(sc).write_text(json.dumps(doc, indent=2))
    print(f"✓ {sc.id}: accepted by {doc['validator']} on {doc['date']} "
          f"({len(sc.turns)} turn(s)) → expected/validation.json")
    return 0
