from __future__ import annotations

import json
from typing import Any, Callable

from content_profiles import (
    ResolvedProfile,
    detect_content_profile,
    detect_content_profile_from_text,
    profile_label_ar,
)


def collect_script_text(settings: dict[str, Any] | None) -> str:
    settings = settings or {}
    parts: list[str] = []
    script = str(settings.get("custom_scenes_script") or "").strip()
    if script:
        parts.append(script)
    raw_scenes = settings.get("custom_scenes") or "[]"
    if isinstance(raw_scenes, str) and raw_scenes.strip() not in {"", "[]"}:
        parts.append(raw_scenes)
        try:
            parsed = json.loads(raw_scenes)
            if isinstance(parsed, list):
                for item in parsed:
                    if isinstance(item, dict):
                        for key in ("narration", "visual", "screen_text", "ai_prompt"):
                            value = item.get(key)
                            if value:
                                parts.append(str(value))
        except json.JSONDecodeError:
            pass
    return "\n".join(parts)


def topic_script_conflict(
    topic: str,
    settings: dict[str, Any] | None,
) -> tuple[bool, ResolvedProfile, ResolvedProfile | None]:
    settings = settings or {}
    topic_profile = detect_content_profile(topic, settings)
    script_text = collect_script_text(settings)
    if not script_text.strip():
        return False, topic_profile, None
    script_profile = detect_content_profile_from_text(script_text)
    if script_profile == "general":
        return False, topic_profile, script_profile
    if topic_profile != script_profile:
        return True, topic_profile, script_profile
    return False, topic_profile, script_profile


def log_pipeline_context(
    topic: str,
    settings: dict[str, Any] | None,
    log: Callable[[str], None] | None,
) -> None:
    if not log:
        return
    settings = settings or {}
    profile = detect_content_profile(topic, settings)
    script_source = settings.get("script_source", "auto")
    session_part = ""
    research_part = ""
    try:
        from production_session import get_current_session, research_engine_label

        session = get_current_session()
        if session:
            session_part = f" | session_id={session.session_id}"
            if session.research_source:
                research_part = f" | {research_engine_label(session.research_source)}"
    except ImportError:
        pass
    log(
        f"📌 topic={topic.strip()} | script_source={script_source} | "
        f"content_profile={profile} ({profile_label_ar(profile)})"
        f"{session_part}{research_part}"
    )
    conflict, topic_p, script_p = topic_script_conflict(topic, settings)
    if conflict and script_p:
        log(
            f"⚠️ تعارض محتمل: الموضوع → {profile_label_ar(topic_p)} | "
            f"السيناريو المحفوظ → {profile_label_ar(script_p)}"
        )
