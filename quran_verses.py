from __future__ import annotations

import re
from typing import Any

from video_pipeline import Scene

# Local verse bank for offline / fallback when the agent omits quran_verse.
STORY_VERSES: dict[str, list[dict[str, str]]] = {
    "اهل الكهف": [
        {
            "event_hint": "هروب",
            "verse": "إِذْ أَوَى الْفِتْيَةُ إِلَى الْكَهْفِ فَقَالُوا رَبَّنَا آتِنَا مِن لَّدُنكَ رَحْمَةً وَهَيِّئْ لَنَا مِنْ أَمْرِنَا رَشَدًا",
            "reference": "سورة الكهف — 18:10",
        },
        {
            "event_hint": "كهف",
            "verse": "وَتَرَى الشَّمْسَ إِذَا طَلَعَت تَّزَاوَرُ عَن كَهْفِهِمْ ذَاتَ الْيَمِينِ وَإِذَا غَرَبَت تَّقْرِضُهُمْ ذَاتَ الشِّمَالِ",
            "reference": "سورة الكهف — 18:17",
        },
        {
            "event_hint": "نوم",
            "verse": "وَنُقَلِّبُهُمْ ذَاتَ الْيَمِينِ وَذَاتَ الشِّمَالِ وَكَلْبُهُم بَاسِطٌ ذِرَاعَيْهِ بِالْوَصِيدِ",
            "reference": "سورة الكهف — 18:18",
        },
        {
            "event_hint": "استيقاظ",
            "verse": "وَكَذَٰلِكَ بَعَثْنَاهُمْ لِيَتَسَاءَلُوا بَيْنَهُمْ",
            "reference": "سورة الكهف — 18:19",
        },
        {
            "event_hint": "طعام",
            "verse": "وَكَذَٰلِكَ أَعْثَرْنَا عَلَيْهِمْ لِيَعْلَمُوا أَنَّ وَعْدَ اللَّهِ حَقٌّ",
            "reference": "سورة الكهف — 18:21",
        },
    ],
    "يوسف": [
        {
            "event_hint": "رؤيا",
            "verse": "إِذْ قَالَ يُوسُفُ لِأَبِيهِ يَا أَبَتِ إِنِّي رَأَيْتُ أَحَدَ عَشَرَ كَوْكَبًا وَالشَّمْسَ وَالْقَمَرَ رَأَيْتُهُمْ لِي سَاجِدِينَ",
            "reference": "سورة يوسف — 12:4",
        },
        {
            "event_hint": "بئر",
            "verse": "وَأَلْقَوْهُ فِي غَيَابَتِ الْجُبِّ",
            "reference": "سورة يوسف — 12:10",
        },
        {
            "event_hint": "صبر",
            "verse": "إِنَّهُ مَن يَتَّقِ وَيَصْبِرْ فَإِنَّ اللَّهَ لَا يُضِيعُ أَجْرَ الْمُحْسِنِينَ",
            "reference": "سورة يوسف — 12:90",
        },
    ],
    "اصحاب الاخدود": [
        {
            "event_hint": "خندق",
            "verse": "قُتِلَ أَصْحَابُ الْأُخْدُودِ النَّارِ ذَاتِ الْوَقُودِ",
            "reference": "سورة البروج — 85:4",
        },
        {
            "event_hint": "شهادة",
            "verse": "إِنَّ الَّذِينَ فَتَنُوا الْمُؤْمِنِينَ وَالْمُؤْمِنَاتِ ثُمَّ لَمْ يَتُوبُوا فَلَهُمْ عَذَابُ جَهَنَّمَ",
            "reference": "سورة البروج — 85:10",
        },
    ],
    "اصحاب الفيل": [
        {
            "event_hint": "فيل",
            "verse": "أَلَمْ تَرَ كَيْفَ فَعَلَ رَبُّكَ بِأَصْحَابِ الْفِيلِ",
            "reference": "سورة الفيل — 105:1",
        },
        {
            "event_hint": "طير",
            "verse": "وَأَرْسَلَ عَلَيْهِمْ طَيْرًا أَبَابِيلَ",
            "reference": "سورة الفيل — 105:3",
        },
        {
            "event_hint": "حجارة",
            "verse": "تَرْمِيهِم بِحِجَارَةٍ مِّن سِجِّيلٍ",
            "reference": "سورة الفيل — 105:4",
        },
    ],
}

STORY_HADITH: dict[str, list[dict[str, str]]] = {
    "اهل الكهف": [
        {
            "event_hint": "عبرة",
            "text": "من سلك طريقًا يطلب فيه علمًا سهل الله له طريقًا إلى الجنة",
            "reference": "رواه مسلم — عن أبي هريرة (عام في طلب العلم)",
        },
    ],
}


def _topic_key(topic: str) -> str:
    text = topic.lower().replace("أ", "ا").replace("إ", "ا").replace("آ", "ا")
    if "كهف" in text or "اهل الكهف" in text:
        return "اهل الكهف"
    if "يوسف" in text:
        return "يوسف"
    if "اخدود" in text or "أخدود" in topic:
        return "اصحاب الاخدود"
    if "فيل" in text or "ابره" in text:
        return "اصحاب الفيل"
    for key in STORY_VERSES:
        if key in text:
            return key
    return ""


def _haystack(scene: Scene) -> str:
    parts = [
        scene.get("narration", ""),
        scene.get("screen_text", ""),
        scene.get("visual", ""),
    ]
    return " ".join(parts).lower()


def _score_verse(haystack: str, hint: str) -> int:
    if not hint:
        return 0
    tokens = [t for t in re.split(r"\s+", hint) if len(t) > 2]
    return sum(1 for t in tokens if t in haystack)


def find_verse_for_scene(scene: Scene, topic: str) -> dict[str, str] | None:
    key = _topic_key(topic)
    if not key or key not in STORY_VERSES:
        return None
    hay = _haystack(scene)
    best: dict[str, str] | None = None
    best_score = 0
    for entry in STORY_VERSES[key]:
        score = _score_verse(hay, entry.get("event_hint", ""))
        if score > best_score:
            best_score = score
            best = entry
    return best if best_score > 0 else None


def find_hadith_for_scene(scene: Scene, topic: str) -> dict[str, str] | None:
    key = _topic_key(topic)
    if not key or key not in STORY_HADITH:
        return None
    hay = _haystack(scene)
    for entry in STORY_HADITH[key]:
        if _score_verse(hay, entry.get("event_hint", "")) > 0:
            return entry
    return None


def inject_islamic_citations(
    scenes: list[Scene],
    topic: str,
    *,
    include_quran: bool = True,
    include_hadith: bool = False,
) -> list[Scene]:
    """Fill missing quran_verse / hadith on scenes when enabled."""
    enriched: list[Scene] = []
    used_verses: set[str] = set()

    for scene in scenes:
        item: dict[str, Any] = dict(scene)
        presentation = str(item.get("presentation") or "").lower()

        if include_quran and not str(item.get("quran_verse") or "").strip():
            match = find_verse_for_scene(item, topic)  # type: ignore[arg-type]
            if match and match["verse"] not in used_verses:
                item["quran_verse"] = match["verse"]
                item["quran_reference"] = match.get("reference", "")
                used_verses.add(match["verse"])
                if presentation not in {"quran_text"} and not item.get("presentation"):
                    pass

        if include_hadith and not str(item.get("hadith_text") or "").strip():
            hadith = find_hadith_for_scene(item, topic)  # type: ignore[arg-type]
            if hadith:
                item["hadith_text"] = hadith.get("text", "")
                item["hadith_reference"] = hadith.get("reference", "")

        if include_quran and presentation == "quran_text" and not str(item.get("quran_verse") or "").strip():
            match = find_verse_for_scene(item, topic)  # type: ignore[arg-type]
            if match:
                item["quran_verse"] = match["verse"]
                item["quran_reference"] = match.get("reference", "")

        enriched.append(item)  # type: ignore[arg-type]
    return enriched
