from __future__ import annotations

import re
from typing import Any, Literal

ContentProfile = Literal["auto", "educational", "islamic_story", "general"]
ResolvedProfile = Literal["educational", "islamic_story", "general"]

ISLAMIC_MARKERS = (
    "أهل الكهف",
    "اصحاب الكهف",
    "أصحاب الكهف",
    "سيدنا يوسف",
    "يوسف عليه",
    "قصة يوسف",
    "يوسف",
    "سيدنا",
    "عليه السلام",
    "أصحاب الأخدود",
    "اصحاب الاخدود",
    "سيدنا موسى",
    "موسى وفرعون",
    "فرعون",
    "غزوة بدر",
    "بدر",
    "فتح مكة",
    "مكة",
    "الصحابة",
    "صحابة",
    "صحابي",
    "الانبياء",
    "الأنبياء",
    "قصة انبياء",
    "قصص انبياء",
    "قصص الأنبياء",
    "قصص اسلامية",
    "قصة اسلامية",
    "سيرة",
    "prophet",
    "quran",
    "islamic story",
    "companions",
)

EDUCATIONAL_MARKERS = (
    "python",
    "flutter",
    "java",
    "programming",
    "btec",
    "تثبيت",
    "تنزيل",
    "download",
    "install",
    "code",
    "برمجة",
    "coding",
    "api",
    "database",
    "tutorial",
    "شرح",
    "درس",
)


def detect_content_profile(topic: str, settings: dict[str, Any] | None = None) -> ResolvedProfile:
    settings = settings or {}
    chosen = str(settings.get("content_profile") or "auto").strip().lower()
    if chosen == "educational":
        return "educational"
    if chosen == "islamic_story":
        return "islamic_story"
    if chosen == "general":
        return "general"

    return detect_content_profile_from_text(topic)


def detect_content_profile_from_text(text: str) -> ResolvedProfile:
    normalized = (text or "").lower().replace("أ", "ا").replace("إ", "ا").replace("آ", "ا")
    if any(marker.lower().replace("أ", "ا") in normalized for marker in ISLAMIC_MARKERS):
        return "islamic_story"
    if any(marker.lower() in normalized for marker in EDUCATIONAL_MARKERS):
        return "educational"
    return "general"


def is_islamic_story_profile(profile: ResolvedProfile) -> bool:
    return profile == "islamic_story"


def profile_label_ar(profile: ResolvedProfile) -> str:
    return {
        "educational": "تعليمي / تقني",
        "islamic_story": "قصة إسلامية",
        "general": "عام",
    }.get(profile, profile)
