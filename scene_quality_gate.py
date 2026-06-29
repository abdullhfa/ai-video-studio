from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from content_profiles import detect_content_profile, is_islamic_story_profile
from image_quality import evaluate_image_quality, passes_quality
from scene_relevance import score_scene_relevance
from video_pipeline import OUTPUTS, Scene

GATE_REPORT_PATH = OUTPUTS / "scene_quality_gate.json"

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}


def _islamic_strict(settings: dict[str, Any] | None, topic: str) -> bool:
    settings = settings or {}
    return is_islamic_story_profile(detect_content_profile(topic, settings))


def validate_scene_visual(
    path: Path,
    scene: Scene,
    topic: str,
    settings: dict[str, Any] | None = None,
) -> dict[str, Any]:
    settings = settings or {}
    strict = _islamic_strict(settings, topic)
    relevance = score_scene_relevance(scene, topic, settings)
    story_relevance = int(relevance.get("story_relevance") or relevance.get("relevance_score") or 0)

    report: dict[str, Any] = {
        "ok": True,
        "quality_gate": "passed",
        "path": path.name if path else None,
        "fail_reason": None,
        "story_relevance": story_relevance,
        "relevance_score": story_relevance,
        "visual_quality": None,
        "text_artifacts": None,
        "text_artifact_score": None,
        "matched_keywords": relevance.get("matched_keywords") or [],
        "aligned_concepts": relevance.get("aligned_concepts") or [],
        "missing_concepts": relevance.get("missing_concepts") or [],
        "generic_markers": relevance.get("generic_markers") or [],
        "narration_visual_alignment": relevance.get("narration_visual_alignment"),
    }

    if strict and not relevance["passes"]:
        report["ok"] = False
        report["quality_gate"] = "failed"
        report["fail_reason"] = "low_relevance"
        return report

    if path.suffix.lower() not in IMAGE_SUFFIXES:
        report["visual_quality"] = 100
        report["text_artifacts"] = 0.0
        report["skip_image_check"] = True
        return report

    scores = evaluate_image_quality(path)
    text_raw = float(scores.get("text_artifact_score") or 0)
    visual_q = round(float(scores.get("quality_score") or 0) * 100)
    report["visual_quality"] = visual_q
    report["text_artifacts"] = round(text_raw, 3)
    report["text_artifact_score"] = round(text_raw, 3)
    report["face_visibility_score"] = scores.get("face_visibility_score")
    report["quality_score"] = scores.get("quality_score")

    max_text = float(settings.get("image_max_text_score", 0.42) or 0.42)
    if strict:
        max_text = min(max_text, float(settings.get("islamic_max_text_score", 0.30) or 0.30))
    min_quality = float(settings.get("image_min_quality", 0.45) or 0.45)
    max_face = float(settings.get("image_max_face_score", 0.38) or 0.38)

    if not passes_quality(
        scores,
        min_quality=min_quality,
        max_face=max_face,
        max_text=max_text,
    ):
        report["ok"] = False
        report["quality_gate"] = "failed"
        if text_raw > max_text:
            report["fail_reason"] = "text_artifact"
        elif float(scores.get("face_visibility_score") or 0) > max_face:
            report["fail_reason"] = "face_visible"
        else:
            report["fail_reason"] = "low_quality"
    return report


def log_scene_quality_block(
    idx: int,
    report: dict[str, Any],
    settings: dict[str, Any] | None,
    log: Callable[[str], None] | None,
    *,
    include_model: bool = False,
) -> None:
    if not log:
        return
    settings = settings or {}
    if include_model:
        from image_providers import image_provider_label

        log(f"  🎨 Image Provider: {image_provider_label(settings)}")
    if report.get("ok"):
        gate = "passed"
    else:
        gate = f"failed ({report.get('fail_reason') or 'quality'})"
    log(f"  🛡️ Quality Gate: {gate} — مشهد {idx + 1}")
    log(f"  📊 Relevance Score: {report.get('story_relevance', '—')}")
    vq = report.get("visual_quality")
    log(f"  📊 Visual Quality: {vq if vq is not None else '—'}")
    ta = report.get("text_artifacts")
    if ta is None:
        log("  📊 Text Artifact Score: —")
    else:
        log(f"  📊 Text Artifact Score: {float(ta):.2f}")
    vq_out = vq if vq is not None else "null"
    sr_out = report.get("story_relevance", "null")
    ta_out = ta if ta is not None else "null"
    log(
        f'  📋 {{"visual_quality": {vq_out}, "story_relevance": {sr_out}, "text_artifacts": {ta_out}}}'
    )
    missing = report.get("missing_concepts") or []
    if missing:
        log(f"  ⚠️ مفاهيم مفقودة في الـ prompt: {', '.join(missing[:4])}")


def save_quality_gate_report(
    scenes: list[Scene],
    reports: list[dict[str, Any]],
    topic: str,
    settings: dict[str, Any] | None = None,
) -> Path:
    from image_providers import image_provider_info

    settings = settings or {}
    provider_info = image_provider_info(settings)
    payload = {
        "topic": topic,
        "image_provider": provider_info,
        "imagerouter_model": settings.get("imagerouter_model"),
        "scenes": [
            {
                "index": idx + 1,
                "screen_text": scenes[idx].get("screen_text") if idx < len(scenes) else "",
                "narration_preview": (scenes[idx].get("narration") or "")[:120] if idx < len(scenes) else "",
                **reports[idx],
            }
            for idx in range(len(reports))
        ],
        "passed": sum(1 for r in reports if r.get("ok")),
        "failed": sum(1 for r in reports if not r.get("ok")),
    }
    OUTPUTS.mkdir(parents=True, exist_ok=True)
    GATE_REPORT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return GATE_REPORT_PATH


def run_pre_compose_gate(
    scenes: list[Scene],
    visual_paths: list[Path],
    topic: str,
    settings: dict[str, Any] | None,
    log: Callable[[str], None] | None = None,
    *,
    log_each_scene: bool = False,
) -> list[dict[str, Any]]:
    reports: list[dict[str, Any]] = []
    for idx, (scene, path) in enumerate(zip(scenes, visual_paths)):
        report = validate_scene_visual(path, scene, topic, settings)
        reports.append(report)
        scene["quality_gate"] = report
        sr = report.get("story_relevance")
        scene["relevance_score"] = int(sr or 0)
        if log_each_scene:
            log_scene_quality_block(idx, report, settings, log)
    save_quality_gate_report(scenes, reports, topic, settings)
    if log:
        passed = sum(1 for r in reports if r.get("ok"))
        log(f"🛡️ بوابة الجودة: {passed}/{len(reports)} مشهد مقبول — scene_quality_gate.json")
    return reports
