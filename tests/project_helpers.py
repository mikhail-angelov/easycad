from __future__ import annotations

from app.ai_generation import project_from_plan


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
    return project_from_plan(plate_plan(), plate_analysis(), {"filename": "plate.png", "mime_type": "image/png"}, b"drawing")
