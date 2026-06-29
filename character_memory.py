from __future__ import annotations

import re
from typing import Any

from video_pipeline import Scene

CharacterDef = dict[str, Any]

# Visual memory — same appearance tokens appended to every related AI prompt.
STORY_CHARACTERS: dict[str, dict[str, CharacterDef]] = {
    "اهل الكهف": {
        "young_believer_1": {
            "label_ar": "فتى مؤمن 1",
            "appearance": {
                "robe": "dark brown wool robe",
                "age": "18",
                "hair": "black short hair",
                "build": "slim young man",
                "view": "back view silhouette only, no face",
            },
            "keywords": ["فتية", "فتى", "شباب", "مؤمن", "هروب", "believers", "youth"],
        },
        "young_believer_2": {
            "label_ar": "فتى مؤمن 2",
            "appearance": {
                "robe": "deep gray travel robe",
                "age": "20",
                "hair": "dark hair",
                "build": "medium build",
                "view": "side silhouette, face hidden",
            },
            "keywords": ["فتية", "شباب", "travelers", "companions"],
        },
        "cave_dog": {
            "label_ar": "كلب الكهف",
            "appearance": {
                "species": "medium guard dog",
                "color": "tan and brown fur",
                "pose": "lying at cave entrance, paws stretched",
            },
            "keywords": ["كلب", "dog", "cave entrance"],
        },
    },
    "يوسف": {
        "young_yusuf": {
            "label_ar": "يوسف عليه السلام (رمزي)",
            "appearance": {
                "robe": "simple cream tunic",
                "age": "teen silhouette",
                "hair": "dark wavy hair",
                "view": "distant figure or back view, no facial features",
            },
            "keywords": ["يوسف", "yusuf", "prophet dream", "بئر"],
        },
        "brothers_group": {
            "label_ar": "الإخوة",
            "appearance": {
                "robes": "mixed earth-tone robes brown and olive",
                "count": "group of men distant",
                "view": "silhouettes only from behind",
            },
            "keywords": ["إخوة", "brothers", "jealousy"],
        },
    },
    "اصحاب الاخدود": {
        "believer_boy": {
            "label_ar": "الفتى المؤمن",
            "appearance": {
                "robe": "white simple robe",
                "age": "12",
                "hair": "short black hair",
                "view": "small figure silhouette, no face",
            },
            "keywords": ["فتى", "مؤمن", "boy", "believer"],
        },
        "tyrant_king": {
            "label_ar": "الملك الظالم",
            "appearance": {
                "robe": "dark crimson royal cloak",
                "crown": "subtle golden crown silhouette",
                "view": "distant authority figure, face in shadow",
            },
            "keywords": ["ملك", "ظالم", "king", "tyrant"],
        },
    },
    "اصحاب الفيل": {
        "war_elephant": {
            "label_ar": "فيل الحرب",
            "appearance": {
                "elephant": "large war elephant with ornate howdah",
                "color": "gray elephant, red and gold textiles",
                "scale": "massive compared to tiny soldiers",
            },
            "keywords": ["فيل", "elephant", "army", "جيش"],
        },
        "abyssinian_army": {
            "label_ar": "جيش أبرهة",
            "appearance": {
                "armor": "ancient leather and bronze armor",
                "banners": "dark banners",
                "view": "distant army silhouettes marching",
            },
            "keywords": ["جيش", "أبرهة", "army", "march"],
        },
    },
}

PIVOTAL_HINTS: dict[str, list[str]] = {
    "اهل الكهف": ["كهف", "دخول", "نوم", "استيقاظ", "خروج", "cave", "sleep", "awakening"],
    "يوسف": ["بئر", "well", "سجن", "prison", "ملك", "reunion", "رؤيا", "dream", "يوسف", "مصر"],
    "اصحاب الاخدود": ["خندق", "ditch", "fire", "نار", "شهادة", "فتى", "ملك", "ظالم"],
    "اصحاب الفيل": ["فيل", "elephant", "كعبة", "kaaba", "طير", "birds", "حجارة"],
}


def _topic_key(topic: str) -> str:
    text = topic.lower().replace("أ", "ا").replace("إ", "ا").replace("آ", "ا")
    if "كهف" in text:
        return "اهل الكهف"
    if "يوسف" in text:
        return "يوسف"
    if "اخدود" in text or "أخدود" in topic:
        return "اصحاب الاخدود"
    if "فيل" in text or "ابره" in text:
        return "اصحاب الفيل"
    for key in STORY_CHARACTERS:
        if key in text:
            return key
    return ""


def _haystack(scene: Scene) -> str:
    parts = [
        scene.get("narration", ""),
        scene.get("characters", ""),
        scene.get("visual", ""),
        scene.get("screen_text", ""),
        scene.get("ai_prompt", ""),
    ]
    return " ".join(parts).lower()


def match_character_ids(scene: Scene, topic: str) -> list[str]:
    key = _topic_key(topic)
    if not key or key not in STORY_CHARACTERS:
        return []

    explicit = scene.get("character_ids")
    if isinstance(explicit, list) and explicit:
        return [c.strip() for c in explicit if c.strip()]

    explicit_one = (scene.get("character_id") or "").strip()
    if explicit_one:
        return [explicit_one]

    hay = _haystack(scene)
    matched: list[str] = []
    for char_id, meta in STORY_CHARACTERS[key].items():
        keywords = meta.get("keywords") or []
        if any(str(kw).lower() in hay for kw in keywords):
            matched.append(char_id)
    return matched[:3]


def appearance_prompt(char_def: CharacterDef) -> str:
    appearance = char_def.get("appearance") or {}
    if not isinstance(appearance, dict):
        return ""
    parts = [f"{k}: {v}" for k, v in appearance.items() if str(v).strip()]
    return ", ".join(parts)


def build_character_prompt_suffix(character_ids: list[str], topic: str) -> str:
    key = _topic_key(topic)
    if not key or not character_ids:
        return ""
    registry = STORY_CHARACTERS.get(key, {})
    chunks: list[str] = []
    for char_id in character_ids:
        meta = registry.get(char_id)
        if not meta:
            continue
        app = appearance_prompt(meta)
        if app:
            chunks.append(f"character {char_id} consistent look: {app}")
    return "; ".join(chunks)


def apply_character_memory(scenes: list[Scene], topic: str) -> list[Scene]:
    key = _topic_key(topic)
    if not key:
        return scenes

    enriched: list[Scene] = []
    for scene in scenes:
        item = dict(scene)
        char_ids = match_character_ids(item, topic)  # type: ignore[arg-type]
        if char_ids:
            item["character_ids"] = char_ids
            suffix = build_character_prompt_suffix(char_ids, topic)
            if suffix:
                existing = str(item.get("ai_prompt") or "").strip()
                if suffix.lower() not in existing.lower():
                    item["ai_prompt"] = f"{existing}, {suffix}".strip(", ") if existing else suffix
        enriched.append(item)  # type: ignore[arg-type]
    return enriched


def _pivotal_score(scene: Scene, topic: str) -> int:
    key = _topic_key(topic)
    hints = PIVOTAL_HINTS.get(key, [])
    if not hints:
        return 0
    hay = _haystack(scene)
    return sum(2 if len(h) > 4 else 1 for h in hints if h in hay)


def is_pivotal_scene(scene: Scene, topic: str) -> bool:
    return _pivotal_score(scene, topic) > 0


def mark_pivotal_scenes(scenes: list[Scene], topic: str, max_pivotal: int = 4) -> list[Scene]:
    """Flag top key scenes for future AI Video; keep AI Images + Ken Burns for others."""
    scores = [( _pivotal_score(scene, topic), idx) for idx, scene in enumerate(scenes)]
    scores.sort(key=lambda item: (item[0], -item[1]), reverse=True)
    pivotal_indices = {idx for score, idx in scores[:max_pivotal] if score > 0}

    marked: list[Scene] = []
    for idx, scene in enumerate(scenes):
        item = dict(scene)
        pivotal = bool(item.get("is_pivotal")) or idx in pivotal_indices
        item["is_pivotal"] = pivotal
        item["media_priority"] = "ai_video_candidate" if pivotal else "ai_image"
        marked.append(item)  # type: ignore[arg-type]
    return marked
