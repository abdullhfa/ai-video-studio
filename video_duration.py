from __future__ import annotations

from typing import Any, Mapping, TypeVar, cast

MAX_DURATION_SEC = 3600
MIN_DURATION_SEC = 60
MAX_SCENES = 240
CHAPTER_THRESHOLD_SEC = 1800
CHAPTER_LENGTH_SEC = 600

TARGET_DURATION_OPTIONS: list[dict[str, int | str]] = [
    {"seconds": 60, "label": "1 دقيقة"},
    {"seconds": 180, "label": "3 دقائق"},
    {"seconds": 300, "label": "5 دقائق"},
    {"seconds": 600, "label": "10 دقائق"},
    {"seconds": 900, "label": "15 دقيقة"},
    {"seconds": 1800, "label": "30 دقيقة"},
    {"seconds": 2700, "label": "45 دقيقة"},
    {"seconds": 3600, "label": "60 دقيقة"},
]

VIDEO_MODES = {
    "short": {"min_sec": 60, "max_sec": 180, "label_ar": "Short", "label_desc": "1–3 دقائق"},
    "standard": {"min_sec": 181, "max_sec": 600, "label_ar": "Standard", "label_desc": "3–10 دقائق"},
    "long": {"min_sec": 601, "max_sec": 1800, "label_ar": "Long", "label_desc": "10–30 دقيقة"},
    "course": {"min_sec": 1801, "max_sec": 3600, "label_ar": "Course", "label_desc": "30–60 دقيقة"},
}

MEDIA_MIX_LONG = (
    ("pexels", 40),
    ("ai", 30),
    ("slide", 20),
    ("screen", 10),
)


def clamp_duration(duration_sec: int) -> int:
    return max(MIN_DURATION_SEC, min(MAX_DURATION_SEC, duration_sec))


def get_video_mode(duration_sec: int) -> str:
    duration_sec = clamp_duration(duration_sec)
    for mode, cfg in VIDEO_MODES.items():
        if cfg["min_sec"] <= duration_sec <= cfg["max_sec"]:
            return mode
    return "course"


def get_mode_info(duration_sec: int) -> dict:
    mode = get_video_mode(duration_sec)
    cfg = VIDEO_MODES[mode]
    return {
        "mode": mode,
        "label": cfg["label_ar"],
        "description": cfg["label_desc"],
        "target_scenes": segment_count(duration_sec),
        "uses_chapters": uses_chapters(duration_sec),
        "chapter_count": chapter_count(duration_sec) if uses_chapters(duration_sec) else 1,
    }


def segment_count(duration_sec: int) -> int:
    duration_sec = clamp_duration(duration_sec)
    return max(5, min(MAX_SCENES, round(duration_sec / 15)))


def uses_chapters(duration_sec: int) -> bool:
    return clamp_duration(duration_sec) >= CHAPTER_THRESHOLD_SEC


def chapter_count(duration_sec: int) -> int:
    duration_sec = clamp_duration(duration_sec)
    return max(3, min(6, round(duration_sec / CHAPTER_LENGTH_SEC)))


def plan_chapters(topic: str, duration_sec: int) -> list[dict]:
    duration_sec = clamp_duration(duration_sec)
    if not uses_chapters(duration_sec):
        return [
            {
                "index": 1,
                "title": topic,
                "topic": topic,
                "duration_sec": duration_sec,
                "scene_target": segment_count(duration_sec),
            }
        ]

    count = chapter_count(duration_sec)
    base = duration_sec // count
    remainder = duration_sec - base * count
    templates = ["مقدمة", "التثبيت والإعداد", "التطبيق العملي", "الواجهات والمكونات", "الاختبار والنشر", "الختام والملخص"]

    chapters: list[dict] = []
    for idx in range(count):
        extra = 1 if idx < remainder else 0
        ch_duration = base + extra
        label = templates[idx] if idx < len(templates) else f"الفصل {idx + 1}"
        chapters.append(
            {
                "index": idx + 1,
                "title": f"{label} — {topic}",
                "topic": topic,
                "duration_sec": ch_duration,
                "scene_target": segment_count(ch_duration),
            }
        )
    return chapters


SceneT = TypeVar("SceneT")


def assign_media_mix(scenes: list[SceneT], duration_sec: int) -> list[SceneT]:
    if clamp_duration(duration_sec) < 600 or not scenes:
        return scenes

    total = len(scenes)
    assigned: list[str] = []
    for media_type, percent in MEDIA_MIX_LONG:
        assigned.extend([media_type] * round(total * percent / 100))

    while len(assigned) < total:
        assigned.append("pexels")
    assigned = assigned[:total]

    mixed: list[SceneT] = []
    for scene, media_type in zip(scenes, assigned):
        item = dict(cast(Mapping[str, Any], scene))
        if item.get("router_locked"):
            mixed.append(cast(SceneT, item))
            continue
        if item.get("media_type") in {None, "", "pexels", "ai"} or item.get("media_type") == _default_media_for_mode(duration_sec):
            item["media_type"] = media_type
        mixed.append(cast(SceneT, item))
    return mixed


def _default_media_for_mode(duration_sec: int) -> str:
    return "pexels"
