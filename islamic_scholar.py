from __future__ import annotations

import re
from typing import Any, Callable

from video_pipeline import Scene

# Patterns that suggest invented dialogue or unsupported dramatization.
_DIALOGUE_PATTERNS = (
    r"قال\s+لهم",
    r"قالت\s+له",
    r"رد\s+عليه",
    r"سأله",
    r"أجاب",
    r"همس",
    r"صرخ",
    r'"',
    r"«",
)

_INVENTION_MARKERS = (
    "ربما",
    "يُحتمل",
    "يُقال",
    "قيل إن",
    "من الممكن",
)


def _normalize(text: str) -> str:
    return text.lower().replace("أ", "ا").replace("إ", "ا").replace("آ", "ا").strip()


def _event_tokens(event: str) -> set[str]:
    words = re.findall(r"[\u0600-\u06FF]{3,}", _normalize(event))
    return {w for w in words if len(w) >= 3}


def scene_matches_event(narration: str, screen_text: str, event: str) -> bool:
    hay = _normalize(f"{narration} {screen_text}")
    tokens = _event_tokens(event)
    if not tokens:
        return True
    hits = sum(1 for t in tokens if t in hay)
    return hits >= max(1, len(tokens) // 3)


def _looks_like_dialogue(text: str) -> bool:
    for pattern in _DIALOGUE_PATTERNS:
        if re.search(pattern, text):
            return True
    return False


def _soften_narration(narration: str, screen_text: str) -> str:
    title = (screen_text or "المشهد").strip()
    return (
        f"في هذا المشهد نستعرض جانبًا من القصة كما ورد في المصادر الشرعية، "
        f"متعلقًا بـ {title}، دون الخوض في تفاصيل غير مثبتة."
    )


def apply_historical_accuracy(
    scenes: list[Scene],
    story_ref: dict[str, Any] | None,
    *,
    enabled: bool = True,
    log: Callable[[str], None] | None = None,
) -> list[Scene]:
    """When enabled, avoid invented dialogue and flag scenes without source backing."""
    if not enabled:
        return scenes

    key_events = []
    if story_ref:
        key_events = [str(e).strip() for e in (story_ref.get("key_events") or []) if str(e).strip()]
    has_sources = bool(story_ref and story_ref.get("sources"))

    guarded: list[Scene] = []
    for idx, scene in enumerate(scenes):
        item = dict(scene)
        narration = str(item.get("narration") or "").strip()
        screen = str(item.get("screen_text") or "").strip()

        if not narration:
            guarded.append(item)  # type: ignore[arg-type]
            continue

        unsupported = False
        if key_events:
            if not any(scene_matches_event(narration, screen, ev) for ev in key_events):
                unsupported = True

        risky = _looks_like_dialogue(narration) or any(m in narration for m in _INVENTION_MARKERS)
        if not has_sources and (risky or unsupported):
            item["narration"] = _soften_narration(narration, screen)
            item["historical_note"] = "وصف عام — لا مصدر واضح"
            if log:
                log(f"  ⚖️ مشهد {idx + 1}: وصف عام (دقة تاريخية)")
        elif risky and has_sources:
            # Remove obvious dialogue while keeping factual tone
            cleaned = re.sub(r"[«»\"].*?[«»\"]", "", narration).strip()
            if len(cleaned) < 20:
                cleaned = _soften_narration(narration, screen)
            item["narration"] = cleaned
            if log:
                log(f"  ⚖️ مشهد {idx + 1}: إزالة حوار غير مثبت")

        guarded.append(item)  # type: ignore[arg-type]
    return guarded
