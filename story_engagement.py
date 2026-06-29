from __future__ import annotations

import re
from typing import Any

from story_reference import _topic_key, build_local_story_reference
from video_pipeline import Scene, _normalize_scene

# معدل الكلام المستهدف (كلمة/دقيقة) حسب نمط الراوي
NARRATOR_WPM_TARGETS: dict[str, tuple[int, int]] = {
    "هادئ": (100, 120),
    "quiet": (100, 120),
    "calm": (100, 120),
    "وثائقي": (120, 150),
    "documentary": (120, 150),
    "storyteller": (115, 145),
    "مؤثر": (110, 140),
    "dramatic": (110, 140),
    "حماسي": (130, 165),
}

QURAN_RATIO_IDEAL = (0.10, 0.25)

STORY_HOOKS: dict[str, str] = {
    "اهل الكهف": (
        "مجموعة من الشباب المؤمنين تركت مدينتها وهربت إلى كهف مجهول في الجبل، "
        "ثم ناموا فيه ليستيقظوا بعد مئات السنين على عالم لم يعودوا يعرفونه. "
        "هذه قصة أهل الكهف كما وردت في القرآن الكريم."
    ),
    "يوسف": (
        "فتى صغير رأى رؤيا عظيمة، فغار منه إخوته وألقوه في بئر عميقة، "
        "لتبدأ رحلة من الصبر والابتلاء حتى يصبح عزيز مصر. "
        "هذه قصة سيدنا يوسف عليه السلام."
    ),
    "اصحاب الاخدود": (
        "ملك ظالم حفر أخدوداً وأوقد فيه ناراً ليحرق المؤمنين، "
        "لكن فتىً مؤمناً واحداً صمد على إيمانه حتى أصبحت قصته آية خالدة. "
        "هذه قصة أصحاب الأخدود."
    ),
    "اصحاب الفيل": (
        "جيش ضخم بقيادة أبرهة زحف نحو الكعبة ومعه فيل الحرب، "
        "ظناً أنه يستطيع هدم بيت الله الحرام، "
        "فأرسل الله عليهم طيراً أبابيل تحمل حجارة من سجيل. "
        "هذه قصة أصحاب الفيل."
    ),
}

CLIFFHANGER_PHRASES = (
    "لكن ما الذي حدث بعد ذلك؟",
    "وفي اللحظة التالية... تتغير كل الأمور.",
    "ولا يعلم أحد ما ينتظرهم في المشهد القادم.",
    "ثم جاءت لحظة لم يتوقعها أحد.",
    "وما حدث بعدها كان أعظم مما يتصوره البشر.",
)

LESSON_INTROS = (
    "والعبرة من هذه القصة أن",
    "ومما نتعلمه من هذا المشهد أن",
    "والدرس الأعظم هنا هو أن",
)


def engagement_enabled(settings: dict[str, Any] | None) -> bool:
    settings = settings or {}
    return any(
        _flag(settings, key, True)
        for key in ("hook_scene", "cliffhanger", "lesson_summary")
    )


def _flag(settings: dict[str, Any], key: str, default: bool) -> bool:
    value = settings.get(key, default)
    if isinstance(value, bool):
        return value
    return str(value).lower() in {"1", "true", "yes", "on"}


def _narrator_wpm_range(settings: dict[str, Any]) -> tuple[int, int]:
    style = str(settings.get("narrator_style") or "وثائقي").strip().lower()
    for key, bounds in NARRATOR_WPM_TARGETS.items():
        if key in style or style in key:
            return bounds
    return NARRATOR_WPM_TARGETS["وثائقي"]


def _word_count(text: str) -> int:
    return len([w for w in re.split(r"\s+", (text or "").strip()) if w])


def _event_narration(event: str, topic: str) -> str:
    event = event.strip()
    if not event:
        return f"نتابع أحداث {topic} كما وردت في المصادر الشرعية."
    return f"في هذا المشهد: {event}، كما ورد في سرد القصة الإسلامية بأسلوب محترم دون اجتهاد."


def _event_visual(event: str, topic: str) -> str:
    base = re.sub(r"[^\w\s\u0600-\u06FF]", " ", event).strip() or topic
    return (
        f"cinematic Islamic historical scene about {base}, ancient Arabian atmosphere, "
        "silhouettes and back view only, no clear faces, golden lighting"
    )


def _hook_visual(topic: str, story_ref: dict[str, Any] | None = None) -> str:
    story_ref = story_ref or build_local_story_reference(topic)
    events = story_ref.get("key_events") or []
    if events:
        event = events[0]
        return (
            f"cinematic Islamic historical scene about {event}, ancient desert atmosphere, "
            "silhouettes and back view only, no clear faces, golden dramatic lighting, no text"
        )
    return (
        "cinematic Islamic desert night sky with stars, ancient atmosphere, silhouettes at distance, "
        "no clear faces, golden lighting, no text"
    )


def _visual_blob(scene: dict[str, Any]) -> str:
    return " ".join(
        [
            str(scene.get("visual") or ""),
            str(scene.get("ai_prompt") or ""),
            str(scene.get("search_query") or ""),
        ]
    ).lower()


def script_has_stale_visuals(topic: str, settings: dict[str, Any] | None = None) -> bool:
    from scene_relevance import GENERIC_VISUAL_MARKERS
    from topic_consistency import collect_script_text

    blob = collect_script_text(settings).lower()
    return any(marker in blob for marker in GENERIC_VISUAL_MARKERS)


def fix_stale_islamic_visual(
    scene: Scene,
    topic: str,
    story_ref: dict[str, Any],
    idx: int,
    log: Any | None = None,
) -> Scene:
    from scene_relevance import GENERIC_VISUAL_MARKERS

    item: dict[str, Any] = dict(scene)
    blob = _visual_blob(item)
    if not any(marker in blob for marker in GENERIC_VISUAL_MARKERS):
        return item  # type: ignore[return-value]

    events = list(story_ref.get("key_events") or [])
    role = str(item.get("engagement_role") or "").strip()
    if idx == 0 or role == "hook":
        new_visual = _hook_visual(topic, story_ref)
        label = "hook"
    elif events:
        event = events[min(idx, len(events) - 1)]
        new_visual = _event_visual(str(event), topic)
        label = str(event)[:40]
    else:
        new_visual = _hook_visual(topic, story_ref)
        label = "fallback"

    item["visual"] = new_visual
    item.pop("ai_prompt", None)
    item.pop("search_query", None)
    item["scene_kind"] = "historical_event"
    item["media_type"] = "ai"
    item["media_source"] = "ai_image"
    if log:
        log(f"  🔧 مشهد {idx + 1}: استبدال وصف بصري قديم → {label}")
    return item  # type: ignore[return-value]


def build_islamic_scenes_from_reference(
    topic: str,
    segments: int,
    per_scene: int,
    story_ref: dict[str, Any] | None = None,
) -> list[Scene]:
    """Local fallback: scenes from key_events instead of generic 'Step 1' text."""
    story_ref = story_ref or build_local_story_reference(topic)
    key = _topic_key(topic)
    events = list(story_ref.get("key_events") or [])
    lessons = list(story_ref.get("key_lessons") or [])
    hook = STORY_HOOKS.get(key, f"قصة {topic} من القصص العظيمة في الإسلام، نرويها بأسلوب مشوق ومحترم.")

    raw_scenes: list[dict[str, Any]] = [
        {
            "narration": hook,
            "visual": _hook_visual(topic, story_ref),
            "screen_text": "البداية",
            "duration_sec": per_scene,
            "media_type": "ai",
            "scene_kind": "historical_event",
            "presentation": "ken_burns_zoom",
            "voice_style": "مؤثر",
            "engagement_role": "hook",
        }
    ]

    body_slots = max(1, segments - 2)
    for idx in range(body_slots):
        event = events[idx % len(events)] if events else f"حدث {idx + 1} من {topic}"
        raw_scenes.append(
            {
                "narration": _event_narration(event, topic),
                "visual": _event_visual(event, topic),
                "screen_text": event[:40] or f"مشهد {idx + 1}",
                "duration_sec": per_scene,
                "media_type": "ai",
                "scene_kind": "historical_event",
                "voice_style": "وثائقي",
                "engagement_role": "narrative",
            }
        )

    lesson_text = "، و".join(lessons) if lessons else "الثبات على الحق والصبر على البلاء"
    closing = (
        f"{LESSON_INTROS[0]} {lesson_text}. "
        f"هكذا تختتم قصة {story_ref.get('title', topic)}، تاركةً في القلب عبرةً لا تُنسى."
    )
    raw_scenes.append(
        {
            "narration": closing,
            "visual": "peaceful golden desert sunset, spiritual closing atmosphere, silhouettes at distance",
            "screen_text": "العبرة",
            "duration_sec": per_scene,
            "media_type": "ai",
            "scene_kind": "closing_lesson",
            "presentation": "static",
            "voice_style": "هادئ",
            "engagement_role": "lesson",
        }
    )

    while len(raw_scenes) < segments:
        i = len(raw_scenes)
        event = events[i % len(events)] if events else topic
        raw_scenes.insert(
            max(1, len(raw_scenes) - 1),
            {
                "narration": _event_narration(str(event), topic),
                "visual": _event_visual(str(event), topic),
                "screen_text": str(event)[:40],
                "duration_sec": per_scene,
                "media_type": "ai",
                "scene_kind": "historical_event",
                "engagement_role": "narrative",
            },
        )

    scenes = [_normalize_scene(item, "ai") for item in raw_scenes[:segments]]
    return scenes


def apply_story_engagement(
    scenes: list[Scene],
    topic: str,
    settings: dict[str, Any] | None = None,
    story_ref: dict[str, Any] | None = None,
) -> list[Scene]:
    settings = settings or {}
    if not scenes:
        return scenes

    use_hook = _flag(settings, "hook_scene", True)
    use_cliff = _flag(settings, "cliffhanger", True)
    use_lesson = _flag(settings, "lesson_summary", True)
    key = _topic_key(topic)
    hook_text = STORY_HOOKS.get(key, "")

    enriched: list[Scene] = []
    last_idx = len(scenes) - 1

    for idx, scene in enumerate(scenes):
        item = dict(scene)
        role = str(item.get("engagement_role") or "").strip()

        if idx == 0 and use_hook and hook_text:
            if role != "hook" and "تركت مدينتها" not in str(item.get("narration") or ""):
                item["narration"] = hook_text
            item["engagement_role"] = "hook"
            item["voice_style"] = item.get("voice_style") or "مؤثر"
            item["presentation"] = item.get("presentation") or "ken_burns_zoom"
            story_ref = story_ref or build_local_story_reference(topic)
            item["visual"] = _hook_visual(topic, story_ref)
            item.pop("ai_prompt", None)
            item["scene_kind"] = "historical_event"
            item["media_type"] = "ai"
            item["media_source"] = "ai_image"

        elif idx == last_idx and use_lesson:
            lessons = (story_ref or {}).get("key_lessons") or build_local_story_reference(topic).get("key_lessons", [])
            if role != "lesson" and lessons:
                lesson_text = "، و".join(str(x) for x in lessons[:2])
                item["narration"] = (
                    f"{LESSON_INTROS[idx % len(LESSON_INTROS)]} {lesson_text}. "
                    f"وهكذا نختتم قصة {(story_ref or {}).get('title', topic)}."
                )
            item["engagement_role"] = "lesson"
            item["scene_kind"] = "closing_lesson"
            item["voice_style"] = item.get("voice_style") or "هادئ"

        elif use_cliff and 0 < idx < last_idx and idx % 3 == 0:
            phrase = CLIFFHANGER_PHRASES[idx % len(CLIFFHANGER_PHRASES)]
            narration = str(item.get("narration") or "").strip()
            if phrase not in narration:
                item["narration"] = f"{narration} {phrase}".strip()
            item["engagement_role"] = "cliffhanger"

        enriched.append(item)  # type: ignore[arg-type]
    return enriched


def analyze_content_pacing(
    scenes: list[Scene],
    settings: dict[str, Any] | None = None,
    duration_sec: int | None = None,
) -> dict[str, Any]:
    settings = settings or {}
    total_words = sum(_word_count(s.get("narration") or "") for s in scenes)
    estimated_sec = duration_sec or sum(float(s.get("duration_sec") or 0) for s in scenes)
    minutes = max(estimated_sec / 60.0, 0.5)
    wpm = round(total_words / minutes, 1)

    quran_scenes = sum(
        1
        for s in scenes
        if s.get("quran_verse") or (s.get("presentation") or "").lower() == "quran_text"
    )
    quran_ratio = round(quran_scenes / max(len(scenes), 1), 3)

    wpm_min, wpm_max = _narrator_wpm_range(settings)
    warnings: list[str] = []
    suggestions: list[str] = []

    if wpm > wpm_max + 20:
        warnings.append(f"معدل الكلام مرتفع ({wpm} كلمة/دقيقة) — قد يتعب المشاهد. المستهدف: {wpm_min}-{wpm_max}")
        suggestions.append("خفّض tts_speed إلى 0.85–0.92 أو اختصر النصوص.")
    elif wpm < wpm_min - 15:
        warnings.append(f"معدل الكلام بطيء ({wpm} كلمة/دقيقة). المستهدف: {wpm_min}-{wpm_max}")
        suggestions.append("أضف تفاصيل سردية أو قلل مدة المشاهد الفارغة.")

    if quran_ratio > QURAN_RATIO_IDEAL[1]:
        warnings.append(
            f"نسبة الآيات مرتفعة ({int(quran_ratio * 100)}%) — المثالي 10–25% في فيديو 10 دقائق."
        )
        suggestions.append("اجعل معظم المشاهد سرداً قصصياً واحتفظ بآية أو آيتين للمشاهد المحورية.")
    elif quran_ratio < QURAN_RATIO_IDEAL[0] and settings.get("include_quran", True):
        suggestions.append("يمكن إضافة آية واحدة في مشهد محوري لتقوية الأثر الروحي.")

    hook_ok = any((s.get("engagement_role") or "") == "hook" for s in scenes)
    lesson_ok = any((s.get("engagement_role") or "") == "lesson" for s in scenes)
    if not hook_ok:
        suggestions.append("فعّل hook_scene لمقدمة تشويقية في أول 30 ثانية.")
    if not lesson_ok:
        suggestions.append("فعّل lesson_summary لختام يوضح العبرة.")

    return {
        "total_words": total_words,
        "estimated_minutes": round(minutes, 2),
        "words_per_minute": wpm,
        "wpm_target_range": [wpm_min, wpm_max],
        "quran_scene_count": quran_scenes,
        "quran_ratio": quran_ratio,
        "quran_ratio_ideal": list(QURAN_RATIO_IDEAL),
        "has_hook": hook_ok,
        "has_lesson_close": lesson_ok,
        "warnings": warnings,
        "suggestions": suggestions,
        "engagement_score": _engagement_score(wpm, wpm_min, wpm_max, quran_ratio, hook_ok, lesson_ok),
    }


def _engagement_score(
    wpm: float,
    wpm_min: int,
    wpm_max: int,
    quran_ratio: float,
    hook_ok: bool,
    lesson_ok: bool,
) -> int:
    score = 70
    if wpm_min <= wpm <= wpm_max:
        score += 10
    elif wpm > wpm_max + 30 or wpm < wpm_min - 20:
        score -= 15
    if QURAN_RATIO_IDEAL[0] <= quran_ratio <= QURAN_RATIO_IDEAL[1]:
        score += 10
    elif quran_ratio > 0.4:
        score -= 10
    if hook_ok:
        score += 5
    if lesson_ok:
        score += 5
    return max(0, min(100, score))
