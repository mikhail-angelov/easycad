from __future__ import annotations

from typing import Any


TERMINAL_OUTCOMES = (
    "contract_ok",
    "needs_review",
    "verified_export",
    "invalid_plan",
    "worker_failed",
    "semantic_failed",
)


def classify_provider_outcome(result: dict[str, Any], *, stl_exported: bool = False, step_exported: bool = False) -> str:
    project = result.get("project") if isinstance(result.get("project"), dict) else {}
    generation = project.get("generation") if isinstance(project.get("generation"), dict) else {}
    error = generation.get("error") if isinstance(generation.get("error"), dict) else {}
    stage = str(error.get("stage") or "")
    if stage in {"cad_generation", "static_validation", "legacy_project"}:
        return "invalid_plan"
    if stage == "semantic_validation" or generation.get("semantic_status") == "failed":
        return "semantic_failed"
    if stage in {"worker", "worker_import", "worker_timeout", "cadquery_execution", "export"}:
        return "worker_failed"
    if result.get("status") == "success" and stl_exported and step_exported and generation.get("semantic_status") == "success":
        return "verified_export"
    if result.get("status") == "needs_review":
        return "needs_review"
    return "contract_ok"
