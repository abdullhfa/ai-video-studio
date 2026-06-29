from __future__ import annotations

from typing import cast

from video_pipeline import Scene

SOURCE_QURAN = "quran"
SOURCE_HADITH = "hadith"
SOURCE_HISTORICAL = "historical"
SOURCE_GENERAL = "general"
SOURCE_NARRATIVE = "narrative"
SOURCE_LANDSCAPE = "landscape"


def infer_source_confidence(scene: Scene, index: int = 0, total: int = 1) -> tuple[str, int]:
    if (scene.get("quran_verse") or "").strip():
        return SOURCE_QURAN, 100
    if (scene.get("presentation") or "").lower() == "quran_text":
        ref = (scene.get("quran_reference") or "").strip()
        return SOURCE_QURAN, 95 if ref else 85
    if (scene.get("hadith_text") or "").strip():
        return SOURCE_HADITH, 92

    note = (scene.get("historical_note") or "").strip()
    if note or (scene.get("source_type") or "") == SOURCE_GENERAL:
        return SOURCE_GENERAL, 40

    kind = (scene.get("scene_kind") or "").lower()
    if kind == "landscape":
        return SOURCE_LANDSCAPE, 70
    if kind in {"historical_event", "map_site"}:
        conf = 80 if index > 0 and index < total - 1 else 72
        return SOURCE_HISTORICAL, conf
    if kind == "closing_lesson":
        return SOURCE_NARRATIVE, 65

    explicit = (scene.get("source_type") or "").strip().lower()
    if explicit in {SOURCE_QURAN, SOURCE_HADITH, SOURCE_HISTORICAL, SOURCE_GENERAL, SOURCE_NARRATIVE}:
        conf = scene.get("confidence") or 0
        return explicit, conf if conf > 0 else 60

    return SOURCE_NARRATIVE, 60


def annotate_scene_confidence(scene: Scene, index: int = 0, total: int = 1) -> Scene:
    item = cast(Scene, dict(scene))
    source_type, confidence = infer_source_confidence(item, index, total)
    item["source_type"] = source_type
    item["confidence"] = confidence
    return item


def annotate_scenes_confidence(scenes: list[Scene]) -> list[Scene]:
    total = len(scenes)
    return [annotate_scene_confidence(scene, idx, total) for idx, scene in enumerate(scenes)]
