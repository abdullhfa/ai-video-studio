#!/usr/bin/env python3
"""اختبار تبديل المواضيع السريع — يتحقق من عزل session_id بين الجلسات المتتالية.

الاستخدام:
    py -3.12 scripts/run_topic_switch_test.py

يفحص بعد كل موضوع أن research.json و scenes.json و story_reference.json
يحملان نفس session_id و topic و pipeline_version.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from production_session import (  # noqa: E402
    OUTPUTS,
    PIPELINE_VERSION,
    get_current_session,
    save_research_bundle,
    set_research_source,
    start_production_session,
)
from scene_cache import topic_slug  # noqa: E402
from story_reference import STORY_REFERENCE_PATH, save_story_reference  # noqa: E402
from topic_research import research_for_topic  # noqa: E402
from video_pipeline import _save_scenes  # noqa: E402

TOPICS = [
    "قصة أهل الكهف",
    "قصة سيدنا يوسف عليه السلام",
    "Flutter",
    "أصحاب الفيل",
]

TRACKED_FILES = (
    OUTPUTS / "research.json",
    OUTPUTS / "scenes.json",
    STORY_REFERENCE_PATH,
)


def _load_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        return None


def _extract_meta(path: Path, data: dict[str, Any]) -> dict[str, Any]:
    if path.name == "scenes.json":
        return {
            "session_id": data.get("session_id"),
            "topic": data.get("topic"),
            "content_profile": data.get("content_profile"),
            "pipeline_version": data.get("pipeline_version"),
            "scene_count": len(data.get("scenes") or []),
        }
    return {
        "session_id": data.get("session_id"),
        "topic": data.get("topic"),
        "content_profile": data.get("content_profile"),
        "pipeline_version": data.get("pipeline_version"),
        "scene_count": len(data.get("scenes") or []),
    }


def verify_session_files(expected_session_id: str, expected_topic: str) -> list[str]:
    errors: list[str] = []
    for path in TRACKED_FILES:
        data = _load_json(path)
        if not data:
            errors.append(f"❌ {path.name}: ملف مفقود أو غير صالح")
            continue
        meta = _extract_meta(path, data)
        if meta.get("session_id") != expected_session_id:
            errors.append(
                f"❌ {path.name}: session_id={meta.get('session_id')!r} "
                f"≠ المتوقع {expected_session_id!r}"
            )
        file_topic = str(meta.get("topic") or "")
        if not file_topic or topic_slug(file_topic) != topic_slug(expected_topic):
            errors.append(
                f"❌ {path.name}: topic={file_topic!r} لا يطابق {expected_topic!r}"
            )
        version = meta.get("pipeline_version")
        if version and version != PIPELINE_VERSION:
            errors.append(f"❌ {path.name}: pipeline_version={version!r} ≠ {PIPELINE_VERSION}")
        if version is None and path.name != "story_reference.json":
            errors.append(f"⚠️ {path.name}: بدون pipeline_version (ملف قديم؟)")
    return errors


def run_research_for_topic(topic: str, duration_sec: int = 180) -> str:
    settings = {
        "content_profile": "auto",
        "video_duration_sec": duration_sec,
        "media_source": "images",
        "include_quran": True,
        "visual_style": "cinematic_islamic",
    }
    session = start_production_session(topic, settings, force_new=True)
    scenes, meta = research_for_topic(topic, duration_sec, "ai", settings, log=print)
    source = str(meta.get("source") or "local")
    set_research_source(source)
    save_research_bundle(
        OUTPUTS / "research.json",
        topic,
        {**meta, "script_source": "auto"},
        scenes,
    )
    story_ref = meta.get("story_reference") or {"title": topic, "topic": topic}
    save_story_reference(story_ref)
    _save_scenes(scenes, topic=topic, settings=settings)
    return session.session_id


def main() -> int:
    print(f"🔬 اختبار تبديل المواضيع | pipeline_version={PIPELINE_VERSION}\n")
    all_errors: list[str] = []
    session_log: list[tuple[str, str]] = []

    for idx, topic in enumerate(TOPICS, 1):
        print(f"\n{'=' * 60}")
        print(f"[{idx}/{len(TOPICS)}] موضوع: {topic}")
        print(f"{'=' * 60}")
        try:
            session_id = run_research_for_topic(topic)
            session_log.append((topic, session_id))
            current = get_current_session()
            print(f"✅ session_id={session_id} | slug={topic_slug(topic)}")
            if current and current.session_id != session_id:
                all_errors.append(f"❌ global state: current_session ≠ {session_id}")
            errors = verify_session_files(session_id, topic)
            if errors:
                all_errors.extend(errors)
                for err in errors:
                    print(err)
            else:
                print("✅ جميع الملفات متسقة")
                for path in TRACKED_FILES:
                    meta = _extract_meta(path, _load_json(path) or {})
                    print(
                        f"   • {path.name}: session={meta.get('session_id')} "
                        f"scenes={meta.get('scene_count', '—')} "
                        f"pv={meta.get('pipeline_version', '—')}"
                    )
        except Exception as exc:
            all_errors.append(f"❌ فشل البحث لـ «{topic}»: {exc}")
            print(all_errors[-1])

    print(f"\n{'=' * 60}")
    print("ملخص الجلسات:")
    for topic, sid in session_log:
        print(f"  • {topic[:40]:40} → {sid}")

    unique_sessions = {sid for _, sid in session_log}
    if len(unique_sessions) != len(session_log):
        all_errors.append("❌ تكرار session_id بين مواضيع مختلفة")

    print(f"\n{'=' * 60}")
    if all_errors:
        print(f"❌ فشل الاختبار — {len(all_errors)} مشكلة:")
        for err in all_errors:
            print(f"  {err}")
        return 1

    print("✅ نجح اختبار تبديل المواضيع — كل ملف يحمل session_id الموضوع الحالي فقط")
    print("\n📌 ملاحظة التوليد المتوازي:")
    print("   النظام يستخدم _generation_lock — إنتاج واحد في كل مرة (HTTP 409).")
    print("   مجلدات outputs/sessions/ غير مطلوبة حتى تفعيل الإنتاج المتوازي.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
