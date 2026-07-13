from __future__ import annotations

import json
from pathlib import Path

from PIL import Image, ImageDraw


ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "tests" / "fixtures" / "capabilities" / "images"


def build() -> None:
    cases = json.loads((ROOT / "tests" / "fixtures" / "capabilities" / "cases.json").read_text())
    OUT.mkdir(parents=True, exist_ok=True)
    for case in cases:
        if case["status"] != "supported":
            continue
        for index, variant in enumerate(case["variants"], start=1):
            image = Image.new("RGB", (900, 600), "white")
            draw = ImageDraw.Draw(image)
            draw.text((40, 25), f"{case['id'].replace('_', ' ').upper()} - VARIANT {index}", fill="black")
            _draw_feature(draw, case["id"], index)
            draw.text((40, 550), "ALL DIMENSIONS mm - SOLID PART FOR 3D PRINTING", fill="black")
            image.save(OUT / f"{case['id']}.{variant}.png")


def _draw_feature(draw: ImageDraw.ImageDraw, capability: str, variant: int) -> None:
    left, top = 120, 120
    length = 420 + variant * 35
    width = 220 + variant * 12
    draw.rectangle((left, top, left + length, top + width), outline="black", width=5)
    draw.text((left, top - 30), f"L {60 + variant * 10}", fill="black")
    draw.text((left + length + 15, top + width // 2), f"W {30 + variant * 5}", fill="black")

    if capability == "ribs":
        x = left + 70 + variant * 15
        draw.polygon([(x, top + width), (x + 90, top + width), (x, top + 70)], outline="black")
        draw.text((x, top + 35), f"RIB T{2 + variant}", fill="black")
    elif "perforations" in capability:
        count = 3 + variant
        if capability.startswith("linear"):
            for idx in range(count):
                x = left + 45 + idx * (length - 90) / max(1, count - 1)
                draw.ellipse((x - 12, top + width / 2 - 12, x + 12, top + width / 2 + 12), outline="black", width=4)
        else:
            import math
            cx, cy, radius = left + length / 2, top + width / 2, 65 + variant * 7
            for idx in range(count):
                angle = 2 * math.pi * idx / count
                x, y = cx + radius * math.cos(angle), cy + radius * math.sin(angle)
                draw.ellipse((x - 10, y - 10, x + 10, y + 10), outline="black", width=4)
        draw.text((left + 20, top + 20), f"{count} x D{3 + variant}", fill="black")
    elif capability == "slots":
        x, y = left + length / 2, top + width / 2
        slot_l, slot_w = 100 + variant * 20, 28 + variant * 4
        draw.rounded_rectangle((x - slot_l / 2, y - slot_w / 2, x + slot_l / 2, y + slot_w / 2), radius=slot_w / 2, outline="black", width=4)
        draw.text((x - 50, y + 30), f"SLOT {15 + variant * 3} x {4 + variant}", fill="black")
    elif capability == "pockets":
        inset = 35 + variant * 8
        draw.rectangle((left + inset, top + inset, left + length - inset, top + width - inset), outline="black", width=4)
        draw.text((left + inset, top + width / 2), f"DEPTH {1 + variant}", fill="black")
    elif capability == "shells":
        thickness = 12 + variant * 3
        draw.rectangle((left + thickness, top + thickness, left + length - thickness, top + width - thickness), outline="black", width=4)
        draw.line((left + length / 2, top, left + length / 2, top + width), fill="black", width=2)
        draw.text((left + 20, top + 20), f"OPEN TOP WALL T{1 + variant}", fill="black")
    elif capability == "text":
        draw.text((left + length / 3, top + width / 2), f"CUT V{variant}", fill="black", stroke_width=2)
        draw.text((left + 20, top + 20), f"RECESSED {0.5 + variant * 0.3:.1f}", fill="black")


if __name__ == "__main__":
    build()
