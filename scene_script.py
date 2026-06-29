from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from video_pipeline import Scene

SCENE_HEADER_RE = re.compile(
    r"^(?:-{2,}|={2,})?\s*(?:مشهد|scene)\s*(\d+)\s*(?:-{2,}|={2,})?\s*$",
    re.IGNORECASE,
)

FIELD_ALIASES: dict[str, list[str]] = {
    "narration": ["الصوت", "ما يُقال", "ما يقال", "التعليق الصوتي", "التعليق", "narration", "voice", "say"],
    "characters": ["الشخصيات", "characters", "شخصيات"],
    "method": ["الطريقة", "method", "أسلوب العرض", "أسلوب", "style"],
    "voice_style": ["نمط الصوت", "voice_style", "tone", "أسلوب الصوت", "النبرة"],
    "visual": ["prompt بصري", "prompt", "البصري", "visual", "ما يظهر", "visual_prompt", "show"],
    "screen_text": ["نص الشاشة", "screen_text", "title", "العنوان"],
    "duration_sec": ["المدة", "duration", "duration_sec", "مدة المشهد"],
    "media_type": ["نوع الوسائط", "الوسائط", "media_type", "media", "نوع"],
    "local_file": ["ملف", "local_file", "file"],
}

ALIAS_LOOKUP: dict[str, str] = {}
for field, labels in FIELD_ALIASES.items():
    for label in labels:
        ALIAS_LOOKUP[label.strip().lower()] = field


def _normalize_key(raw: str) -> str | None:
    key = raw.strip().lower().replace("_", " ")
    key = re.sub(r"\s+", " ", key)
    return ALIAS_LOOKUP.get(key)


def _split_scene_blocks(text: str) -> list[str]:
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    blocks: list[list[str]] = []
    current: list[str] = []

    for line in lines:
        if SCENE_HEADER_RE.match(line.strip()):
            if current:
                blocks.append(current)
            current = []
            continue
        current.append(line)

    if current:
        blocks.append(current)

    if blocks:
        return ["\n".join(block).strip() for block in blocks if any(l.strip() for l in block)]

    # Fallback: entire text as one block if no headers
    stripped = text.strip()
    return [stripped] if stripped else []


def _parse_block(block: str) -> dict[str, Any]:
    scene: dict[str, Any] = {}
    current_field: str | None = None
    buffer: list[str] = []

    def flush() -> None:
        nonlocal buffer, current_field
        if current_field is None:
            buffer = []
            return
        value = "\n".join(buffer).strip()
        if value:
            scene[current_field] = value
        buffer = []

    for line in block.split("\n"):
        if ":" in line:
            key_part, value_part = line.split(":", 1)
            field = _normalize_key(key_part)
            if field:
                flush()
                current_field = field
                buffer = [value_part.strip()] if value_part.strip() else []
                continue
        if current_field:
            buffer.append(line.rstrip())

    flush()
    return scene


def parse_scenes_full_text(text: str) -> list[dict[str, Any]]:
    text = (text or "").strip()
    if not text:
        raise ValueError("السيناريو فارغ — الصق مشاهدك بالصيغة الموضحة")

    if text.lstrip().startswith("["):
        try:
            data = json.loads(text)
            if isinstance(data, list):
                return [item for item in data if isinstance(item, dict)]
        except json.JSONDecodeError:
            pass

    blocks = _split_scene_blocks(text)
    if not blocks:
        raise ValueError("لم يتم العثور على مشاهد — استخدم --- مشهد 1 --- ثم الحقول")

    scenes: list[dict[str, Any]] = []
    for block in blocks:
        parsed = _parse_block(block)
        if parsed.get("narration") or parsed.get("visual"):
            scenes.append(parsed)

    if not scenes:
        raise ValueError("لم يُستخرج أي مشهد — تأكد من وجود «الصوت:» أو «Prompt بصري:»")

    return scenes


def scenes_to_script_text(scenes: list[Scene]) -> str:
    chunks: list[str] = []
    for idx, scene in enumerate(scenes, start=1):
        lines = [f"--- مشهد {idx} ---"]
        if scene.get("narration"):
            lines.append(f"الصوت: {scene['narration']}")
        if scene.get("characters"):
            lines.append(f"الشخصيات: {scene['characters']}")
        if scene.get("method"):
            lines.append(f"الطريقة: {scene['method']}")
        if scene.get("voice_style"):
            lines.append(f"نمط الصوت: {scene['voice_style']}")
        if scene.get("visual"):
            lines.append(f"Prompt بصري: {scene['visual']}")
        if scene.get("screen_text"):
            lines.append(f"نص الشاشة: {scene['screen_text']}")
        if scene.get("duration_sec"):
            lines.append(f"المدة: {scene['duration_sec']}")
        if scene.get("media_type"):
            lines.append(f"نوع الوسائط: {scene['media_type']}")
        if scene.get("local_file"):
            lines.append(f"ملف: {scene['local_file']}")
        chunks.append("\n".join(lines))
    return "\n\n".join(chunks)


DEFAULT_SCRIPT_TEMPLATE = """--- مشهد 1 ---
الصوت: مرحباً، في هذا الدرس سنتعلم [الموضوع] خطوة بخطوة.
الشخصيات: راوٍ تعليمي / لا شخصيات
الطريقة: مقدمة مباشرة للكاميرا
نمط الصوت: هادئ وواضح
Prompt بصري: cinematic intro related to [الموضوع], educational style
نص الشاشة: [الموضوع]
المدة: 8
نوع الوسائط: ai

--- مشهد 2 ---
الصوت: [اشرح أول خطوة أو أول حدث في الموضوع]
الشخصيات: [من يظهر في المشهد]
الطريقة: [شرح على شاشة / رسوم / لقطة برنامج]
نمط الصوت: [حماسي / جاد / storyteller]
Prompt بصري: [وصف بصري دقيق بالإنجليزية]
نص الشاشة: الخطوة 1
المدة: 10
نوع الوسائط: screen
"""
