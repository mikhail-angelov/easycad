"""Filesystem layout of the harness (bench-SPEC §6, §7)."""

from __future__ import annotations

from pathlib import Path

BENCH_ROOT = Path(__file__).resolve().parents[2]   # .../bench
REPO_ROOT = BENCH_ROOT.parent                        # .../easycad
SCENARIOS = BENCH_ROOT / "scenarios"
RUNS = BENCH_ROOT / "runs"
BASELINES = BENCH_ROOT / "baselines"


def scenario_dir(scenario_id: str) -> Path:
    return SCENARIOS / scenario_id


def expected_dir(scenario_id: str) -> Path:
    return scenario_dir(scenario_id) / "expected"
