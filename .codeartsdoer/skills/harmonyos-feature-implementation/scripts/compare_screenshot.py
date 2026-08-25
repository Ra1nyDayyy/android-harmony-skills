#!/usr/bin/env python3
"""Deterministic Pillow screenshot comparator with full-region and element masks."""

from __future__ import annotations

import math
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
    if expected.size != actual.size or expected.width < 1 or expected.height < 1:
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
    for top in range(0, expected.height, 8):
        for left in range(0, expected.width, 8):
            bottom, right = min(top + 8, expected.height), min(left + 8, expected.width)
            xs = [_luminance(expected_pixels[x, y]) for y in range(top, bottom) for x in range(left, right)]
            ys = [_luminance(actual_pixels[x, y]) for y in range(top, bottom) for x in range(left, right)]
            count = len(xs)
            mean_x, mean_y = sum(xs) / count, sum(ys) / count
            denominator = max(count - 1, 1)
            var_x = sum((value - mean_x) ** 2 for value in xs) / denominator
            var_y = sum((value - mean_y) ** 2 for value in ys) / denominator
            covariance = sum((xs[index] - mean_x) * (ys[index] - mean_y) for index in range(count)) / denominator
            scores.append(((2 * mean_x * mean_y + c1) * (2 * covariance + c2)) / ((mean_x ** 2 + mean_y ** 2 + c1) * (var_x + var_y + c2)))
    return (sum(scores) / len(scores), changed / total)


def _finite(value: Any, label: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(float(value)):
        raise ValueError(f"Screenshot {label} must be finite")
    return float(value)


def _geometry_root(contract: dict[str, Any]) -> dict[str, Any]:
    values = contract.get("source_geometry")
    if not isinstance(values, list) or len(values) != 1 or not isinstance(values[0], dict):
        raise ValueError("Screenshot comparison requires one state/environment source geometry object")
    return values[0]


def _region(value: dict[str, Any], label: str) -> tuple[tuple[int, int, int, int], float]:
    region = value.get("application_region")
    density = _finite(value.get("density"), f"{label} density")
    if not isinstance(region, dict) or density <= 0:
        raise ValueError(f"Screenshot {label} application region/density is missing")
    x, y = _finite(region.get("x"), f"{label} region x"), _finite(region.get("y"), f"{label} region y")
    width, height = _finite(region.get("width"), f"{label} region width"), _finite(region.get("height"), f"{label} region height")
    if min(x, y) < 0 or width <= 0 or height <= 0:
        raise ValueError(f"Screenshot {label} application region is invalid")
    return (round(x), round(y), round(x + width), round(y + height)), density


def _aligned_regions(
    expected: Image.Image, actual: Image.Image, source: dict[str, Any], snapshot: dict[str, Any]
) -> tuple[Image.Image, Image.Image, list[dict[str, object]], float]:
    expected_box, expected_density = _region(source, "expected")
    actual_box, actual_density = _region(snapshot, "actual")
    if expected_box[2] > expected.width or expected_box[3] > expected.height or actual_box[2] > actual.width or actual_box[3] > actual.height:
        raise ValueError("Screenshot application region exceeds image bounds")
    expected_dp = ((expected_box[2] - expected_box[0]) / expected_density, (expected_box[3] - expected_box[1]) / expected_density)
    actual_dp = ((actual_box[2] - actual_box[0]) / actual_density, (actual_box[3] - actual_box[1]) / actual_density)
    differences: list[dict[str, object]] = []
    if any(abs(expected_dp[index] - actual_dp[index]) > 0.5 for index in (0, 1)):
        differences.append({"kind": "APPLICATION_REGION_MISMATCH", "expected_dp": expected_dp, "actual_dp": actual_dp})
    target = (max(1, round(expected_dp[0])), max(1, round(expected_dp[1])))
    resampling = getattr(Image, "Resampling", Image).BILINEAR
    return (
        expected.crop(expected_box).resize(target, resampling),
        actual.crop(actual_box).resize(target, resampling),
        differences,
        expected_density,
    )


def compare_screenshot(
    contract: dict[str, Any], snapshot: dict[str, Any], expected_path: Path, actual_path: Path, artifact_dir: Path
) -> ComparisonResult:
    policy = contract.get("comparison_policy") if isinstance(contract.get("comparison_policy"), dict) else {}
    minimum_ssim = float(policy.get("application_region_ssim", 0.98))
    maximum_changed = float(policy.get("changed_pixel_ratio", 0.02))
    with Image.open(expected_path) as expected_source, Image.open(actual_path) as actual_source:
        # 分辨率硬校验：Android 与 Harmony 截图必须同分辨率/同方向；不一致即 BLOCKED。
        # （不允许 resize 强行对齐——那会掩盖真实几何差异）
        if expected_source.size != actual_source.size:
            raise ValueError(
                f"Screenshot resolution mismatch: Android={expected_source.size} Harmony={actual_source.size}; "
                "both emulators must use the same frozen resolution/density (see phase-2 evidence_index resolution and "
                "H4ENV screen_resolution). Fix the environment instead of resizing."
            )
        source = _geometry_root(contract)
        expected, actual, differences, source_density = _aligned_regions(
            expected_source.convert("RGB"), actual_source.convert("RGB"), source, snapshot
        )
        ssim, changed = _metrics(expected, actual)
        if ssim < minimum_ssim:
            differences.append({"kind": "SSIM_BELOW_THRESHOLD", "actual": round(ssim, 8), "minimum": minimum_ssim})
        if changed > maximum_changed:
            differences.append({"kind": "CHANGED_PIXEL_RATIO_ABOVE_THRESHOLD", "actual": round(changed, 8), "maximum": maximum_changed})
        mask_failures = 0
        mask_minimum_ssim = max(minimum_ssim, 0.995)
        mask_maximum_changed = min(maximum_changed, 0.005)
        if expected.size == actual.size:
            for row in source.get("components", []):
                if not isinstance(row, dict) or not row.get("component_id") or not all(key in row for key in ("x", "y", "width", "height")):
                    continue
                region = source["application_region"]
                box = (
                    round((_finite(row["x"], "mask x") - _finite(region["x"], "region x")) / source_density),
                    round((_finite(row["y"], "mask y") - _finite(region["y"], "region y")) / source_density),
                    round((_finite(row["x"], "mask x") - _finite(region["x"], "region x") + _finite(row["width"], "mask width")) / source_density),
                    round((_finite(row["y"], "mask y") - _finite(region["y"], "region y") + _finite(row["height"], "mask height")) / source_density),
                )
                if box[0] < 0 or box[1] < 0 or box[2] > expected.width or box[3] > expected.height or box[2] <= box[0] or box[3] <= box[1]:
                    raise ValueError(f"Screenshot required mask is outside application region: {row['component_id']}")
                mask_ssim, mask_changed = _metrics(expected.crop(box), actual.crop(box))
                if mask_ssim < mask_minimum_ssim or mask_changed > mask_maximum_changed:
                    mask_failures += 1
                    differences.append({"kind": "REQUIRED_ELEMENT_MASK_MISMATCH", "component_id": str(row["component_id"]), "ssim": round(mask_ssim, 8), "minimum_ssim": mask_minimum_ssim, "changed_pixel_ratio": round(mask_changed, 8), "maximum_changed_pixel_ratio": mask_maximum_changed})
        artifact_dir.mkdir(parents=True, exist_ok=True)
        diff = ImageChops.difference(expected, actual)
        diff.save(artifact_dir / "diff.png")
        Image.blend(expected, actual, 0.5).save(artifact_dir / "overlay.png")
    return comparison_result(
        "CMP-SCREENSHOT", "screenshot", {"sha256": file_sha256(expected_path)}, {"sha256": file_sha256(actual_path)},
        {"ssim": round(ssim, 8), "changed_pixel_ratio": round(changed, 8), "mask_failures": mask_failures, "color_threshold": COLOR_THRESHOLD}, differences,
    )
