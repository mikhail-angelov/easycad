from __future__ import annotations

from pathlib import Path

from app.main import load_project_json
from app.runner import run_project
from app.validator import validate_project


def main() -> None:
    for path in sorted(Path("projects").glob("*.json")):
        project = load_project_json(path.read_text(encoding="utf-8"))
        validate_project(project)
        for fmt in ("stl", "step"):
            result = run_project(project, {}, fmt=fmt)
            artifact_size = len(result["artifact_bytes"])
            if artifact_size <= 0:
                raise AssertionError(f"{path.name} {fmt} export was empty")
            print(path.name, fmt, artifact_size, result.get("bounding_box"))


if __name__ == "__main__":
    main()
