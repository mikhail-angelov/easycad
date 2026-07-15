from __future__ import annotations

from dataclasses import dataclass

from PIL import Image, ImageChops


@dataclass(frozen=True)
class SilhouetteMetrics:
    occupied_pixels: int
    bounds: tuple[int, int, int, int] | None


def silhouette_mask(image: Image.Image, *, threshold: int = 245) -> Image.Image:
    if image.mode == "1":
        return image.copy()
    grayscale = image.convert("L")
    return grayscale.point(lambda value: 255 if value < threshold else 0, mode="1")


def silhouette_metrics(mask: Image.Image) -> SilhouetteMetrics:
    bounds = mask.getbbox()
    occupied = sum(1 for value in mask.get_flattened_data() if value)
    return SilhouetteMetrics(occupied_pixels=occupied, bounds=bounds)


def compare_silhouettes(expected: Image.Image, actual: Image.Image) -> dict[str, float | tuple[int, int, int, int] | None]:
    expected_mask = silhouette_mask(expected)
    actual_mask = silhouette_mask(actual)
    if expected_mask.size != actual_mask.size:
        raise ValueError("silhouette images must have identical dimensions")
    expected_metrics = silhouette_metrics(expected_mask)
    actual_metrics = silhouette_metrics(actual_mask)
    difference = ImageChops.logical_xor(expected_mask, actual_mask)
    differing_pixels = sum(1 for value in difference.get_flattened_data() if value)
    total_pixels = expected_mask.width * expected_mask.height
    return {
        "expected_bounds": expected_metrics.bounds,
        "actual_bounds": actual_metrics.bounds,
        "expected_occupied_area": expected_metrics.occupied_pixels / total_pixels,
        "actual_occupied_area": actual_metrics.occupied_pixels / total_pixels,
        "symmetric_difference": differing_pixels / total_pixels,
    }
