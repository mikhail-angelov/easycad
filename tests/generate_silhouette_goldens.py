from __future__ import annotations

import json
from io import BytesIO
from pathlib import Path

from PIL import Image

from app.main import load_project_json
from app.models import CADProject, CADSource, FeatureGraph
from app.runner import run_project
from app.silhouette import silhouette_mask
from app.feature_compiler import compile_feature_graph


ROOT = Path(__file__).resolve().parent.parent
OUTPUT = ROOT / "tests" / "fixtures" / "silhouettes"


def main() -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    _write_mask("bracket_fixture.front.png", bracket_project(), "front")
    _write_mask("asymmetric_rib.isometric.png", rib_project(), "isometric")


def bracket_project() -> CADProject:
    return load_project_json((ROOT / "projects" / "bracket_fixture.json").read_text(encoding="utf-8"))


def rib_project() -> CADProject:
    cases = json.loads((ROOT / "tests" / "fixtures" / "capabilities" / "cases.json").read_text(encoding="utf-8"))
    ribs = next(case for case in cases if case["id"] == "ribs")
    graph = FeatureGraph.model_validate({"operations": ribs["operations"]})
    return CADProject(
        parameters={key: {"label": key, "value": value, "type": "number"} for key, value in ribs["parameters"].items()},
        feature_graph=graph,
        cad=CADSource(source=compile_feature_graph(graph, ribs["parameters"]), source_kind="compiled"),
    )


def _write_mask(name: str, project: CADProject, view: str) -> None:
    result = run_project(project, {}, fmt="stl", render_views=True)
    image = Image.open(BytesIO(result["render_artifacts"][view]))
    silhouette_mask(image).save(OUTPUT / name, format="PNG")


if __name__ == "__main__":
    main()
