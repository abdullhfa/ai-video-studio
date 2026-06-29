from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, cast

from moviepy.editor import AudioFileClip

from video_pipeline import Scene

SCENE_TAIL_PADDING_SEC = 0.45

# Bounds by presentation / scene role (seconds, including tail padding).
DURATION_BOUNDS: dict[str, tuple[float, float]] = {
    "opening": (8.0, 14.0),
    "closing": (10.0, 22.0),
    "quran_text": (8.0, 18.0),
    "map_slide": (8.0, 16.0),
    "main": (12.0, 28.0),
    "landscape": (6.0, 14.0),
    "default": (6.0, 30.0),
}


def _tts_speed(settings: dict[str, Any] | None) -> float:
    settings = settings or {}
    try:
        return max(0.7, min(1.3, float(settings.get("tts_speed", 1.0) or 1.0)))
    except (TypeError, ValueError):
        return 1.0


def estimate_narration_seconds(text: str, settings: dict[str, Any] | None = None) -> float:
    """Rough Arabic TTS duration from word count (~2.4 words/sec at speed 1.0)."""
    words = [w for w in (text or "").split() if w.strip()]
    if not words:
        return 0.0
    base_wps = 2.35 / _tts_speed(settings)
    return max(2.0, len(words) / base_wps)


def _scene_role(scene: Scene, index: int, total: int) -> str:
    presentation = (scene.get("presentation") or "").lower()
    kind = (scene.get("scene_kind") or "").lower()
    if presentation == "quran_text":
        return "quran_text"
    if presentation == "map_slide" or kind == "map_site":
        return "map_slide"
    if kind == "landscape":
        return "landscape"
    if kind == "closing_lesson" or index == total - 1:
        return "closing"
    if index == 0:
        return "opening"
    return "main"


def duration_bounds(scene: Scene, index: int = 0, total: int = 1) -> tuple[float, float]:
    role = _scene_role(scene, index, total)
    return DURATION_BOUNDS.get(role, DURATION_BOUNDS["default"])


def measure_audio_seconds(audio_path: Path) -> float:
    clip = AudioFileClip(str(audio_path))
    try:
        return float(clip.duration or 0.0)
    finally:
        clip.close()


def resolve_scene_duration(
    scene: Scene,
    audio_seconds: float,
    *,
    index: int = 0,
    total: int = 1,
    settings: dict[str, Any] | None = None,
) -> float:
    """Audio drives scene length; bounds only pad short clips or cap extremes."""
    settings = settings or {}
    min_dur, max_dur = duration_bounds(scene, index, total)
    tail = SCENE_TAIL_PADDING_SEC
    if audio_seconds <= 0:
        audio_seconds = estimate_narration_seconds(scene.get("narration") or "", settings)

    raw = audio_seconds + tail
    if raw < min_dur:
        return min_dur
    if raw > max_dur:
        # Never truncate narration audio in compose — cap is soft metadata only.
        return raw
    return raw


def sync_scenes_to_audio(
    scenes: list[Scene],
    audio_paths: list[Path],
    settings: dict[str, Any] | None = None,
    log: Callable[[str], None] | None = None,
) -> list[Scene]:
    """After TTS: store real audio duration on each scene for assembly."""
    settings = settings or {}
    synced: list[Scene] = []
    total = len(scenes)
    for idx, (scene, audio_path) in enumerate(zip(scenes, audio_paths)):
        item = cast(Scene, dict(scene))
        audio_sec = measure_audio_seconds(audio_path)
        clip_dur = resolve_scene_duration(item, audio_sec, index=idx, total=total, settings=settings)
        item["audio_duration_sec"] = round(audio_sec, 2)
        item["duration_sec"] = round(clip_dur, 1)
        if log:
            role = _scene_role(item, idx, total)
            log(
                f"  ⏱️ مشهد {idx + 1}: صوت {audio_sec:.1f}s → مدة {clip_dur:.1f}s ({role})"
            )
        synced.append(item)
    return synced


def pre_estimate_scene_durations(
    scenes: list[Scene],
    settings: dict[str, Any] | None = None,
) -> list[Scene]:
    """Planner estimate before TTS (research / quality preview)."""
    settings = settings or {}
    total = len(scenes)
    estimated: list[Scene] = []
    for idx, scene in enumerate(scenes):
        item = cast(Scene, dict(scene))
        est = estimate_narration_seconds(item.get("narration") or "", settings)
        dur = resolve_scene_duration(item, est, index=idx, total=total, settings=settings)
        item["duration_sec"] = round(dur, 1)
        item["audio_duration_sec"] = round(est, 1)
        estimated.append(item)
    return estimated
