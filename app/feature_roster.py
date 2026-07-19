"""Read-only presentation view over a draft: what's confirmed, what was omitted and why."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .draft_lint import resolved_feature_extent
from .models import DraftSpecification


@dataclass(frozen=True)
class FeatureRosterEntry:
    id: str
    label: str
    status: Literal["confirmed", "unsupported"]
    omission_reason: str | None
    extent: dict[str, list[float]] | None


def feature_roster(draft: DraftSpecification, values: dict[str, float]) -> list[FeatureRosterEntry]:
    """Describe every specification feature, confirmed or omitted, in draft order."""
    entries = []
    for feature in draft.features:
        extent = resolved_feature_extent(feature, values)
        entries.append(FeatureRosterEntry(
            id=feature.id,
            label=feature.label,
            status=feature.status,
            omission_reason=feature.omission_reason,
            extent={"minimum": list(extent.minimum), "maximum": list(extent.maximum)} if extent else None,
        ))
    return entries
