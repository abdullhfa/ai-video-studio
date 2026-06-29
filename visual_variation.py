from __future__ import annotations

from typing import cast

from video_pipeline import Scene

CAMERA_ANGLES = (
    "wide establishing shot from a distance",
    "medium shot from the left, three-quarter view",
    "low angle looking upward, dramatic scale",
    "high angle overview, birds-eye composition",
    "over-the-shoulder silhouette perspective",
    "side profile silhouette from the right",
    "diagonal dutch angle for visual tension",
    "rear view walking into the scene",
)

COMPOSITIONS = (
    "rule of thirds with subject off-center",
    "symmetrical framing with leading lines",
    "foreground rocks framing the subject",
    "depth layers: foreground, midground, background",
    "negative space on the left for balance",
    "cinematic letterbox composition",
    "path or road leading into the distance",
    "arch or cave mouth framing the scene",
)

ENVIRONMENT_DETAILS = (
    "morning mist and soft golden light",
    "late afternoon long shadows",
    "wind-blown sand and drifting dust",
    "distant mountains under cloudy sky",
    "torchlight glow on stone walls",
    "starlit night with moon haze",
    "olive trees and rocky terrain",
    "ancient stone ruins in background",
)


def variation_suffix(seed: int, *, attempt: int = 0) -> str:
    idx = max(1, seed) + attempt
    angle = CAMERA_ANGLES[idx % len(CAMERA_ANGLES)]
    comp = COMPOSITIONS[(idx // 2) % len(COMPOSITIONS)]
    env = ENVIRONMENT_DETAILS[(idx // 3) % len(ENVIRONMENT_DETAILS)]
    return (
        f"different camera angle: {angle}, "
        f"different composition: {comp}, "
        f"different environment details: {env}, "
        f"unique visual variation #{idx}, not a repeat of prior frames"
    )


def append_visual_variation(prompt: str, seed: int, *, attempt: int = 0) -> str:
    base = (prompt or "").strip().rstrip(",")
    suffix = variation_suffix(seed, attempt=attempt)
    if suffix.lower() in base.lower():
        return base
    if not base:
        return suffix
    return f"{base}, {suffix}"


def assign_visual_variation_seeds(scenes: list[Scene]) -> list[Scene]:
    varied: list[Scene] = []
    for idx, scene in enumerate(scenes):
        item = cast(Scene, dict(scene))
        raw = item.get("visual_variation_seed")
        if isinstance(raw, int) and raw != 0:
            seed = raw
        else:
            seed = idx + 1
        item["visual_variation_seed"] = seed
        ai_prompt = item.get("ai_prompt")
        if ai_prompt:
            item["ai_prompt"] = append_visual_variation(ai_prompt, seed)
        varied.append(item)
    return varied
