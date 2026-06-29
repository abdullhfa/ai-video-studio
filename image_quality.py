from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageFilter

# Minimum scores (0.0–1.0) to accept an image without regeneration.
DEFAULT_MIN_QUALITY = 0.45
DEFAULT_MAX_FACE = 0.38
DEFAULT_MAX_TEXT = 0.42


def _rgb_to_hsv(rgb: np.ndarray) -> np.ndarray:
    rgb = rgb.astype(np.float32) / 255.0
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    cmax = np.max(rgb, axis=-1)
    cmin = np.min(rgb, axis=-1)
    delta = cmax - cmin
    h = np.zeros_like(cmax)
    mask = delta > 1e-5
    idx = np.argmax(rgb, axis=-1)
    h[mask & (idx == 0)] = ((g - b) / (delta + 1e-6))[mask & (idx == 0)] % 6
    h[mask & (idx == 1)] = ((b - r) / (delta + 1e-6))[mask & (idx == 1)] + 2
    h[mask & (idx == 2)] = ((r - g) / (delta + 1e-6))[mask & (idx == 2)] + 4
    h = (h / 6.0) % 1.0
    s = np.where(cmax > 1e-5, delta / (cmax + 1e-6), 0.0)
    v = cmax
    return np.stack([h, s, v], axis=-1)


def _laplacian_variance(gray: np.ndarray) -> float:
    kernel = np.array([[0, 1, 0], [1, -4, 1], [0, 1, 0]], dtype=np.float32)
    from numpy.lib.stride_tricks import sliding_window_view

    padded = np.pad(gray, 1, mode="edge")
    patches = sliding_window_view(padded, (3, 3))
    conv = (patches * kernel).sum(axis=(-2, -1))
    return float(np.var(conv))


def evaluate_image_quality(image_path: Path) -> dict[str, Any]:
    """Heuristic quality check without ML models — fast and offline."""
    with Image.open(image_path) as img:
        rgb = img.convert("RGB")
        small = rgb.resize((512, 288), Image.Resampling.LANCZOS)
        arr = np.asarray(small, dtype=np.float32)

    gray = arr.mean(axis=-1)
    sharpness = _laplacian_variance(gray)
    quality_score = min(1.0, sharpness / 120.0)

    brightness = float(gray.mean() / 255.0)
    if brightness < 0.08 or brightness > 0.94:
        quality_score *= 0.7

    h, w = gray.shape
    cy0, cy1 = h // 4, 3 * h // 4
    cx0, cx1 = w // 4, 3 * w // 4
    center = arr[cy0:cy1, cx0:cx1]
    hsv = _rgb_to_hsv(center)
    hue, sat, val = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    skin_mask = (
        (hue < 0.12)
        & (sat > 0.18)
        & (sat < 0.68)
        & (val > 0.25)
        & (val < 0.92)
    )
    face_visibility_score = float(skin_mask.mean())

    edges = np.asarray(small.convert("L").filter(ImageFilter.FIND_EDGES), dtype=np.float32) / 255.0
    strong = edges > 0.45
    # Text-like: many small strong-edge clusters in horizontal bands
    band_scores: list[float] = []
    rows = 8
    for row in range(rows):
        y0 = row * h // rows
        y1 = (row + 1) * h // rows
        band = strong[y0:y1, :]
        band_scores.append(float(band.mean()))
    text_artifact_score = min(1.0, float(np.std(band_scores) * 4.0 + np.mean(band_scores) * 0.8))

    return {
        "quality_score": round(quality_score, 3),
        "face_visibility_score": round(face_visibility_score, 3),
        "text_artifact_score": round(text_artifact_score, 3),
        "brightness": round(brightness, 3),
        "sharpness_raw": round(sharpness, 2),
    }


def passes_quality(
    scores: dict[str, Any],
    *,
    min_quality: float = DEFAULT_MIN_QUALITY,
    max_face: float = DEFAULT_MAX_FACE,
    max_text: float = DEFAULT_MAX_TEXT,
) -> bool:
    q = float(scores.get("quality_score") or 0)
    face = float(scores.get("face_visibility_score") or 0)
    text = float(scores.get("text_artifact_score") or 0)
    if q < min_quality:
        return False
    if face > max_face:
        return False
    if text > max_text:
        return False
    return True


def regeneration_prompt_suffix(attempt: int) -> str:
    fixes = (
        "no visible faces, silhouettes only, fix anatomy, no extra fingers",
        "no text, no letters, no watermark, no captions, clean image",
        "consistent lighting, no distorted limbs, cinematic still frame",
    )
    return fixes[attempt % len(fixes)]
