"""Curated story database: events + visual_requirements per scene."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from story_reference import ISLAMIC_DEPICTION_RULES, _topic_key
from video_pipeline import ROOT, Scene

STORIES_DIR = ROOT / "data" / "stories"

_STORY_FILES: dict[str, str] = {
    "يوسف": "yusuf.json",
    "اهل الكهف": "kahf.json",
    "اصحاب الفيل": "fil.json",
    "اصحاب الاخدود": "ukhdood.json",
}


def _story_path(topic: str) -> Path | None:
    key = _topic_key(topic)
    filename = _STORY_FILES.get(key)
    if not filename:
        return None
    path = STORIES_DIR / filename
    return path if path.exists() else None


def load_story_db(topic: str) -> dict[str, Any] | None:
    path = _story_path(topic)
    if not path:
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (json.JSONDecodeError, OSError):
        return None


def story_db_available(topic: str) -> bool:
    return load_story_db(topic) is not None


def _db_scenes(db: dict[str, Any]) -> list[dict[str, Any]]:
    scenes = db.get("scenes") or []
    return [s for s in scenes if isinstance(s, dict)]


def event_entry_for_index(db: dict[str, Any], idx: int, total: int) -> dict[str, Any] | None:
    entries = _db_scenes(db)
    if not entries:
        return None
    if total <= 1:
        return entries[0]
    if idx <= 0:
        return entries[0]
    if idx >= total - 1:
        return entries[-1]
    # Map middle scenes across story events (skip first/last reserved for hook/close)
    body_slots = max(1, total - 2)
    body_idx = idx - 1
    if len(entries) <= 2:
        pick = min(body_idx, len(entries) - 1)
        return entries[pick]
    span = len(entries) - 2
    pick = 1 + (body_idx * span) // body_slots
    return entries[min(pick, len(entries) - 1)]


def prompt_from_requirements(entry: dict[str, Any]) -> str:
    reqs = entry.get("visual_requirements") or []
    if isinstance(reqs, list) and reqs:
        parts = [str(r).strip() for r in reqs if str(r).strip()]
        if parts:
            return ", ".join(parts)
    event = str(entry.get("event") or "").strip()
    if event:
        return (
            f"cinematic Islamic historical scene about {event}, ancient Arabian atmosphere, "
            "silhouettes only, no clear faces, golden lighting, no text"
        )
    return ""


def build_scene_prompt_from_db(entry: dict[str, Any]) -> str:
    base = prompt_from_requirements(entry)
    if not base:
        return ""
    if ISLAMIC_DEPICTION_RULES.lower() not in base.lower():
        base = f"{base}, {ISLAMIC_DEPICTION_RULES}"
    return base


def enrich_scene_from_story_db(
    scene: Scene,
    idx: int,
    topic: str,
    total: int,
    log: Any | None = None,
) -> Scene:
    db = load_story_db(topic)
    if not db:
        return scene
    entry = event_entry_for_index(db, idx, total)
    if not entry:
        return scene

    item: dict[str, Any] = dict(scene)
    event = str(entry.get("event") or "").strip()
    narration = str(entry.get("narration") or "").strip()
    prompt = build_scene_prompt_from_db(entry)
    if not prompt:
        return scene  # type: ignore[return-value]

    changed = False
    if event:
        item["event"] = event
        item["screen_text"] = item.get("screen_text") or event[:40]
    if narration and not str(item.get("narration") or "").strip():
        item["narration"] = narration

    old_visual = str(item.get("visual") or "").lower()
    generic_markers = (
        "running toward rocky mountains",
        "young silhouettes running",
        "peaceful golden desert sunset",
        "cinematic islamic historical scene about",
        "ancient arabian atmosphere",
    )
    if not old_visual or any(m in old_visual for m in generic_markers):
        item["visual"] = prompt
        item["ai_prompt"] = prompt
        changed = True
    elif not item.get("ai_prompt"):
        item["ai_prompt"] = prompt
        changed = True

    item["visual_requirements"] = list(entry.get("visual_requirements") or [])
    item["media_type"] = "ai"
    item["media_source"] = "ai_image"
    item["scene_kind"] = "historical_event"

    if changed and log:
        log(f"  📖 مشهد {idx + 1}: prompt من قاعدة القصة — {event or 'حدث'}")
    return item  # type: ignore[return-value]


def apply_story_db_to_scenes(
    scenes: list[Scene],
    topic: str,
    log: Any | None = None,
) -> list[Scene]:
    if not load_story_db(topic):
        return scenes
    total = len(scenes)
    return [enrich_scene_from_story_db(s, i, topic, total, log) for i, s in enumerate(scenes)]
