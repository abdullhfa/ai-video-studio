from __future__ import annotations

import hashlib
import json
import re
import shutil
from pathlib import Path
from typing import Any

from video_pipeline import ROOT, Scene

CACHE_ROOT = ROOT / "cache"


def cache_enabled(settings: dict[str, Any] | None) -> bool:
    settings = settings or {}
    if _force_fresh_media(settings):
        return False
    value = settings.get("scene_cache_enabled", True)
    if isinstance(value, bool):
        return value
    return str(value).lower() in {"1", "true", "yes", "on"}


def _force_fresh_media(settings: dict[str, Any] | None) -> bool:
    settings = settings or {}
    value = settings.get("force_fresh_media", False)
    if isinstance(value, bool):
        return value
    return str(value).lower() in {"1", "true", "yes", "on"}


def clear_topic_cache(topic: str) -> int:
    """Remove cached scene media for a topic slug. Returns deleted entry count."""
    slug_dir = CACHE_ROOT / topic_slug(topic)
    if not slug_dir.exists():
        return 0
    manifest = _load_manifest(topic)
    count = len(manifest.get("entries") or {})
    shutil.rmtree(slug_dir, ignore_errors=True)
    return count


def clear_all_cache() -> int:
    if not CACHE_ROOT.exists():
        return 0
    topics = [p for p in CACHE_ROOT.iterdir() if p.is_dir()]
    for path in topics:
        shutil.rmtree(path, ignore_errors=True)
    return len(topics)


def topic_slug(topic: str) -> str:
    text = topic.strip().lower()
    text = text.replace("أ", "ا").replace("إ", "ا").replace("آ", "ا")
    mapping = {
        "قصة أهل الكهف": "ahl_alkahf",
        "اهل الكهف": "ahl_alkahf",
        "قصة سيدنا يوسف": "yusuf",
        "يوسف": "yusuf",
        "أصحاب الأخدود": "ashab_alkhudud",
        "اصحاب الاخدود": "ashab_alkhudud",
        "أصحاب الفيل": "ashab_alfil",
        "اصحاب الفيل": "ashab_alfil",
    }
    for key, slug in mapping.items():
        if key in topic or key.replace("أ", "ا") in text:
            return slug
    safe = re.sub(r"[^\w\u0600-\u06FF]+", "_", text).strip("_")
    if not safe:
        safe = "story"
    return safe[:48]


def scene_fingerprint(scene: Scene, topic: str, settings: dict[str, Any] | None = None) -> str:
    settings = settings or {}
    payload = {
        "topic": topic_slug(topic),
        "narration": (scene.get("narration") or "").strip(),
        "ai_prompt": (scene.get("ai_prompt") or "").strip(),
        "presentation": (scene.get("presentation") or "").strip(),
        "quran_verse": (scene.get("quran_verse") or "").strip(),
        "quran_reference": (scene.get("quran_reference") or "").strip(),
        "screen_text": (scene.get("screen_text") or "").strip(),
        "visual": (scene.get("visual") or "").strip(),
        "visual_requirements": scene.get("visual_requirements") or [],
        "event": (scene.get("event") or "").strip(),
        "character_ids": scene.get("character_ids") or [],
        "voice_name": settings.get("voice_name"),
        "tts_speed": settings.get("tts_speed"),
        "narrator_style": settings.get("narrator_style"),
        "arabic_font": settings.get("arabic_font"),
        "imagerouter_model": settings.get("imagerouter_model") or "black-forest-labs/FLUX-1-schnell",
        "image_provider": settings.get("image_provider") or "local",
        "local_image_backend": settings.get("local_image_backend") or "automatic1111",
        "local_image_model": settings.get("local_image_model") or "",
        "pipeline_version": "2.5.0",
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]


def _scene_dir(topic: str, fingerprint: str) -> Path:
    return CACHE_ROOT / topic_slug(topic) / "scenes" / fingerprint


def _manifest_path(topic: str) -> Path:
    return CACHE_ROOT / topic_slug(topic) / "manifest.json"


def _load_manifest(topic: str) -> dict[str, Any]:
    path = _manifest_path(topic)
    if not path.exists():
        return {"topic": topic_slug(topic), "entries": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {"topic": topic_slug(topic), "entries": {}}
    except json.JSONDecodeError:
        return {"topic": topic_slug(topic), "entries": {}}


def _save_manifest(topic: str, manifest: dict[str, Any]) -> None:
    path = _manifest_path(topic)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")


def get_cached_image(topic: str, fingerprint: str) -> Path | None:
    image = _scene_dir(topic, fingerprint) / "image.png"
    return image if image.exists() and image.stat().st_size > 1000 else None


def get_cached_audio(topic: str, fingerprint: str) -> Path | None:
    audio = _scene_dir(topic, fingerprint) / "narration.mp3"
    return audio if audio.exists() and audio.stat().st_size > 500 else None


def save_cached_image(
    topic: str,
    fingerprint: str,
    image_path: Path,
    meta: dict[str, Any] | None = None,
) -> Path:
    dest_dir = _scene_dir(topic, fingerprint)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / "image.png"
    shutil.copy2(image_path, dest)
    _update_manifest(topic, fingerprint, "image", dest, meta)
    return dest


def save_cached_audio(
    topic: str,
    fingerprint: str,
    audio_path: Path,
    meta: dict[str, Any] | None = None,
) -> Path:
    dest_dir = _scene_dir(topic, fingerprint)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / "narration.mp3"
    shutil.copy2(audio_path, dest)
    _update_manifest(topic, fingerprint, "audio", dest, meta)
    return dest


def _update_manifest(
    topic: str,
    fingerprint: str,
    kind: str,
    path: Path,
    meta: dict[str, Any] | None,
) -> None:
    manifest = _load_manifest(topic)
    entries = manifest.setdefault("entries", {})
    entry = entries.setdefault(fingerprint, {})
    entry[kind] = str(path.relative_to(CACHE_ROOT))
    if meta:
        entry["meta"] = {**(entry.get("meta") or {}), **meta}
    _save_manifest(topic, manifest)


def copy_cached_to_output(cached: Path, output_path: Path) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(cached, output_path)
    return output_path
