from __future__ import annotations

import json
import re
from typing import Any, Callable

import google.generativeai as genai

from story_reference import ISLAMIC_DEPICTION_RULES, build_local_story_reference, normalize_story_reference, save_story_reference
from video_duration import clamp_duration, segment_count
from video_pipeline import GEMINI_MODELS, Scene, _configure_gemini, _is_quota_error, _normalize_scene


def _log_call(log: Callable[[str], None] | None, msg: str) -> None:
    if log:
        log(msg)


def _parse_json_object(raw: str) -> dict[str, Any] | None:
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def prepare_scenes_for_media(scenes: list[Scene], default_media: str = "pexels", topic: str = "") -> list[Scene]:
    """Use internet media (Pexels/AI) when screen/slide is set but no file was uploaded."""
    prepared: list[Scene] = []
    fallback = "pexels" if default_media == "pexels" else "ai"
    for scene in scenes:
        item = dict(scene)
        media_type = str(item.get("media_type") or fallback).lower()
        local_file = str(item.get("local_file") or "").strip()
        if media_type in {"screen", "slide"} and not local_file:
            item["media_type"] = fallback
        if not str(item.get("search_query") or "").strip():
            item["search_query"] = build_search_query(
                str(item.get("visual") or ""),
                str(item.get("narration") or ""),
                str(item.get("screen_text") or ""),
                topic,
            )
        prepared.append(item)  # type: ignore[arg-type]
    return prepared


def build_search_query(visual: str, narration: str, screen_text: str, topic: str = "") -> str:
    """English keywords for Pexels / AI — topic-aware, not generic stock."""
    parts: list[str] = []
    for chunk in (visual, screen_text, topic, narration):
        text = re.sub(r"[^\w\s]", " ", chunk or "").strip()
        if not text:
            continue
        if re.search(r"[\u0600-\u06FF]", text):
            continue
        parts.append(text)
    query = " ".join(parts).strip()
    query = re.sub(r"\s+", " ", query)
    if len(query) < 6 and topic:
        query = f"{topic} tutorial computer screen software"
    return query[:100] or "software tutorial computer screen"


def research_topic_with_agent(
    topic: str,
    duration_sec: int,
    default_media: str,
    log: Callable[[str], None] | None = None,
) -> tuple[list[Scene], dict[str, Any]]:
    """
    Agent step: research the topic (steps, tools, official sources) then build scenes
    with internet-searchable English queries for each visual.
    """
    duration_sec = clamp_duration(duration_sec)
    segments = segment_count(duration_sec)
    per_scene = max(4, min(20, round(duration_sec / max(segments, 1))))
    media_default = "pexels" if default_media == "pexels" else "ai"

    prompt = f"""أنت وكيل إنتاج فيديو تعليمي عربي. مهمتك البحث والتخطيط قبل التصوير.

الموضوع: {topic}
المدة: {duration_sec} ثانية — عدد المشاهd المستهدف: {segments}

## مهم جداً
1. ابحث في معرفتك عن الموضوع: الخطوات الرسمية، الأدوات، الموقع الرسمي، أوامر التثبيت، أخطاء شائعة.
2. لا تستخدم مشاهd "screen" أو "slide" — استخدم فقط pexels أو ai لأن النظام يحمّل الوسائط من الإنترنت تلقائياً.
3. لكل مشهد أعطِ search_query بالإنجليزية: كلمات دقيقة لصورة/فيديو stock (مثال Flutter: "flutter sdk download windows installer screen").
4. visual بالإنجليزية — وصف مشهد يمكن تصويره أو توليده.
5. narration بالعربية — شرح خطوة محددة وليس مقدمة عامة فقط.

أعد JSON فقط:
{{
  "research_summary": "ملخص 3-5 جمل عن الموضوع والخطوات الرسمية",
  "official_sources": ["رابط أو اسم مصدر 1", "مصدر 2"],
  "key_steps": ["خطوة 1", "خطوة 2"],
  "scenes": [
    {{
      "narration": "نص الصوت بالعربية",
      "characters": "مقدّم",
      "method": "شرح على الشاشة",
      "voice_style": "واضح تعليمي",
      "visual": "flutter official website download page on laptop",
      "screen_text": "Step 1",
      "search_query": "flutter sdk download website laptop screen",
      "duration_sec": {per_scene},
      "media_type": "{media_default}"
    }}
  ]
}}"""

    _log_call(log, f"🔎 وكيل البحث: جمع تفاصيل «{topic}»...")
    try:
        _configure_gemini()
        for model_name in GEMINI_MODELS:
            try:
                _log_call(log, f"  • تحليل عبر {model_name}...")
                model = genai.GenerativeModel(model_name)
                response = model.generate_content(prompt)
                data = _parse_json_object((response.text or "").strip())
                if not data:
                    continue
                items = data.get("scenes") or []
                scenes = [_normalize_scene(item, media_default) for item in items if isinstance(item, dict)]
                scenes = [s for s in scenes if s.get("narration")]
                if len(scenes) < 3:
                    continue
                if len(scenes) > segments:
                    scenes = scenes[:segments]
                scenes = prepare_scenes_for_media(scenes, media_default, topic)
                meta = {
                    "research_summary": str(data.get("research_summary") or "").strip(),
                    "official_sources": data.get("official_sources") or [],
                    "key_steps": data.get("key_steps") or [],
                    "source": "gemini_agent",
                }
                _log_call(log, f"✅ البحث اكتمل — {len(scenes)} مشهدًا جاهزًا للوسائط")
                if meta["research_summary"]:
                    _log_call(log, f"📚 ملخص: {meta['research_summary'][:200]}")
                return scenes, meta
            except Exception as exc:
                if _is_quota_error(exc):
                    continue
                raise
    except Exception:
        pass

    _log_call(log, "⚠️ وكيل البحث: Gemini غير متاح — سيناريو محلي مع استعلامات بحث محسّنة")
    from video_pipeline import _generate_local_scenes_smart

    scenes = _generate_local_scenes_smart(topic, segments, per_scene, False, duration_sec)
    scenes = prepare_scenes_for_media(scenes, media_default, topic)
    for scene in scenes:
        if not scene.get("search_query"):
            scene["search_query"] = build_search_query(
                scene.get("visual", ""),
                scene.get("narration", ""),
                scene.get("screen_text", ""),
                topic,
            )
    return scenes, {"research_summary": "", "official_sources": [], "key_steps": [], "source": "local_agent"}


def research_islamic_story_with_agent(
    topic: str,
    duration_sec: int,
    log: Callable[[str], None] | None = None,
    settings: dict[str, Any] | None = None,
) -> tuple[list[Scene], dict[str, Any]]:
    """Research agent for Islamic narrative content — scene breakdown + AI prompts."""
    settings = settings or {}
    duration_sec = clamp_duration(duration_sec)
    segments = max(5, min(segment_count(duration_sec), 12))
    per_scene = max(6, min(25, round(duration_sec / max(segments, 1))))

    include_quran = settings.get("include_quran", True)
    include_hadith = settings.get("include_hadith", False)
    historical_accuracy = settings.get("historical_accuracy", True)
    visual_style = settings.get("visual_style", "cinematic_islamic")

    quran_line = (
        "7. في 1-2 مشاهد أضف quran_verse (بالتشكيل) و quran_reference و presentation: quran_text"
        if include_quran
        else "7. لا تُدرج آيات قرآنية (include_quran=false)."
    )
    hadith_line = (
        "8. في مشهد واحد أضف hadith_text و hadith_reference من حديث صحيح."
        if include_hadith
        else "8. لا تُدرج أحاديث (include_hadith=false)."
    )
    accuracy_block = (
        "\n## دقة تاريخية\n- لا تختلق حوارات أو أحداثاً.\n- بدون مصدر: narration وصف عام فقط.\n"
        if historical_accuracy
        else ""
    )

    prompt = f"""أنت وكيل إنتاج فيديو قصصي إسلامي عربي محترم. مهمتك البحث وتقسيم القصة قبل التصوير.

الموضوع: {topic}
المدة: {duration_sec} ثانية — عدد المشاهd: {segments}
الأسلوب البصري: {visual_style}
{accuracy_block}
## جذب المشاهد (Story Engagement)
- المشهد الأول: افتتاحية تشويقية hook_scene (سؤال أو مشهد درامي، لا تبدأ بجملة جافة مثل «قصة X من القصص...»)
- كل 3-4 مشاهد: cliffhanger خفيف ينقل للمشهد التالي
- المشهد الأخير: lesson_summary يجمع العبرة بوضوح
- engagement_role: hook | narrative | cliffhanger | lesson

## قواعد صارمة للمحتوى
1. أنشئ أولاً story_reference بالمصادر الشرعية (قرآن/حديث) ولا تختلق أحداثاً غير موجودة.
2. كل مشهد في scenes يجب أن يطابق حدثاً من key_events فقط — لا تضف أحداثاً جديدة.
3. narration بالعربية — سرد محترم، بدون اجتهاد في العقيدة.
4. visual و ai_prompt بالإنجليزية.

## قواعد التصوير (إلزامية في كل ai_prompt)
- No clear faces.
- No direct depiction of prophets or companions.
- Use silhouettes, back view, distant figures, cinematic lighting.
- No text, no watermark, no modern objects.
- Ancient Arabian atmosphere, golden cinematic lighting, consistent color grading.

5. scene_kind: landscape | historical_event | map_site | closing_lesson
6. presentation (تنويع المشاهد): static | ken_burns_zoom | ken_burns_pan | map_slide | quran_text
{quran_line}
{hadith_line}
9. media_type: ai للمشاهد التاريخية، pexels فقط للمناظر الطبيعية.
10. character_id أو character_ids: معرف الشخصية لاتساق المظهر (مثل young_believer_1).
11. source_type: quran | hadith | historical | general — و confidence من 0 إلى 100.

مثال أهل الكهف — key_events:
هروب الفتية، الوصول للكهف، النوم، تغير المدينة، إرسال للطعام، الاكتشاف.

أعد JSON فقط:
{{
  "story_reference": {{
    "title": "قصة أهل الكهف",
    "sources": ["سورة الكهف الآيات 9-26"],
    "key_events": ["هروب الفتية", "الوصول إلى الكهف", "..."],
    "key_lessons": ["الثبات على الإيمان"]
  }},
  "research_summary": "ملخص القصة من المصادر",
  "scenes": [
    {{
      "narration": "نص الصوت بالعربية",
      "characters": "راوٍ",
      "method": "سرد قصصي",
      "voice_style": "هادئ storyteller",
      "visual": "silhouettes of young believers walking toward mountains at sunset, back view only",
      "screen_text": "الهروب",
      "ai_prompt": "Silhouettes of young believers walking toward rocky mountains at sunset, back view only, cinematic Islamic historical scene, no clear faces, no direct depiction of prophets, dramatic lighting, no text",
      "scene_kind": "historical_event",
      "presentation": "ken_burns_zoom",
      "quran_verse": "",
      "quran_reference": "",
      "hadith_text": "",
      "hadith_reference": "",
      "duration_sec": {per_scene},
      "media_type": "ai"
    }}
  ]
}}"""

    _log_call(log, f"🕌 وكيل القصص الإسلامية: تحليل «{topic}»...")
    try:
        _configure_gemini()
        for model_name in GEMINI_MODELS:
            try:
                _log_call(log, f"  • تحليل قصصي عبر {model_name}...")
                model = genai.GenerativeModel(model_name)
                response = model.generate_content(prompt)
                data = _parse_json_object((response.text or "").strip())
                if not data:
                    continue
                items = data.get("scenes") or []
                scenes = [_normalize_scene(item, "ai") for item in items if isinstance(item, dict)]
                scenes = [s for s in scenes if s.get("narration")]
                if len(scenes) < 4:
                    continue
                if len(scenes) > segments:
                    scenes = scenes[:segments]
                story_ref = normalize_story_reference(data.get("story_reference"), topic)
                save_story_reference(story_ref)
                from story_engagement import apply_story_engagement

                scenes = apply_story_engagement(scenes, topic, settings, story_ref)
                _log_call(log, f"📜 مراجع القصة: {', '.join(story_ref.get('sources', [])[:2]) or 'محلي'}")
                for scene in scenes:
                    scene["content_profile"] = "islamic_story"
                    if not scene.get("ai_prompt"):
                        visual = scene.get("visual", "")
                        scene["ai_prompt"] = f"{visual}, {ISLAMIC_DEPICTION_RULES}"
                    elif ISLAMIC_DEPICTION_RULES.lower() not in (scene.get("ai_prompt") or "").lower():
                        scene["ai_prompt"] = f"{scene['ai_prompt']}, {ISLAMIC_DEPICTION_RULES}"
                    from visual_styles import append_visual_style

                    scene["ai_prompt"] = append_visual_style(scene.get("ai_prompt") or "", visual_style)
                meta = {
                    "content_profile": "islamic_story",
                    "story_reference": story_ref,
                    "research_summary": str(data.get("research_summary") or "").strip(),
                    "key_lessons": story_ref.get("key_lessons") or data.get("key_lessons") or [],
                    "source": "gemini_islamic_agent",
                }
                _log_call(log, f"✅ تقسيم القصة اكتمل — {len(scenes)} مشهدًا")
                if meta["research_summary"]:
                    _log_call(log, f"📚 ملخص: {meta['research_summary'][:200]}")
                return scenes, meta
            except Exception as exc:
                if _is_quota_error(exc):
                    continue
                raise
    except Exception:
        pass

    _log_call(log, "⚠️ Gemini غير متاح — سيناريو إسلامي محلي")
    from story_engagement import apply_story_engagement, build_islamic_scenes_from_reference

    story_ref = build_local_story_reference(topic)
    save_story_reference(story_ref)
    scenes = build_islamic_scenes_from_reference(topic, segments, per_scene, story_ref)
    scenes = apply_story_engagement(scenes, topic, settings, story_ref)
    for scene in scenes:
        scene["content_profile"] = "islamic_story"
        if not scene.get("ai_prompt"):
            scene["ai_prompt"] = f"{scene.get('visual', '')}, {ISLAMIC_DEPICTION_RULES}"
    return scenes, {
        "content_profile": "islamic_story",
        "story_reference": story_ref,
        "research_summary": story_ref.get("title", ""),
        "key_lessons": story_ref.get("key_lessons", []),
        "source": "local_islamic_agent",
    }


def research_for_topic(
    topic: str,
    duration_sec: int,
    default_media: str,
    settings: dict[str, Any] | None,
    log: Callable[[str], None] | None = None,
) -> tuple[list[Scene], dict[str, Any]]:
    from content_profiles import detect_content_profile, profile_label_ar

    settings = settings or {}
    profile = detect_content_profile(topic, settings)
    _log_call(log, f"📂 نوع المحتوى المكتشف: {profile_label_ar(profile)}")
    if profile == "islamic_story":
        return research_islamic_story_with_agent(topic, duration_sec, log, settings)
    scenes, meta = research_topic_with_agent(topic, duration_sec, default_media, log)
    meta["content_profile"] = profile
    return scenes, meta
