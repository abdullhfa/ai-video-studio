from __future__ import annotations

import re
from typing import Any

from content_profiles import ResolvedProfile, is_islamic_story_profile
from story_reference import ISLAMIC_DEPICTION_RULES, load_story_reference_for_topic
from visual_styles import append_visual_style
from video_pipeline import Scene

ISLAMIC_AI_SUFFIX = ISLAMIC_DEPICTION_RULES

LANDSCAPE_TOKENS = (
    "desert",
    "mountain",
    "sky",
    "sea",
    "ocean",
    "sunset",
    "sand",
    "waves",
    "clouds",
    "landscape",
    "صحراء",
    "جبل",
    "سماء",
    "بحر",
    "غروب",
    "أمواج",
    "سماء",
)

HISTORICAL_TOKENS = (
    "cave",
    "palace",
    "pharaoh",
    "prophet",
    "battle",
    "city",
    "sleeping",
    "believers",
    "travelers",
    "ancient town",
    "كهف",
    "فرعون",
    "نبي",
    "غزوة",
    "مدينة",
    "قصر",
    "نوم",
    "فتية",
    "أصحاب",
)

MAP_TOKENS = ("map", "location", "mosque", "mecca", "medina", "خريطة", "موقع", "مكة", "المدينة")


def _haystack(scene: Scene, topic: str) -> str:
    parts = [
        scene.get("visual", ""),
        scene.get("narration", ""),
        scene.get("screen_text", ""),
        scene.get("search_query", ""),
        scene.get("ai_prompt", ""),
        topic,
    ]
    return " ".join(parts).lower()


def infer_scene_kind(scene: Scene, topic: str, profile: ResolvedProfile) -> str:
    explicit = (scene.get("scene_kind") or "").strip().lower()
    if explicit in {"landscape", "historical_event", "map_site", "closing_lesson"}:
        return explicit

    role = (scene.get("engagement_role") or "").strip().lower()
    if role == "hook" and is_islamic_story_profile(profile):
        text = _haystack(scene, topic)
        if any(token in text for token in HISTORICAL_TOKENS) or (scene.get("visual_requirements")):
            return "historical_event"
        return "landscape"
    if role in {"narrative", "cliffhanger"} and is_islamic_story_profile(profile):
        return "historical_event"

    text = _haystack(scene, topic)
    if any(token in text for token in MAP_TOKENS):
        return "map_site"
    if any(token in text for token in LANDSCAPE_TOKENS) and not any(token in text for token in HISTORICAL_TOKENS):
        return "landscape"
    if profile == "islamic_story" or any(token in text for token in HISTORICAL_TOKENS):
        return "historical_event"
    if any(word in text for word in ("lesson", "moral", "عبرة", "ختام", "خلاصة")):
        return "closing_lesson"
    return "historical_event" if is_islamic_story_profile(profile) else "general"


def build_islamic_ai_prompt(scene: Scene, topic: str, settings: dict | None = None) -> str:
    settings = settings or {}
    reqs = scene.get("visual_requirements") or []
    if isinstance(reqs, list) and reqs:
        from story_db import build_scene_prompt_from_db

        prompt = build_scene_prompt_from_db({"visual_requirements": reqs, "event": scene.get("event")})
        if prompt:
            return append_visual_style(prompt, settings.get("visual_style"))
    existing = (scene.get("ai_prompt") or "").strip()
    if existing:
        prompt = existing
        if ISLAMIC_AI_SUFFIX.lower() not in existing.lower():
            prompt = f"{existing}, {ISLAMIC_AI_SUFFIX}"
    else:
        visual = (scene.get("visual") or "").strip()
        screen = (scene.get("screen_text") or "").strip()
        base = visual or screen or topic
        prompt = f"{base}, {ISLAMIC_AI_SUFFIX}"
    return append_visual_style(prompt, settings.get("visual_style"))


def _landscape_broll_query(scene: Scene, topic: str) -> str:
    """Safe stock search for Islamic B-roll (no faces, generic geography)."""
    visual = re.sub(r"[^\w\s\u0600-\u06FF]", " ", (scene.get("visual") or "")).strip()
    if visual and len(visual) > 12:
        return f"{visual}, cinematic, no people, wide shot"[:100]
    return (
        "ancient middle east desert cinematic sunset golden hour aerial landscape "
        "no people no faces"
    )[:100]


def _route_islamic_scene(
    scene: Scene,
    topic: str,
    settings: dict[str, Any],
    *,
    scene_kind: str,
    presentation: str,
) -> dict[str, Any]:
    """Scene policy for islamic_story: B-roll video, FLUX for events, slides for Quran/maps."""
    routed: dict[str, Any] = dict(scene)
    routed["router_locked"] = True
    routed["scene_kind"] = scene_kind

    if presentation == "quran_text" or routed.get("quran_verse"):
        if presentation == "quran_text" or str(routed.get("media_source") or "") == "quran_slide":
            routed["presentation"] = "quran_text"
            routed["media_source"] = "quran_slide"
            return routed

    if scene_kind == "closing_lesson":
        if routed.get("quran_verse") and presentation in {"", "quran_text", "ken_burns_zoom"}:
            routed["presentation"] = "quran_text"
            routed["media_source"] = "quran_slide"
        else:
            routed["media_type"] = "ai"
            routed["media_source"] = "ai_image"
            routed["presentation"] = "ken_burns_zoom"
            routed["ai_prompt"] = build_islamic_ai_prompt(routed, topic, settings)  # type: ignore[arg-type]
        return routed

    if presentation == "map_slide" or scene_kind == "map_site":
        routed["presentation"] = "map_slide"
        routed["media_source"] = "map_slide"
        if routed.get("image_url"):
            routed["media_type"] = "ai"
            routed["media_source"] = "web_image"
        else:
            routed["media_type"] = "ai"
            routed["media_source"] = "ai_image"
            routed["ai_prompt"] = build_islamic_ai_prompt(
                scene, f"historical map style scene about {topic}", settings
            )
        return routed

    if scene_kind == "landscape":
        routed["media_type"] = "pexels"
        routed["media_source"] = "pexels_video"
        routed["search_query"] = _landscape_broll_query(scene, topic)
        routed["presentation"] = presentation if presentation not in {"", "static"} else "static"
        return routed

    role = str(routed.get("engagement_role") or "").strip().lower()
    if role == "hook" and scene_kind != "historical_event":
        routed["scene_kind"] = "landscape"
        routed["media_type"] = "pexels"
        routed["media_source"] = "pexels_video"
        routed["search_query"] = _landscape_broll_query(scene, topic)
        routed["presentation"] = "static"
        return routed

    # historical_event — FLUX with Islamic depiction rules
    routed["media_type"] = "ai"
    routed["media_source"] = "ai_image"
    routed["scene_kind"] = "historical_event"
    if presentation in {"", "static"}:
        routed["presentation"] = "ken_burns_zoom"
    routed["ai_prompt"] = build_islamic_ai_prompt(routed, topic, settings)  # type: ignore[arg-type]
    return routed


def route_scene_media(scene: Scene, topic: str, profile: ResolvedProfile, settings: dict | None = None) -> Scene:
    settings = settings or {}
    routed: dict[str, Any] = dict(scene)
    routed["content_profile"] = profile
    scene_kind = infer_scene_kind(scene, topic, profile)
    presentation = str(routed.get("presentation") or "").strip().lower()

    local_file = str(routed.get("local_file") or "").strip()
    if local_file:
        routed["router_locked"] = True
        return routed  # type: ignore[return-value]

    if is_islamic_story_profile(profile):
        routed.update(_route_islamic_scene(scene, topic, settings, scene_kind=scene_kind, presentation=presentation))
        return routed  # type: ignore[return-value]

    # educational / general — keep existing media_type, only enrich prompts
    media_type = str(routed.get("media_type") or "ai").lower()
    if media_type in {"screen", "slide"} and not local_file:
        routed["media_type"] = "pexels"
        routed["media_source"] = "pexels_video"
    elif media_type == "ai":
        routed["media_source"] = "ai_image"
    elif media_type == "pexels":
        routed["media_source"] = "pexels_video"
    else:
        routed["media_source"] = media_type
    return routed  # type: ignore[return-value]


def _landscape_query(scene: Scene, topic: str) -> str:
    visual = re.sub(r"[^\w\s]", " ", (scene.get("visual") or "")).strip()
    if visual:
        return visual[:100]
    return f"desert mountains sunset cinematic landscape {topic}"[:100]


def route_scenes_media(scenes: list[Scene], topic: str, settings: dict[str, Any]) -> list[Scene]:
    from content_profiles import detect_content_profile
    from islamic_scholar import apply_historical_accuracy
    from quran_verses import inject_islamic_citations

    profile = detect_content_profile(topic, settings)
    story_ref = load_story_reference_for_topic(topic) if is_islamic_story_profile(profile) else None

    from story_db import apply_story_db_to_scenes

    scenes = apply_story_db_to_scenes(scenes, topic)

    prepared: list[Scene] = []
    for idx, scene in enumerate(scenes):
        item = dict(scene)
        if story_ref is not None:
            from story_engagement import fix_stale_islamic_visual

            item = fix_stale_islamic_visual(item, topic, story_ref, idx)  # type: ignore[assignment]
        prepared.append(item)  # type: ignore[arg-type]

    routed = list(prepared)

    if is_islamic_story_profile(profile):
        include_quran = _setting_bool(settings, "include_quran", True)
        include_hadith = _setting_bool(settings, "include_hadith", False)
        historical_accuracy = _setting_bool(settings, "historical_accuracy", True)

        if not include_quran:
            routed = _strip_quran_presentations(routed)

        routed = inject_islamic_citations(
            routed,
            topic,
            include_quran=include_quran,
            include_hadith=include_hadith,
        )
        story_ref = load_story_reference_for_topic(topic)
        routed = apply_historical_accuracy(
            routed,
            story_ref,
            enabled=historical_accuracy,
        )

        duration = int(settings.get("video_duration_sec") or 300)
        routed = apply_islamic_scene_variety(routed, duration, topic, include_quran=include_quran)

        from character_memory import apply_character_memory, mark_pivotal_scenes
        from scene_confidence import annotate_scenes_confidence
        from scene_timing import pre_estimate_scene_durations
        from visual_variation import assign_visual_variation_seeds

        routed = apply_character_memory(routed, topic)
        routed = assign_visual_variation_seeds(routed)
        max_pivotal = int(settings.get("max_ai_video_scenes", 3) or 3)
        routed = mark_pivotal_scenes(routed, topic, max_pivotal=max(1, min(6, max_pivotal)))
        routed = apply_islamic_cinematic_broll(routed, topic, duration)
        routed = annotate_scenes_confidence(routed)
        routed = pre_estimate_scene_durations(routed, settings)

        from story_engagement import apply_story_engagement, fix_stale_islamic_visual

        story_ref = load_story_reference_for_topic(topic)
        routed = apply_story_engagement(routed, topic, settings, story_ref)
        rerouted: list[Scene] = []
        for idx, scene in enumerate(routed):
            fixed = fix_stale_islamic_visual(scene, topic, story_ref, idx)
            rerouted.append(route_scene_media(fixed, topic, profile, settings))
        routed = rerouted
    return routed


def _setting_bool(settings: dict[str, Any], key: str, default: bool) -> bool:
    value = settings.get(key, default)
    if isinstance(value, bool):
        return value
    return str(value).lower() in {"1", "true", "yes", "on"}


def _strip_quran_presentations(scenes: list[Scene]) -> list[Scene]:
    cleaned: list[Scene] = []
    for scene in scenes:
        item = dict(scene)
        if str(item.get("presentation") or "").lower() == "quran_text":
            item["presentation"] = "ken_burns_zoom"
            item["media_source"] = "ai_image"
            item["media_type"] = "ai"
        item.pop("quran_verse", None)
        item.pop("quran_reference", None)
        cleaned.append(item)  # type: ignore[arg-type]
    return cleaned


PRESENTATION_CYCLE = (
    "static",
    "ken_burns_zoom",
    "static",
    "ken_burns_pan",
    "map_slide",
    "quran_text",
    "ken_burns_zoom",
    "static",
)


def apply_islamic_cinematic_broll(
    scenes: list[Scene],
    topic: str,
    duration_sec: int,
) -> list[Scene]:
    """Insert B-roll video slots (~20%) between story beats for cinematic pacing."""
    if len(scenes) < 3:
        return scenes

    target_broll = max(1, len(scenes) // 5)
    candidates: list[tuple[int, int]] = []
    for idx, scene in enumerate(scenes):
        if idx == 0 or idx >= len(scenes) - 1:
            continue
        item = dict(scene)
        kind = str(item.get("scene_kind") or "").lower()
        if kind in {"map_site", "closing_lesson"}:
            continue
        if item.get("quran_verse") or str(item.get("presentation") or "").lower() == "quran_text":
            continue
        if str(item.get("media_source") or "") in {"map_slide", "quran_slide"}:
            continue
        score = 0 if item.get("is_pivotal") else 2
        text = _haystack(item, topic)  # type: ignore[arg-type]
        if any(token in text for token in LANDSCAPE_TOKENS):
            score += 3
        if score > 0:
            candidates.append((score, idx))

    candidates.sort(reverse=True)
    broll_indices = {idx for _, idx in candidates[:target_broll]}
    if not broll_indices and len(scenes) >= 4:
        for mid in (len(scenes) // 2, len(scenes) // 2 + 1, 1):
            if mid <= 0 or mid >= len(scenes) - 1:
                continue
            probe = dict(scenes[mid])
            if probe.get("quran_verse") or str(probe.get("presentation") or "").lower() == "quran_text":
                continue
            if str(probe.get("media_source") or "") in {"map_slide", "quran_slide"}:
                continue
            broll_indices.add(mid)
            break

    mixed: list[Scene] = []
    for idx, scene in enumerate(scenes):
        item = dict(scene)
        if idx not in broll_indices:
            mixed.append(item)  # type: ignore[arg-type]
            continue
        item["scene_kind"] = "landscape"
        item["media_type"] = "pexels"
        item["media_source"] = "pexels_video"
        item["search_query"] = _landscape_broll_query(item, topic)  # type: ignore[arg-type]
        item["presentation"] = "static"
        mixed.append(item)  # type: ignore[arg-type]
    return mixed


def apply_islamic_scene_variety(
    scenes: list[Scene],
    duration_sec: int,
    topic: str = "",
    *,
    include_quran: bool = True,
) -> list[Scene]:
    """Mix presentations so long Islamic videos are not all static AI images."""
    if len(scenes) < 2:
        return scenes

    varied: list[Scene] = []
    quran_slot = max(2, len(scenes) // 4)
    map_slot = max(3, len(scenes) // 3)
    quran_assigned = False
    map_assigned = False

    for idx, scene in enumerate(scenes):
        item = dict(scene)
        kind = str(item.get("scene_kind") or "").lower()
        existing_presentation = str(item.get("presentation") or "").strip().lower()

        if existing_presentation in {"quran_text", "map_slide"}:
            varied.append(item)  # type: ignore[arg-type]
            continue
        if item.get("media_source") == "pexels_video" and kind == "landscape":
            varied.append(item)  # type: ignore[arg-type]
            continue

        if kind == "map_site":
            item["presentation"] = "map_slide"
            item["media_source"] = "map_slide"
        elif kind == "landscape":
            item["presentation"] = "static"
            item["media_type"] = "pexels"
            item["media_source"] = "pexels_video"
            item["search_query"] = _landscape_broll_query(item, topic)  # type: ignore[arg-type]
        elif kind == "closing_lesson":
            if item.get("quran_verse"):
                item["presentation"] = "quran_text"
                item["media_source"] = "quran_slide"
            else:
                item["presentation"] = "ken_burns_zoom"
                item["media_type"] = "ai"
                item["media_source"] = "ai_image"
        elif not quran_assigned and include_quran and item.get("quran_verse"):
            item["presentation"] = "quran_text"
            item["media_source"] = "quran_slide"
            quran_assigned = True
        elif not quran_assigned and idx == len(scenes) - 1 and include_quran and kind == "closing_lesson":
            item["presentation"] = "quran_text"
            item["media_source"] = "quran_slide"
            quran_assigned = True
            if not item.get("quran_verse"):
                from quran_verses import find_verse_for_scene

                match = find_verse_for_scene(item, topic)  # type: ignore[arg-type]
                if match:
                    item["quran_verse"] = match.get("verse", "")
                    item["quran_reference"] = match.get("reference", "")
                else:
                    item["quran_verse"] = _extract_verse_hint(item)
        elif not map_assigned and idx == map_slot and kind != "closing_lesson":
            item["presentation"] = "map_slide"
            item["media_source"] = "map_slide"
            map_assigned = True
        else:
            item["presentation"] = PRESENTATION_CYCLE[idx % len(PRESENTATION_CYCLE)]
            if item["presentation"] in {"ken_burns_zoom", "ken_burns_pan"}:
                item["media_type"] = "ai"
                item["media_source"] = "ai_image"
        varied.append(item)  # type: ignore[arg-type]
    return varied


def _extract_verse_hint(scene: dict[str, Any]) -> str:
    narration = str(scene.get("narration") or "").strip()
    if len(narration) > 220:
        return narration[:220] + "..."
    return narration
