"""Deterministic cross-view/cross-method dimension verification for multi-panel sketches.

Engages only when the uploaded image itself is a deliberately multi-panel drawing (front/top/
side/bottom views arranged on one page). An ordinary single-view photo (the common case) falls
through untouched: `detect_panel_layout` returns None and the caller uses the normal
single-image path, at only the cost of one cheap classification call (see below for why that
call, rather than a free pixel heuristic, is what decides this).

Promoted from a standalone experiment (tests/test_e2e_multiview_triangulation.py) after a real
run confirmed two dimensions independently agreed between an LLM read and an unrelated OCR read
on the same pixels. Two things were tried and ruled out on the way here (both documented in
docs/AI_LEARNED.md):
  - Classical arrowhead detection (OpenCV HoughLinesP + wedge-angle junctions) to separate
    dimension-line pixels from object-line pixels: too noisy on a hand sketch, fires on
    ordinary corners and hatching crossings as readily as on real arrowheads.
  - A pixel-brightness gap heuristic for `detect_panel_layout` itself (row/column mean
    brightness above a fixed threshold): failed its own unit test the day it was written --
    false-positived on an ordinary single-view PNG that happens to be uniformly bright
    end-to-end (no real panel gap, just a lot of blank margin), and false-negatived on the
    real multi-panel *photo* (camera lighting falloff keeps even the genuine gaps well below
    a scan-calibrated threshold). Absolute brightness isn't comparable across a phone photo
    and a clean scan; a same-day relative-contrast variant didn't cleanly separate the two
    test images either. "Is this one drawing or several panels" is exactly the kind of
    holistic visual judgment a vision model is good at and pixel statistics aren't -- so that
    one decision is delegated to a single, cheap, non-agentic vision call instead.

Best-effort throughout: any failure here (not multi-panel, missing tesseract binary, a
malformed vision response, ...) degrades to an empty grounding string rather than failing the
upload. This module never raises to its caller.
"""

from __future__ import annotations

import base64
import logging
import os
import re
from dataclasses import dataclass
from io import BytesIO
from typing import Any

from PIL import Image

from .ai_generation import OPENROUTER_URL, _chat_json, normalize_model_id

logger = logging.getLogger("easycad.multiview")

_NUMERIC = re.compile(r"\d+\.?\d*")


@dataclass(frozen=True)
class PanelLayout:
    panels: dict[str, Image.Image]


async def detect_panel_layout(image: Image.Image, api_key: str) -> PanelLayout | None:
    """One cheap vision call: does this page show multiple separate view panels, and roughly
    where? None if it's a single drawing, if the model isn't confident, or on any failure --
    the caller always has the normal single-image path to fall back to."""
    prompt = (
        "Does this image contain ONE drawing of an object, or MULTIPLE separate view panels of "
        "the same object arranged on one page (for example front/top/side/bottom views, each "
        "occupying its own region of the page, typically with its own label like 'top' or "
        "'front')? Most sketches are a single view; only answer true when you can actually see "
        "2 or more clearly distinct panels. "
        'Return strictly one JSON object: {"multi_panel": <true|false>, "panels": '
        '[{"label": "<short name for this panel, e.g. front|top|bottom|side>", '
        '"left": <0-1>, "top": <0-1>, "right": <0-1>, "bottom": <0-1>}]}. '
        "left/top/right/bottom are fractions of the full image width/height bounding each "
        "panel with a small margin. Omit \"panels\" (or leave it empty) when multi_panel is "
        "false."
    )
    model = normalize_model_id(os.environ.get("OPEN_ROUTER_MODEL", "google/gemini-3-flash-preview"))
    buf = BytesIO()
    image.convert("RGB").save(buf, format="PNG")
    encoded = base64.b64encode(buf.getvalue()).decode("ascii")
    payload = {
        "model": model,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{encoded}"}},
            ],
        }],
        "temperature": 0.1,
        "max_tokens": 400,
        "response_format": {"type": "json_object"},
    }
    result = await _chat_json(OPENROUTER_URL, api_key, payload, "multiview_layout_detection")
    if not result.get("multi_panel") or not result.get("panels"):
        return None

    w, h = image.size
    panels: dict[str, Image.Image] = {}
    for index, entry in enumerate(result["panels"]):
        try:
            left, top, right, bottom = (float(entry[key]) for key in ("left", "top", "right", "bottom"))
        except (KeyError, TypeError, ValueError):
            continue
        box = (max(0, int(left * w)), max(0, int(top * h)), min(w, int(right * w)), min(h, int(bottom * h)))
        if box[2] - box[0] < 20 or box[3] - box[1] < 20:
            continue
        label = str(entry.get("label") or f"panel_{index}")
        panels[label if label not in panels else f"{label}_{index}"] = image.crop(box)
    return PanelLayout(panels=panels) if len(panels) >= 2 else None


def ocr_panel_dimensions(image: Image.Image) -> list[dict[str, Any]]:
    """Best-effort digit OCR; returns [] (never raises) if pytesseract or the tesseract binary
    isn't available in this environment -- an independent reading *method* on the same pixels
    as the LLM panel read, not a required one."""
    try:
        import pytesseract
    except Exception:
        return []
    try:
        data = pytesseract.image_to_data(
            image, config="--psm 11 -c tessedit_char_whitelist=0123456789.", output_type=pytesseract.Output.DICT,
        )
    except Exception:
        logger.warning("OCR unavailable (tesseract binary missing?); continuing without it", exc_info=True)
        return []
    readings = []
    for i, text in enumerate(data["text"]):
        text = text.strip()
        if len(text) >= 2 and int(data["conf"][i]) > 40 and _NUMERIC.fullmatch(text):
            readings.append({"value": float(text), "measures": "(ocr digit, no semantic label)", "confidence": int(data["conf"][i]) / 100})
    return readings


async def _read_panel_dimensions(image: Image.Image, panel_name: str, api_key: str) -> dict[str, Any]:
    prompt = (
        f"This is one cropped view (internal label: '{panel_name}') from a hand-drawn multi-view "
        "mechanical sketch; the other views are NOT in this image. "
        "First distinguish the two kinds of lines: object/silhouette lines are the actual physical "
        "boundary of the part; dimension, extension, and leader lines are thin lines that terminate "
        "in an arrowhead and exist only to point at a number -- they are annotation, never a physical "
        "feature (do not report a bump, step, or protrusion whose only evidence is a dimension/leader "
        "line and its arrowhead). "
        "Read every numeric dimension callout visible in only this crop. Return strictly one JSON object: "
        '{"view": "<front|top|bottom|side|section, your best guess for THIS crop>", '
        '"dimensions": [{"value": <number, millimeters>, "measures": "<short plain description, '
        'e.g. overall width, wall thickness, hole diameter>", "confidence": <0-1>}]}. '
        "Do not report a dimension you cannot actually see in this specific crop."
    )
    buf = BytesIO()
    image.save(buf, format="PNG")
    encoded = base64.b64encode(buf.getvalue()).decode("ascii")
    model = normalize_model_id(os.environ.get("OPEN_ROUTER_MODEL", "google/gemini-3-flash-preview"))
    payload = {
        "model": model,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{encoded}"}},
            ],
        }],
        "temperature": 0.1,
        "max_tokens": 1000,
        "response_format": {"type": "json_object"},
    }
    return await _chat_json(OPENROUTER_URL, api_key, payload, "multiview_panel_dimensions")


def reconcile(panel_results: dict[str, dict], ocr_results: dict[str, list[dict]], tolerance_mm: float = 0.5) -> dict:
    """A dimension is cross-verified if 2+ readings from *different (panel, method) pairs*
    agree on the number -- grouping key is the numeric value alone (not text similarity),
    deliberately crude and deterministic rather than trusting an LLM's judgment that two
    descriptions "mean the same thing"."""
    readings = [
        {"source": f"{panel}/llm", "value": float(dim["value"]), "measures": dim.get("measures", ""), "confidence": dim.get("confidence", 0.5)}
        for panel, result in panel_results.items()
        for dim in result.get("dimensions", [])
    ]
    readings += [
        {"source": f"{panel}/ocr", "value": dim["value"], "measures": dim["measures"], "confidence": dim["confidence"]}
        for panel, dims in ocr_results.items()
        for dim in dims
    ]

    groups: list[list[dict]] = []
    used: set[int] = set()
    for i, a in enumerate(readings):
        if i in used:
            continue
        group = [a]
        used.add(i)
        for j, b in enumerate(readings):
            if j in used or j == i:
                continue
            if abs(a["value"] - b["value"]) <= tolerance_mm:
                group.append(b)
                used.add(j)
        groups.append(group)

    verified, single_source = [], []
    for group in groups:
        sources = sorted({g["source"] for g in group})
        llm_descriptions = [g["measures"] for g in group if g["source"].endswith("/llm") and g["measures"]]
        best_description = max(llm_descriptions, key=len) if llm_descriptions else group[0]["measures"]
        entry = {"value": group[0]["value"], "measures": best_description, "confirmed_by": sources}
        (verified if len(sources) >= 2 else single_source).append(entry)
    return {"verified": verified, "single_source_only": single_source}


def format_grounding_instructions(reconciliation: dict) -> str:
    if not reconciliation["verified"]:
        return ""
    lines = ["Dimensions independently cross-verified by 2+ separate (view, reading-method) pairs (trust these exact values):"]
    for item in reconciliation["verified"]:
        lines.append(f"- {item['value']}mm: {item['measures']} (confirmed by: {', '.join(item['confirmed_by'])})")
    return "\n".join(lines)


async def build_grounding_instructions(image_bytes: bytes, api_key: str) -> str:
    """Best-effort end to end: '' if the image isn't a confident multi-panel layout, or if
    anything in the pipeline fails -- callers always get a normal upload, just without the
    extra grounding text. Never raises."""
    if not api_key:
        return ""
    try:
        image = Image.open(BytesIO(image_bytes))
        layout = await detect_panel_layout(image, api_key)
        if layout is None:
            return ""
        panel_results: dict[str, dict] = {}
        ocr_results: dict[str, list] = {}
        for name, crop in layout.panels.items():
            panel_results[name] = await _read_panel_dimensions(crop, name, api_key)
            ocr_results[name] = ocr_panel_dimensions(crop)
        reconciliation = reconcile(panel_results, ocr_results)
        return format_grounding_instructions(reconciliation)
    except Exception:
        logger.warning("multiview triangulation failed; continuing with the normal single-image path", exc_info=True)
        return ""
