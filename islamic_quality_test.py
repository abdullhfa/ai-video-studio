"""Quality preview for 4 Islamic stories — research, routing, timing, confidence."""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from media_router import route_scenes_media
from story_engagement import analyze_content_pacing
from topic_research import research_for_topic
from video_pipeline import OUTPUTS, Scene, _default_media_type

QUALITY_STORIES = [
    "قصة أهل الكهف",
    "قصة سيدنا يوسف",
    "أصحاب الأخدود",
    "أصحاب الفيل",
]

DEFAULT_SETTINGS: dict[str, Any] = {
    "content_profile": "islamic_story",
    "media_source": "images",
    "video_duration_sec": 600,
    "include_quran": True,
    "include_hadith": False,
    "historical_accuracy": True,
    "visual_style": "cinematic_islamic",
    "narrator_style": "وثائقي",
    "tts_speed": 0.95,
    "scene_cache_enabled": True,
    "max_ai_video_scenes": 3,
    "hook_scene": True,
    "cliffhanger": True,
    "lesson_summary": True,
}


def _visual_signature(scene: Scene) -> str:
    chars = ",".join(sorted(scene.get("character_ids") or []))
    kind = scene.get("scene_kind") or ""
    visual = (scene.get("visual") or scene.get("ai_prompt") or "")[:80].lower()
    return f"{chars}|{kind}|{visual}"


def visual_repetition_ratio(scenes: list[Scene]) -> float:
    if len(scenes) < 2:
        return 0.0
    sigs = [_visual_signature(s) for s in scenes]
    counts = Counter(sigs)
    repeated_slots = sum(count for count in counts.values() if count > 1)
    return round(repeated_slots / len(scenes), 3)


def _scene_summary(scene: Scene, idx: int) -> dict[str, Any]:
    return {
        "index": idx + 1,
        "screen_text": scene.get("screen_text", ""),
        "presentation": scene.get("presentation", ""),
        "scene_kind": scene.get("scene_kind", ""),
        "duration_sec": scene.get("duration_sec"),
        "audio_duration_sec": scene.get("audio_duration_sec"),
        "source_type": scene.get("source_type"),
        "confidence": scene.get("confidence"),
        "character_ids": scene.get("character_ids") or [],
        "visual_variation_seed": scene.get("visual_variation_seed"),
        "is_pivotal": scene.get("is_pivotal"),
        "media_priority": scene.get("media_priority"),
        "has_quran": bool(scene.get("quran_verse")),
        "engagement_role": scene.get("engagement_role"),
        "narration_words": len((scene.get("narration") or "").split()),
    }


def _story_metrics(scenes: list[Scene]) -> dict[str, Any]:
    durations = [float(s.get("duration_sec") or 0) for s in scenes if s.get("duration_sec")]
    quran_count = sum(1 for s in scenes if s.get("quran_verse") or s.get("presentation") == "quran_text")
    return {
        "avg_scene_duration_sec": round(sum(durations) / max(len(durations), 1), 2),
        "min_scene_duration_sec": round(min(durations), 2) if durations else 0,
        "max_scene_duration_sec": round(max(durations), 2) if durations else 0,
        "quran_ratio": round(quran_count / max(len(scenes), 1), 3),
        "visual_repetition_ratio": visual_repetition_ratio(scenes),
        "unique_variation_seeds": len({s.get("visual_variation_seed") for s in scenes}),
    }


def run_quality_preview(
    duration_sec: int = 600,
    settings: dict[str, Any] | None = None,
) -> dict[str, Any]:
    settings = {**DEFAULT_SETTINGS, **(settings or {}), "video_duration_sec": duration_sec}
    default_media = _default_media_type(settings)
    report: dict[str, Any] = {"duration_sec": duration_sec, "stories": [], "comparison": {}}

    for title in QUALITY_STORIES:
        entry: dict[str, Any] = {"topic": title, "ok": False}
        try:
            scenes, meta = research_for_topic(title, duration_sec, default_media, settings, log=None)
            scenes = route_scenes_media(scenes, title, settings)
            metrics = _story_metrics(scenes)
            total_dur = sum(float(s.get("duration_sec") or 0) for s in scenes)
            pacing = analyze_content_pacing(
                scenes,
                settings,
                int(total_dur) if total_dur > 0 else duration_sec,
            )
            presentations = [s.get("presentation") or "static" for s in scenes]
            entry.update(
                {
                    "ok": True,
                    "scene_count": len(scenes),
                    "estimated_total_sec": round(total_dur, 1),
                    "source": meta.get("source"),
                    "story_reference": meta.get("story_reference"),
                    "presentation_mix": {
                        p: presentations.count(p) for p in sorted(set(presentations))
                    },
                    "avg_confidence": round(
                        sum(s.get("confidence") or 0 for s in scenes) / max(len(scenes), 1),
                        1,
                    ),
                    "pivotal_scenes": sum(1 for s in scenes if s.get("is_pivotal")),
                    "quran_scenes": sum(1 for s in scenes if s.get("quran_verse")),
                    "content_pacing": pacing,
                    **metrics,
                    "scenes": [_scene_summary(s, i) for i, s in enumerate(scenes)],
                }
            )
        except Exception as exc:
            entry["error"] = str(exc)
        report["stories"].append(entry)

    ok_stories = [s for s in report["stories"] if s.get("ok")]
    if ok_stories:
        report["comparison"] = {
            "scene_count_range": [
                min(s["scene_count"] for s in ok_stories),
                max(s["scene_count"] for s in ok_stories),
            ],
            "avg_scene_duration_range": [
                min(s["avg_scene_duration_sec"] for s in ok_stories),
                max(s["avg_scene_duration_sec"] for s in ok_stories),
            ],
            "quran_ratio_range": [
                min(s["quran_ratio"] for s in ok_stories),
                max(s["quran_ratio"] for s in ok_stories),
            ],
            "repetition_ratio_range": [
                min(s["visual_repetition_ratio"] for s in ok_stories),
                max(s["visual_repetition_ratio"] for s in ok_stories),
            ],
            "pivotal_scenes": {s["topic"]: s["pivotal_scenes"] for s in ok_stories},
            "engagement_scores": {s["topic"]: s.get("content_pacing", {}).get("engagement_score") for s in ok_stories},
            "wpm_range": [
                min(s.get("content_pacing", {}).get("words_per_minute", 0) for s in ok_stories),
                max(s.get("content_pacing", {}).get("words_per_minute", 0) for s in ok_stories),
            ],
            "stable_pipeline": len(ok_stories) == len(QUALITY_STORIES),
        }

    return report


def save_quality_report(report: dict[str, Any]) -> Path:
    OUTPUTS.mkdir(parents=True, exist_ok=True)
    out = OUTPUTS / "islamic_quality_report.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return out
