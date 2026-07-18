"""Append-only planner-run observability."""

from __future__ import annotations

import json
from pathlib import Path
from threading import Lock


_LOCK = Lock()


def append_planner_run(record: dict[str, object], path: Path = Path("logs/planner_runs.jsonl")) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n"
    with _LOCK:
        with path.open("a", encoding="utf-8") as handle:
            handle.write(line)
