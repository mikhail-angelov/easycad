"""Server-owned ordering for the specification review UI."""

from __future__ import annotations

from .draft_lint import LintResult
from .models import DraftSpecification


def build_review_plan(draft: DraftSpecification, lint: LintResult) -> list[dict[str, object]]:
    plan: list[dict[str, object]] = []
    seen: set[tuple[str, str]] = set()

    def add(tier: int, item_type: str, item_id: str, reason: str) -> None:
        identity = (item_type, item_id)
        if identity in seen:
            return
        seen.add(identity)
        plan.append({"tier": tier, "item_type": item_type, "item_id": item_id, "reason": reason})

    for item in draft.questions:
        add(1 if item.required else 5, "question", item.id, item.prompt)
    for item in draft.dimensions:
        if item.critical and item.status in {"needs_input", "conflicted"}:
            add(2, "dimension", item.id, f"{item.label} requires input")
        elif item.status == "assumed":
            add(3, "dimension", item.id, f"Accept proposed value for {item.label}")
        elif item.status != "confirmed":
            add(5, "dimension", item.id, f"Review {item.label}")
    for item in draft.features:
        if item.status == "assumed":
            add(3, "feature", item.id, f"Accept proposed feature {item.label}")
        elif item.status == "needs_input" or item.status == "conflicted":
            add(2, "feature", item.id, f"{item.label} requires input")
    for item in draft.assumptions:
        if item.status == "assumed":
            add(3, "assumption", item.id, item.rationale)
    for item in draft.features:
        if item.status == "unsupported":
            add(4, "feature", item.id, f"{item.label} is omitted")
    for item in draft.exclusions:
        add(4, "exclusion", item.feature_id, item.reason)
    for item in lint.issues:
        add(2 if item.severity == "error" else 4, "lint_issue", item.issue_id, item.message)
    return sorted(plan, key=lambda item: int(item["tier"]))
