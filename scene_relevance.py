from __future__ import annotations

import re
from typing import Any, Mapping

from content_profiles import detect_content_profile, is_islamic_story_profile
from story_reference import _topic_key, build_local_story_reference

GENERIC_VISUAL_MARKERS = (
    "software tutorial",
    "download",
    "installer",
    "running toward rocky mountains",
    "young silhouettes running",
    "step 1",
    "flutter",
    "test",
    "demo",
    "sample",
    "lorem ipsum",
    "preview",
    "placeholder",
    "stock photo",
)

# Narration concept → expected visual vocabulary (Arabic + English).
NARRATION_VISUAL_SIGNALS: tuple[tuple[tuple[str, ...], tuple[str, ...]], ...] = (
    (("بئر", "البئر", "إلقاء", "ألق", "حفرة"), ("well", "pit", "cistern", "deep hole", "dark opening", "بئر")),
    (("مصر", "بيع", "سوق", "قافلة"), ("egypt", "market", "caravan", "merchant", "trade", "nile", "palace", "مصر")),
    (("سجن", "سجن"), ("prison", "dungeon", "chains", "jail", "dark cell", "سجن")),
    (("رؤيا", "كواكب", "شمس", "قمر"), ("dream", "stars", "planets", "sun and moon", "vision", "رؤيا")),
    (("إخوان", "اخوان", "غيرة", "brothers"), ("brothers", "siblings", "jealousy", "group of men", "إخوان")),
    (("ملك", "عزيز", "فرعون"), ("king", "pharaoh", "throne", "palace", "royal", "crown")),
    (("والد", "أب", "يعقوب"), ("father", "elderly man silhouette", "patriarch", "والد")),
    (("قمصان", "قميص"), ("shirt", "blood", "garment", "stained cloth", "قميص")),
    (("كهف", "نوم"), ("cave", "sleeping", "rock shelter", "كهف")),
    (("فيل", "أبره", "كعبة"), ("elephant", "kaaba", "mecca", "army", "فيل")),
    (("أخدود", "نار"), ("fire", "pit", "ditch", "flames", "أخدود")),
)

STORY_KEYWORDS: dict[str, tuple[str, ...]] = {
    "yusuf": (
        "يوسف", "بئر", "مصر", "سجن", "رؤيا", "إخوان", "قافلة", "عزيز", "ملك",
        "joseph", "well", "egypt", "prison", "dream", "brothers", "caravan",
    ),
    "اهل الكهف": ("كهف", "فتية", "نوم", "cave", "sleeping", "believers"),
    "اصحاب الفيل": ("فيل", "أبرهة", "كعبة", "makkah", "elephant"),
    "اصحاب الاخدود": ("أخدود", "اخدود", "شهيد", "fire", "ditched"),
}


def _scene_text_blob(scene: Mapping[str, Any], topic: str) -> str:
    parts = [
        topic,
        str(scene.get("narration") or ""),
        str(scene.get("visual") or ""),
        str(scene.get("ai_prompt") or ""),
        str(scene.get("screen_text") or ""),
        str(scene.get("search_query") or ""),
    ]
    return " ".join(parts).lower()


def _score_narration_visual_alignment(narration: str, visual_blob: str) -> tuple[int, list[str], list[str]]:
    """Does the visual prompt describe what the narration says?"""
    narration_l = (narration or "").lower()
    visual_l = (visual_blob or "").lower()
    if not narration_l.strip():
        return 70, [], []

    matched_groups: list[str] = []
    missing_groups: list[str] = []
    score = 72
    active_groups = 0

    for n_markers, v_markers in NARRATION_VISUAL_SIGNALS:
        if not any(m in narration_l for m in n_markers):
            continue
        active_groups += 1
        label = n_markers[0]
        if any(m in visual_l for m in v_markers):
            matched_groups.append(label)
            score += 12
        else:
            missing_groups.append(label)
            score -= 22

    if active_groups == 0:
        return 65, [], []

    return max(0, min(100, score)), matched_groups, missing_groups


def score_scene_relevance(
    scene: Mapping[str, Any],
    topic: str,
    settings: dict[str, Any] | None = None,
) -> dict[str, Any]:
    settings = settings or {}
    profile = detect_content_profile(topic, settings)
    blob = _scene_text_blob(scene, topic)
    narration = str(scene.get("narration") or "").strip()
    visual_only = " ".join(
        [
            str(scene.get("visual") or ""),
            str(scene.get("ai_prompt") or ""),
            str(scene.get("search_query") or ""),
        ]
    ).lower()

    story_key = _topic_key(topic)
    keywords = list(STORY_KEYWORDS.get(story_key, ()))
    if not keywords and is_islamic_story_profile(profile):
        ref = build_local_story_reference(topic)
        for event in ref.get("key_events") or []:
            for token in re.split(r"[\s،,]+", str(event)):
                token = token.strip()
                if len(token) >= 3:
                    keywords.append(token.lower())

    matched = [kw for kw in keywords if kw.lower() in blob]
    keyword_ratio = len(set(matched)) / max(len(set(keywords)), 1)
    topic_score = int(min(100, 40 + keyword_ratio * 55))

    align_score, align_ok, align_missing = _score_narration_visual_alignment(narration, visual_only)
    if align_ok:
        score = (topic_score * 20 + align_score * 80) // 100
    elif align_missing:
        score = (topic_score * 30 + align_score * 70) // 100
    else:
        score = (topic_score * 35 + align_score * 65) // 100

    generic_hits = [g for g in GENERIC_VISUAL_MARKERS if g in blob]
    if generic_hits:
        score = max(0, score - 20 * len(generic_hits))

    if narration and visual_only and len(narration) > 15:
        score = min(100, score + 5)

    role = str(scene.get("engagement_role") or "")
    if role in {"hook", "lesson", "cliffhanger"}:
        score = min(100, score + 3)

    min_score = int(settings.get("scene_relevance_min_score", 80) or 80)
    if not is_islamic_story_profile(profile):
        min_score = min(min_score, 50)

    final_score = max(0, min(100, score))
    return {
        "relevance_score": final_score,
        "story_relevance": final_score,
        "topic_keyword_score": topic_score,
        "narration_visual_alignment": align_score,
        "matched_keywords": matched[:8],
        "aligned_concepts": align_ok,
        "missing_concepts": align_missing,
        "generic_markers": generic_hits,
        "min_score": min_score,
        "passes": final_score >= min_score,
    }
