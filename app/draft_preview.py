"""Fast deterministic schematic projections for draft review."""

from __future__ import annotations

from html import escape

from .draft_lint import resolved_feature_extent
from .models import DraftSpecification
from .specification import resolve_dimension_values


def draft_schematic(draft: DraftSpecification) -> dict[str, str]:
    values, _ = resolve_dimension_values(draft)
    views = {"front": (0, 2), "top": (0, 1), "right": (1, 2)}
    extents = {item.id: resolved_feature_extent(item, values) for item in draft.features}
    result: dict[str, str] = {}
    for view, axes in views.items():
        elements: list[str] = []
        approximate: list[str] = []
        for feature in sorted(draft.features, key=lambda item: item.id):
            extent = extents[feature.id]
            css = "cut" if feature.operation == "cut" else "additive"
            if extent is not None:
                x = extent.minimum[axes[0]]
                y = extent.minimum[axes[1]]
                width = max(0.1, extent.maximum[axes[0]] - x)
                height = max(0.1, extent.maximum[axes[1]] - y)
                elements.append(f'<rect data-feature-id="{escape(feature.id)}" class="{css}" x="{x:g}" y="{-y-height:g}" width="{width:g}" height="{height:g}"/>')
                continue
            origin = feature.placement.origin or []
            resolved = [values.get(value) if isinstance(value, str) else value for value in origin]
            if len(resolved) == 3 and all(isinstance(value, (int, float)) for value in resolved):
                x = float(resolved[axes[0]])
                y = float(resolved[axes[1]])
                elements.append(f'<rect data-feature-id="{escape(feature.id)}" class="approximate" x="{x:g}" y="{-y-10:g}" width="10" height="10"/>')
            else:
                approximate.append(feature.id)
        legend = "".join(f'<text class="approximate-label">approximate: {escape(item)}</text>' for item in approximate)
        result[view] = (
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="-10 -110 120 120">'
            '<style>.additive{fill:#cad9cf;stroke:#254438}.cut{fill:none;stroke:#a44432;stroke-dasharray:3 2}.approximate{fill:url(#h);stroke:#777}.approximate-label{font-size:4px}[data-selected="true"]{stroke:#1769aa;stroke-width:2}</style>'
            '<defs><pattern id="h" width="4" height="4" patternUnits="userSpaceOnUse"><path d="M0 4L4 0" stroke="#999"/></pattern></defs>'
            + "".join(elements) + legend + "</svg>"
        )
    return result
