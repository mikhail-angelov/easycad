"""Make `bench` and `easycad_geom` importable when pytest runs from the repo
root, and give the bench suite small on-the-fly geometry fixtures."""

import sys
import tempfile
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
for p in (str(_REPO), str(_REPO / "bench" / "src")):
    if p not in sys.path:
        sys.path.insert(0, p)

import pytest

from bench.cadexec import run_and_export
from easycad_geom.facts import compute_facts
from easycad_geom import mesh as gmesh


@pytest.fixture
def build(tmp_path):
    """Exec CadQuery `code`, export STEP+STL, return (facts, stl_path)."""
    counter = {"n": 0}

    def _build(code: str):
        counter["n"] += 1
        step = tmp_path / f"m{counter['n']}.step"
        stl = tmp_path / f"m{counter['n']}.stl"
        run_and_export(code, step, stl)
        return compute_facts(step, stl), stl

    return _build


@pytest.fixture
def load_mesh():
    def _load(stl):
        return gmesh.normalize(gmesh.load_mesh(stl))
    return _load
