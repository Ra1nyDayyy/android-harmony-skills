#!/usr/bin/env python3
"""Deterministic Pillow screenshot comparator with full-region and element masks."""

from __future__ import annotations

from pathlib import Path
from typing import Any

try:
    from PIL import Image, ImageChops
except ImportError as exc:  # pragma: no cover - exercised in dependency-free production
    raise ImportError(
        "Pillow is required for deterministic screenshot comparison; install requirements-ci.txt"
    ) from exc

from comparison_common import ComparisonResult, comparison_result, file_sha256


COLOR_THRESHOLD = 8


def _luminance(pixel: tuple[int, ...]) -> float:
    return 0.299 * pixel[0] + 0.587 * pixel[1] + 0.114 * pixel[2]


def _metrics(expected: Image.Image, actual: Image.Image) -> tuple[float, float]:
    if expected.size != actual.size or expected.width < 8 or expected.height < 8:
        return (-1.0, 1.0)
    expected_rgb, actual_rgb = expected.convert("RGB"), actual.convert("RGB")
    scores: list[float] = []
    changed = 0
    total = expected.width * expected.height
    expected_pixels, actual_pixels = expected_rgb.load(), actual_rgb.load()
    for y in range(expected.height):
        for x in range(expected.width):
            if any(abs(expected_pixels[x, y][channel] - actual_pixels[x, y][channel]) > COLOR_THRESHOLD for channel in range(3)):
                changed += 1
    c1, c2 = (0.01 * 255) ** 2, (0.03 * 255) ** 2
    for top in range(0, expected.height - 7, 8):
        for left in range(0, expected.width - 7, 8):
            xs = [_luminance(expected_pixels[x, y]) for y in range(top, top + 8) for x in range(left, left + 8)]
            ys = [_luminance(actual_pixels[x, y]) for y in range(top, top + 8) for x in range(left, left + 8)]
            mean_x, mean_y = sum(xs) / 64.0, sum(ys) / 64.0
            var_x = sum((value - mean_x) ** 2 for value in xs) / 63.0
            var_y = sum((value - mean_y) ** 2 for value in ys) / 63.0
            covariance = sum((xs[index] - mean_x) * (ys[index] - mean_y) for index in range(64)) / 63.0
            scores.append(((2 * mean_x * mean_y + c1) * (2 * covariance + c2)) / ((mean_x ** 2 + mean_y ** 2 + c1) * (var_x + var_y + c2)))
    return (sum(scores) / len(scores), changed / total)


def compare_screenshot(
    contract: dict[str, Any], expected_path: Path, actual_path: Path, artifact_dir: Path
) -> ComparisonResult:
    policy = contract.get("comparison_policy") if isinstance(contract.get("comparison_policy"), dict) else {}
    minimum_ssim = float(policy.get("application_region_ssim", 0.98))
    maximum_changed = float(policy.get("changed_pixel_ratio", 0.02))
    with Image.open(expected_path) as expected_source, Image.open(actual_path) as actual_source:
        expected, actual = expected_source.convert("RGB"), actual_source.convert("RGB")
        ssim, changed = _metrics(expected, actual)
        differences: list[dict[str, object]] = []
        if expected.size != actual.size:
            differences.append({"kind": "IMAGE_SIZE_MISMATCH", "expected": expected.size, "actual": actual.size})
        if ssim < minimum_ssim:
            differences.append({"kind": "SSIM_BELOW_THRESHOLD", "actual": round(ssim, 8), "minimum": minimum_ssim})
        if changed > maximum_changed:
            differences.append({"kind": "CHANGED_PIXEL_RATIO_ABOVE_THRESHOLD", "actual": round(changed, 8), "maximum": maximum_changed})
        mask_failures = 0
        if expected.size == actual.size:
            geometry = contract.get("source_geometry") if isinstance(contract.get("source_geometry"), list) else []
            flat = geometry[0] if geometry and isinstance(geometry[0], list) else geometry
            for row in flat:
                if not isinstance(row, dict) or not row.get("component_id") or not all(key in row for key in ("x", "y", "width", "height")):
                    continue
                box = (int(row["x"]), int(row["y"]), int(row["x"] + row["width"]), int(row["y"] + row["height"]))
                mask_ssim, mask_changed = _metrics(expected.crop(box), actual.crop(box))
                if mask_ssim < minimum_ssim or mask_changed > maximum_changed:
                    mask_failures += 1
                    differences.append({"kind": "REQUIRED_ELEMENT_MASK_MISMATCH", "component_id": str(row["component_id"]), "ssim": round(mask_ssim, 8), "changed_pixel_ratio": round(mask_changed, 8)})
        artifact_dir.mkdir(parents=True, exist_ok=True)
        diff = ImageChops.difference(expected, actual) if expected.size == actual.size else Image.new("RGB", expected.size, (255, 0, 0))
        diff.save(artifact_dir / "diff.png")
        Image.blend(expected, actual.resize(expected.size), 0.5).save(artifact_dir / "overlay.png")
    return comparison_result(
        "CMP-SCREENSHOT", "screenshot", {"sha256": file_sha256(expected_path)}, {"sha256": file_sha256(actual_path)},
        {"ssim": round(ssim, 8), "changed_pixel_ratio": round(changed, 8), "mask_failures": mask_failures, "color_threshold": COLOR_THRESHOLD}, differences,
    )
