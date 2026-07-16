"""Compatibility adapters used only to replay historical provider payloads in tests."""

from __future__ import annotations

from typing import Any


def normalize_draft_specification_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Translate recorded provider field variants into the DraftSpecification contract."""
    wrapped_parameters = payload.get("parameters")
    wrapped_specification = payload.get("draft_specification")
    if isinstance(wrapped_parameters, dict) and "features" in wrapped_parameters:
        normalized = dict(wrapped_parameters)
    elif isinstance(wrapped_specification, dict) and "features" in wrapped_specification:
        normalized = dict(wrapped_specification)
    else:
        normalized = dict(payload)
    if str(normalized.get("units", "")).lower() in {"millimeter", "millimeters", "millimetre", "millimetres"}:
        normalized["units"] = "mm"

    for dimension in normalized.get("dimensions", []):
        if not isinstance(dimension, dict):
            continue
        if isinstance(dimension.get("evidence"), str):
            dimension["evidence"] = [dimension["evidence"]]
        if dimension.get("source") not in {"drawing", "derived", "inferred", "assumed", "manual", None}:
            dimension["source"] = "drawing"

    for feature in normalized.get("features", []):
        if isinstance(feature, dict):
            if isinstance(feature.get("evidence"), str):
                feature["evidence"] = [feature["evidence"]]
            if feature.get("placement") is None:
                feature["placement"] = {}

    assumptions = []
    for assumption in normalized.get("assumptions", []):
        if not isinstance(assumption, dict):
            continue
        item = dict(assumption)
        description = str(item.get("description", ""))
        item.setdefault("value", description)
        item.setdefault("rationale", description)
        item.setdefault("affected_ids", item.get("affects", []))
        assumptions.append(item)
    normalized["assumptions"] = assumptions

    questions = []
    for question in normalized.get("questions", []):
        if not isinstance(question, dict):
            continue
        item = dict(question)
        related = item.get("related_features") or item.get("related_dimensions") or item.get("required_for") or []
        if not related and item.get("related_feature"):
            related = [item["related_feature"]]
        if isinstance(related, str):
            related = [related]
        item.setdefault("field_id", related[0] if related else item.get("id", "question"))
        item.setdefault("prompt", item.get("question", item.get("description", "Please provide the missing detail.")))
        questions.append(item)
    normalized["questions"] = questions

    annotations = []
    for annotation in normalized.get("annotations", []):
        if not isinstance(annotation, dict):
            continue
        item = dict(annotation)
        links = item.get("links_to", item.get("field_ids", item.get("field_id", [])))
        if isinstance(links, str):
            links = [links]
        if not isinstance(links, list):
            links = []
        field_ids = [link for link in links if isinstance(link, str) and link]
        item["field_ids"] = field_ids
        item["field_id"] = field_ids[0] if field_ids else str(item.get("field_id") or item.get("id", "annotation"))
        item.setdefault("label", item.get("text", item["field_id"]))
        annotations.append(item)
    normalized["annotations"] = annotations
    return normalized
