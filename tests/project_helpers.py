from __future__ import annotations

from app.feature_compiler import compile_project_feature_graph
from app.models import (
    CADParameter,
    CADProject,
    CADSource,
    DrawingAnalysis,
    FeatureCoverageReport,
    FeatureGraph,
    FeatureSummary,
    GenerationResult,
    SourceInfo,
)
from app.source_images import store_source_image


def plate_plan() -> dict:
    return {
        "title": "Plate",
        "parameters": [
            {"id": "plate_length", "label": "Length", "type": "number", "value": 40},
            {"id": "plate_width", "label": "Width", "type": "number", "value": 30},
            {"id": "plate_thickness", "label": "Height", "type": "number", "value": 5},
        ],
        "feature_graph": {"operations": [{"id": "base", "type": "box", "operation": "add", "source_feature_ids": ["base"], "parameters": {"length": "plate_length", "width": "plate_width", "height": "plate_thickness"}, "status": "implemented", "implementation": "base"}]},
        "feature_summary": [{"id": "base", "name": "Base", "type": "body", "description": "Plate"}],
        "assumptions": [],
    }


def plate_analysis() -> dict:
    return {"title": "Plate", "features": [{"id": "base", "type": "body", "confidence": 1.0}], "dimensions": [], "views": [], "uncertainties": []}


def make_plate_project():
    image_ref, image_sha256 = store_source_image(b"drawing")
    project = CADProject(
        title="Plate",
        source=SourceInfo(filename="plate.png", mime_type="image/png", image_ref=image_ref, image_sha256=image_sha256),
        analysis=DrawingAnalysis(features=plate_analysis()["features"]),
        parameters={
            "plate_length": CADParameter(label="Length", value=40),
            "plate_width": CADParameter(label="Width", value=30),
            "plate_thickness": CADParameter(label="Height", value=5),
        },
        feature_graph=FeatureGraph.model_validate(plate_plan()["feature_graph"]),
        feature_coverage=FeatureCoverageReport.model_validate({"entries": [{"feature_id": "base", "operation_ids": ["base"], "status": "implemented", "confidence": 1.0}]}),
        feature_summary=[FeatureSummary(id="base", name="Base", type="body", description="Plate")],
        cad=CADSource(source="", source_kind="compiled"),
        generation=GenerationResult(status="needs_review"),
    )
    return compile_project_feature_graph(project)
