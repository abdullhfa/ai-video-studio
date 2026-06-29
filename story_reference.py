from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from video_pipeline import OUTPUTS

STORY_REFERENCE_PATH = OUTPUTS / "story_reference.json"

ISLAMIC_DEPICTION_RULES = (
    "No clear faces. No direct depiction of prophets or companions. "
    "Use silhouettes, back view, distant figures, cinematic lighting only. "
    "Respectful Islamic historical illustration, no text, no watermark."
)

LOCAL_STORY_REFERENCES: dict[str, dict[str, Any]] = {
    "اهل الكهف": {
        "title": "قصة أهل الكهف",
        "sources": ["سورة الكهف الآيات 9-26"],
        "key_events": [
            "هروب الفتية المؤمنين من قومهم",
            "اللجوء إلى الكهف والدعاء",
            "النوم في الكهف لسنوات طويلة",
            "تغيّر المدينة والزمان حولهم",
            "إرسال أحدهم بفضة للطعام بحذر",
            "اكتشاف أمرهم بعد الاستيقاظ",
        ],
        "key_lessons": ["الثبات على الإيمان", "قدرة الله على حفظ المؤمنين"],
    },
    "اصحاب الاخدود": {
        "title": "أصحاب الأخدود",
        "sources": ["سورة البروج الآيات 4-9", "صحيح البخاري — حديث أصحاب الأخدود"],
        "key_events": [
            "الملك الظالم وأصحاب الأخدود",
            "الساحر والفتى المؤمن",
            "تعليم الفتى الإيمان",
            "إلقاء المؤمنين في الأخدود",
            "شهادة الفتى وثباته",
            "انتصار الإيمان على الظلم",
        ],
        "key_lessons": ["الثبات على الحق", "الشهادة في سبيل الإيمان"],
    },
    "اصحاب الفيل": {
        "title": "أصحاب الفيل",
        "sources": ["سورة الفيل"],
        "key_events": [
            "أبرهة يبني القليسة",
            "سعي أبرهة لهدم الكعبة",
            "زحف الجيش مع الفيل",
            "إرسال الطير أبابيل",
            "رمي الحجارة على الجيش",
            "إفناء الجيش ونجاة البيت",
        ],
        "key_lessons": ["حماية الله لبيته", "الباطل يزول مهما عظم"],
    },
    "يوسف": {
        "title": "قصة سيدنا يوسف عليه السلام",
        "sources": ["سورة يوسف"],
        "key_events": [
            "رؤيا يوسف عليه السلام",
            "غيرة الإخوة وإلقاؤه في البئر",
            "بيعه في مصر",
            "فتنة امرأة العزيز والسجن",
            "تفسير رؤيا الملك",
            "الشفاعة والاجتماع بالأهل",
        ],
        "key_lessons": ["الصبر والثقة بالله", "العدل يعلو في النهاية"],
    },
}


def _topic_key(topic: str) -> str:
    text = topic.lower()
    for key in LOCAL_STORY_REFERENCES:
        if key in text.replace("أ", "ا").replace("إ", "ا"):
            return key
    if "كهف" in topic:
        return "اهل الكهف"
    if "يوسف" in topic:
        return "يوسف"
    if "اخدود" in topic or "أخدود" in topic:
        return "اصحاب الاخدود"
    if "فيل" in topic or "أبره" in topic:
        return "اصحاب الفيل"
    return ""


def build_local_story_reference(topic: str) -> dict[str, Any]:
    key = _topic_key(topic)
    if key and key in LOCAL_STORY_REFERENCES:
        ref = dict(LOCAL_STORY_REFERENCES[key])
        ref["topic"] = topic
        return ref
    return {
        "title": topic.strip(),
        "sources": [],
        "key_events": [],
        "key_lessons": [],
        "topic": topic.strip(),
    }


def normalize_story_reference(raw: dict[str, Any] | None, topic: str) -> dict[str, Any]:
    if not raw:
        return build_local_story_reference(topic)
    ref = {
        "title": str(raw.get("title") or topic).strip(),
        "sources": _as_str_list(raw.get("sources")),
        "key_events": _as_str_list(raw.get("key_events")),
        "key_lessons": _as_str_list(raw.get("key_lessons") or raw.get("key_lessons")),
        "topic": topic.strip(),
    }
    for key in ("session_id", "content_profile", "research_source", "pipeline_version"):
        if raw.get(key):
            ref[key] = raw[key]
    if not ref["key_events"]:
        ref["key_events"] = build_local_story_reference(topic).get("key_events", [])
    if not ref["sources"]:
        ref["sources"] = build_local_story_reference(topic).get("sources", [])
    return ref


def _as_str_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    if isinstance(value, str) and value.strip():
        return [part.strip() for part in re.split(r"[\n;]+", value) if part.strip()]
    return []


def story_display_name(topic: str, reference: dict[str, Any] | None = None) -> str:
    key = _topic_key(topic)
    if key:
        return key
    if reference:
        title = str(reference.get("title") or "").strip()
        if title:
            return title[:40]
    return topic.strip()[:40] or "قصة"


def save_story_reference(reference: dict[str, Any]) -> Path:
    OUTPUTS.mkdir(parents=True, exist_ok=True)
    payload = dict(reference)
    try:
        from production_session import stamp_session_fields

        payload = stamp_session_fields(payload)
    except ImportError:
        pass
    STORY_REFERENCE_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return STORY_REFERENCE_PATH


def load_story_reference() -> dict[str, Any] | None:
    if not STORY_REFERENCE_PATH.exists():
        return None
    try:
        data = json.loads(STORY_REFERENCE_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        return None


def load_story_reference_for_topic(topic: str) -> dict[str, Any]:
    loaded = load_story_reference()
    if not loaded:
        return build_local_story_reference(topic)
    try:
        from production_session import file_matches_topic_session

        if not file_matches_topic_session(loaded, topic):
            return build_local_story_reference(topic)
    except ImportError:
        pass
    current_key = _topic_key(topic)
    saved_key = _topic_key(str(loaded.get("topic") or loaded.get("title") or ""))
    if current_key and saved_key and current_key != saved_key:
        return build_local_story_reference(topic)
    return normalize_story_reference(loaded, topic)
