from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from islamic_quality_test import visual_repetition_ratio
from video_pipeline import ROOT, Scene

OUTPUTS = ROOT / "outputs"
REPORT_PATH = OUTPUTS / "production_report.json"
HISTORY_PATH = OUTPUTS / "production_history.json"


class ProductionTracker:
    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.topic = ""
        self.settings: dict[str, Any] = {}
        self.started_at = 0.0
        self.cache_hits = 0
        self.cache_misses = 0
        self.image_cache_hits = 0
        self.audio_cache_hits = 0
        self.images_generated = 0
        self.images_regenerated = 0
        self.audio_generated = 0
        self.quality_scores: list[float] = []
        self.video_duration_sec = 0.0
        self.output_path: Path | None = None
        self.error: str | None = None
        self.chaptered = False
        self.tts_time_sec = 0.0
        self.media_time_sec = 0.0
        self.render_time_sec = 0.0
        self.session_id = ""
        self.research_source = ""

    def start(self, topic: str, settings: dict[str, Any], *, chaptered: bool = False) -> None:
        self.reset()
        self.topic = topic
        self.settings = dict(settings)
        self.chaptered = chaptered
        self.started_at = time.monotonic()
        try:
            from production_session import get_current_session

            session = get_current_session()
            if session:
                self.session_id = session.session_id
                self.research_source = session.research_source
        except ImportError:
            pass

    def elapsed_sec(self) -> int:
        if self.started_at <= 0:
            return 0
        return int(time.monotonic() - self.started_at)

    def record_cache_hit(self, kind: str = "generic") -> None:
        self.cache_hits += 1
        if kind == "image":
            self.image_cache_hits += 1
        elif kind == "audio":
            self.audio_cache_hits += 1

    def record_cache_miss(self) -> None:
        self.cache_misses += 1

    def record_image_generated(self, *, regenerated: bool = False, quality_score: float | None = None) -> None:
        self.images_generated += 1
        if regenerated:
            self.images_regenerated += 1
        if quality_score is not None:
            self.quality_scores.append(quality_score)

    def record_audio_generated(self) -> None:
        self.audio_generated += 1

    def record_video_duration(self, seconds: float) -> None:
        self.video_duration_sec = max(self.video_duration_sec, float(seconds or 0))

    def record_phase_time(self, phase: str, seconds: float) -> None:
        elapsed = max(0.0, float(seconds or 0))
        if phase == "tts":
            self.tts_time_sec += elapsed
        elif phase == "media":
            self.media_time_sec += elapsed
        elif phase == "render":
            self.render_time_sec += elapsed


_tracker: ProductionTracker | None = None


def active_tracker() -> ProductionTracker | None:
    return _tracker


def start_production_run(
    topic: str,
    settings: dict[str, Any],
    *,
    chaptered: bool = False,
) -> ProductionTracker:
    global _tracker
    _tracker = ProductionTracker()
    _tracker.start(topic, settings, chaptered=chaptered)
    return _tracker


def predict_youtube_retention(
    scenes: list[Scene],
    pacing: dict[str, Any],
    scene_metrics: dict[str, Any],
) -> tuple[int, dict[str, int]]:
    """تقدير 0–100 لمدى احتفاظ المشاهد — ليس دقيقاً، لكنه يفرض مراجعة YouTube-first."""

    hook_scene = next((s for s in scenes if (s.get("engagement_role") or "") == "hook"), None)
    if hook_scene is None and scenes:
        hook_scene = scenes[0]

    hook_words = len((hook_scene.get("narration") or "").split()) if hook_scene else 0
    hook_strength = 40
    if hook_scene and (hook_scene.get("engagement_role") or "") == "hook":
        hook_strength += 25
    if 35 <= hook_words <= 90:
        hook_strength += 20
    elif hook_words >= 20:
        hook_strength += 10
    hook_strength = min(100, hook_strength)

    durations = [float(s.get("duration_sec") or s.get("audio_duration_sec") or 0) for s in scenes]
    durations = [d for d in durations if d > 0]
    scene_pacing = 55
    if durations:
        avg = sum(durations) / len(durations)
        long_scenes = sum(1 for d in durations if d > 45)
        short_scenes = sum(1 for d in durations if d < 4)
        if 8 <= avg <= 28:
            scene_pacing += 25
        elif 5 <= avg <= 35:
            scene_pacing += 12
        scene_pacing -= min(30, long_scenes * 8)
        scene_pacing -= min(20, short_scenes * 5)
    scene_pacing = max(0, min(100, scene_pacing))

    wpm = float(pacing.get("words_per_minute") or 0)
    wpm_range = pacing.get("wpm_target_range") or [120, 150]
    wpm_min, wpm_max = int(wpm_range[0]), int(wpm_range[1])
    if wpm_min <= wpm <= wpm_max:
        narration_speed = 90
    elif wpm_min - 20 <= wpm <= wpm_max + 25:
        narration_speed = 70
    else:
        narration_speed = 45

    rep = float(scene_metrics.get("visual_repetition_ratio") or 0.5)
    visual_variety = max(0, min(100, int((1.0 - rep) * 100)))

    cliff_count = sum(1 for s in scenes if (s.get("engagement_role") or "") == "cliffhanger")
    pivotal = sum(1 for s in scenes if s.get("is_pivotal"))
    tension_points = min(100, 40 + cliff_count * 12 + pivotal * 5)

    factors = {
        "hook_strength": hook_strength,
        "scene_pacing": scene_pacing,
        "narration_speed": narration_speed,
        "visual_variety": visual_variety,
        "tension_points": tension_points,
    }
    weights = {
        "hook_strength": 0.25,
        "scene_pacing": 0.20,
        "narration_speed": 0.20,
        "visual_variety": 0.20,
        "tension_points": 0.15,
    }
    score = round(sum(factors[k] * weights[k] for k in factors))
    return max(0, min(100, score)), factors


def build_production_report(
    topic: str,
    scenes: list[Scene],
    settings: dict[str, Any],
    output_path: Path | None,
    tracker: ProductionTracker,
) -> dict[str, Any]:
    from content_profiles import detect_content_profile
    from production_session import PIPELINE_VERSION
    from story_engagement import analyze_content_pacing

    target = int(settings.get("video_duration_sec") or 60)
    video_dur = tracker.video_duration_sec
    if video_dur <= 0:
        video_dur = sum(float(s.get("duration_sec") or s.get("audio_duration_sec") or 0) for s in scenes)

    pacing = analyze_content_pacing(
        scenes,
        settings,
        int(video_dur) if video_dur > 0 else target,
    )
    scene_metrics = {
        "scene_count": len(scenes),
        "avg_scene_duration_sec": round(
            sum(float(s.get("duration_sec") or 0) for s in scenes) / max(len(scenes), 1),
            2,
        ),
        "visual_repetition_ratio": visual_repetition_ratio(scenes),
        "pivotal_scenes": sum(1 for s in scenes if s.get("is_pivotal")),
        "cliffhanger_scenes": sum(
            1 for s in scenes if (s.get("engagement_role") or "") == "cliffhanger"
        ),
    }

    retention, retention_factors = predict_youtube_retention(scenes, pacing, scene_metrics)
    total_cache = tracker.cache_hits + tracker.cache_misses
    hit_rate = round(tracker.cache_hits / total_cache, 3) if total_cache else 0.0
    avg_q = (
        round(sum(tracker.quality_scores) / len(tracker.quality_scores), 3)
        if tracker.quality_scores
        else None
    )

    visual_rep = scene_metrics["visual_repetition_ratio"]
    auto_visual_ok = visual_rep <= 0.55

    return {
        "topic": topic,
        "session_id": tracker.session_id or None,
        "research_source": tracker.research_source or settings.get("research_source"),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "target_duration_sec": target,
        "video_duration_sec": round(video_dur, 1),
        "generation_time_sec": tracker.elapsed_sec(),
        "tts_time_sec": round(tracker.tts_time_sec, 1),
        "media_time_sec": round(tracker.media_time_sec, 1),
        "render_time_sec": round(tracker.render_time_sec, 1),
        "script_source": settings.get("script_source"),
        "content_profile": settings.get("content_profile"),
        "resolved_content_profile": detect_content_profile(topic, settings),
        "pipeline_version": PIPELINE_VERSION,
        "output_file": output_path.name if output_path else None,
        "chaptered": tracker.chaptered,
        "cache_hits": tracker.cache_hits,
        "cache_misses": tracker.cache_misses,
        "cache_hit_rate": hit_rate,
        "image_cache_hits": tracker.image_cache_hits,
        "audio_cache_hits": tracker.audio_cache_hits,
        "images_generated": tracker.images_generated,
        "images_regenerated": tracker.images_regenerated,
        "audio_generated": tracker.audio_generated,
        "scene_count": len(scenes),
        "avg_quality_score": avg_q,
        "engagement_score": pacing.get("engagement_score"),
        "youtube_retention_prediction": retention,
        "retention_factors": retention_factors,
        "content_pacing": pacing,
        "scene_metrics": scene_metrics,
        "manual_review": {
            "arabic_issues": None,
            "image_issues": None,
            "audio_issues": None,
            "notes": "",
        },
        "ai_video_gate": {
            "watch_full_video": None,
            "visual_repetition_ok": auto_visual_ok,
            "no_arabic_audio_issues": None,
            "ready_for_ai_video": False,
            "max_ai_video_scenes": int(settings.get("max_ai_video_scenes") or 3),
        },
        "error": tracker.error,
    }


def save_production_report(report: dict[str, Any]) -> Path:
    OUTPUTS.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    _append_history(report)
    return REPORT_PATH


def _append_history(report: dict[str, Any]) -> None:
    history: list[dict[str, Any]] = []
    if HISTORY_PATH.exists():
        try:
            loaded = json.loads(HISTORY_PATH.read_text(encoding="utf-8"))
            if isinstance(loaded, list):
                history = loaded
        except json.JSONDecodeError:
            history = []
    summary = {
        "topic": report.get("topic"),
        "session_id": report.get("session_id"),
        "research_source": report.get("research_source"),
        "generated_at": report.get("generated_at"),
        "video_duration_sec": report.get("video_duration_sec"),
        "generation_time_sec": report.get("generation_time_sec"),
        "cache_hit_rate": report.get("cache_hit_rate"),
        "engagement_score": report.get("engagement_score"),
        "youtube_retention_prediction": report.get("youtube_retention_prediction"),
        "images_regenerated": report.get("images_regenerated"),
    }
    history.append(summary)
    HISTORY_PATH.write_text(json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8")


def finalize_production_run(
    topic: str,
    scenes: list[Scene],
    settings: dict[str, Any],
    output_path: Path | None,
    log: Callable[[str], None] | None = None,
) -> dict[str, Any] | None:
    tracker = active_tracker()
    if tracker is None:
        return None

    try:
        from production_session import get_current_session, stamp_session_fields

        session = get_current_session()
        if session and not tracker.research_source and session.research_source:
            tracker.research_source = session.research_source
        if session and not tracker.session_id:
            tracker.session_id = session.session_id
    except ImportError:
        pass

    report = build_production_report(topic, scenes, settings, output_path, tracker)
    try:
        from production_session import stamp_session_fields

        report = stamp_session_fields(report)
    except ImportError:
        pass
    path = save_production_report(report)

    if log:
        log(
            f"📋 تقرير الإنتاج: {path.name} | session={report.get('session_id', '—')} | "
            f"محرك={report.get('research_source', '—')} | "
            f"المدة {report['video_duration_sec']}s | "
            f"الوقت {report['generation_time_sec']}s "
            f"(TTS {report.get('tts_time_sec', 0)}s / وسائط {report.get('media_time_sec', 0)}s / ترميز {report.get('render_time_sec', 0)}s) | "
            f"Cache {int((report.get('cache_hit_rate') or 0) * 100)}% | "
            f"Engagement {report.get('engagement_score')} | "
            f"Retention ~{report.get('youtube_retention_prediction')}"
        )
    return report


def load_production_report() -> dict[str, Any] | None:
    if not REPORT_PATH.exists():
        return None
    try:
        data = json.loads(REPORT_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        return None
