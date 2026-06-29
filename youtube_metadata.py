from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, TypeVar, TypedDict, cast

import google.generativeai as genai
from PIL import Image, ImageDraw, ImageFont, ImageOps

from video_pipeline import GEMINI_MODELS, IMAGE_DIR, OUTPUTS, ROOT, Scene, _configure_gemini, _is_quota_error


class ChannelTemplate(TypedDict, total=False):
    channel_name: str
    default_description: str
    default_tags: list[str]
    watermark_text: str
    title_prefix: str
    title_suffix: str


class PublishMetadata(TypedDict, total=False):
    title: str
    description: str
    tags: list[str]
    hashtags: list[str]
    topic: str
    approved: bool
    generated_at: str
    queued_item_id: str


CHANNEL_TEMPLATE_PATH = OUTPUTS / "channel_template.json"
METADATA_PATH = OUTPUTS / "publish_metadata.json"
THUMBNAIL_PATH = OUTPUTS / "thumbnail.jpg"

DEFAULT_CHANNEL_TEMPLATE: ChannelTemplate = {
    "channel_name": "alsawalmeh btec pro",
    "default_description": (
        "قناة alsawalmeh btec pro — دروس تعليمية في BTEC والبرمجة والتقنية.\n"
        "تابعنا للمزيد من الشروحات العملية.\n"
        "━━━━━━━━━━━━━━━━━━\n"
    ),
    "default_tags": [
        "btec",
        "btec arabic",
        "programming",
        "tutorial arabic",
        "alsawalmeh",
    ],
    "watermark_text": "alsawalmeh btec pro",
    "title_prefix": "",
    "title_suffix": " | BTEC Pro",
}


TJson = TypeVar("TJson")


def _read_json(path: Path, fallback: TJson) -> TJson:
    if not path.exists():
        return fallback
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(fallback, dict):
            return cast(TJson, {**fallback, **data})
        return fallback
    except json.JSONDecodeError:
        return fallback


def _write_json(path: Path, data: ChannelTemplate | PublishMetadata | dict[str, Any]) -> None:
    OUTPUTS.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def load_channel_template() -> ChannelTemplate:
    data = _read_json(CHANNEL_TEMPLATE_PATH, DEFAULT_CHANNEL_TEMPLATE)
    tags = data.get("default_tags", DEFAULT_CHANNEL_TEMPLATE["default_tags"])
    if isinstance(tags, str):
        tags = [t.strip() for t in re.split(r"[\n,]+", tags) if t.strip()]
    data["default_tags"] = tags
    return data


def save_channel_template(template: ChannelTemplate) -> ChannelTemplate:
    tags = template.get("default_tags", [])
    if isinstance(tags, str):
        tags = [t.strip() for t in re.split(r"[\n,]+", tags) if t.strip()]
    payload: ChannelTemplate = {
        "channel_name": template.get("channel_name", DEFAULT_CHANNEL_TEMPLATE["channel_name"]).strip(),
        "default_description": template.get("default_description", "").strip(),
        "default_tags": tags,
        "watermark_text": template.get("watermark_text", "").strip(),
        "title_prefix": template.get("title_prefix", "").strip(),
        "title_suffix": template.get("title_suffix", "").strip(),
    }
    _write_json(CHANNEL_TEMPLATE_PATH, payload)
    return payload


def load_publish_metadata() -> PublishMetadata:
    data = _read_json(METADATA_PATH, {})
    for key in ("tags", "hashtags"):
        value = data.get(key, [])
        if isinstance(value, str):
            data[key] = [t.strip() for t in re.split(r"[\n,]+", value) if t.strip()]
    return cast(PublishMetadata, data)


def save_publish_metadata(metadata: PublishMetadata) -> PublishMetadata:
    tags = metadata.get("tags", [])
    hashtags = metadata.get("hashtags", [])
    if isinstance(tags, str):
        tags = [t.strip() for t in re.split(r"[\n,]+", tags) if t.strip()]
    if isinstance(hashtags, str):
        hashtags = [t.strip() for t in re.split(r"[\n,]+", hashtags) if t.strip()]
    payload: PublishMetadata = {
        "title": metadata.get("title", "").strip(),
        "description": metadata.get("description", "").strip(),
        "tags": tags,
        "hashtags": hashtags,
        "topic": metadata.get("topic", "").strip(),
        "approved": metadata.get("approved", False),
        "generated_at": metadata.get("generated_at") or datetime.now().isoformat(timespec="seconds"),
    }
    _write_json(METADATA_PATH, payload)
    return payload


def merge_with_channel_template(metadata: PublishMetadata, template: ChannelTemplate | None = None) -> PublishMetadata:
    template = template or load_channel_template()
    title = metadata.get("title", "").strip()
    prefix = template.get("title_prefix", "")
    suffix = template.get("title_suffix", "")
    if prefix and not title.startswith(prefix.strip()):
        title = f"{prefix}{title}".strip()
    if suffix and suffix not in title:
        title = f"{title}{suffix}".strip()

    body = metadata.get("description", "").strip()
    footer = template.get("default_description", "").strip()
    if footer and footer not in body:
        description = f"{body}\n\n{footer}".strip() if body else footer
    else:
        description = body

    tags = list(metadata.get("tags") or [])
    for tag in template.get("default_tags") or []:
        if tag and tag not in tags:
            tags.append(tag)

    hashtags = list(metadata.get("hashtags") or [])
    hashtag_line = " ".join(h if h.startswith("#") else f"#{h}" for h in hashtags if h)
    if hashtag_line and hashtag_line not in description:
        description = f"{description}\n\n{hashtag_line}".strip()

    return {
        **metadata,
        "title": title,
        "description": description,
        "tags": tags[:30],
        "hashtags": hashtags,
    }


def _scene_summary(scenes: list[Scene]) -> str:
    lines = []
    for idx, scene in enumerate(scenes[:8], start=1):
        narration = scene.get("narration", "").strip()
        if narration:
            lines.append(f"{idx}. {narration}")
    return "\n".join(lines)


def _metadata_local(topic: str, scenes: list[Scene], template: ChannelTemplate) -> PublishMetadata:
    year = datetime.now().year
    title = f"شرح {topic} خطوة بخطوة للمبتدئين {year}"
    summary = _scene_summary(scenes)
    description = (
        f"في هذا الفيديو نتعلم {topic} بشكل عملي ومبسّط.\n\n"
        f"محتوى الفيديو:\n{summary}\n\n"
        f"القناة: {template.get('channel_name', 'alsawalmeh btec pro')}"
    )
    slug = re.sub(r"\s+", " ", topic).strip().lower()
    tags = [
        slug,
        f"{slug} tutorial",
        f"{slug} arabic",
        "btec",
        "tutorial arabic",
    ]
    hashtags = [slug.replace(" ", ""), "btec", "tutorial", "programming"]
    return {
        "title": title,
        "description": description,
        "tags": tags,
        "hashtags": hashtags,
        "topic": topic,
        "approved": False,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
    }


def generate_publish_metadata(
    topic: str,
    scenes: list[Scene],
    log: Callable[[str], None] | None = None,
) -> PublishMetadata:
    def _log(msg: str) -> None:
        if log:
            log(msg)

    template = load_channel_template()
    summary = _scene_summary(scenes)
    prompt = f"""أنت خبير SEO لقناة YouTube تعليمية عربية اسمها "{template.get('channel_name', 'alsawalmeh btec pro')}".

الموضوع: {topic}
ملخص المشاهد:
{summary}

أنشئ بيانات نشر احترافية. أعد JSON فقط:
{{
  "title": "عنوان SEO عربي جذاب (60-90 حرف)",
  "description": "وصف YouTube مفصل بالعربية (3-6 فقرات)",
  "tags": ["كلمة1", "keyword2", "flutter tutorial"],
  "hashtags": ["flutter", "programming", "btec"]
}}"""

    try:
        _configure_gemini()
        for model_name in GEMINI_MODELS:
            try:
                _log(f"📝 توليد بيانات YouTube عبر {model_name}...")
                model = genai.GenerativeModel(model_name)
                response = model.generate_content(prompt)
                raw = (response.text or "").strip()
                match = re.search(r"\{.*\}", raw, re.DOTALL)
                if not match:
                    continue
                data = json.loads(match.group(0))
                metadata: PublishMetadata = {
                    "title": str(data.get("title", "")).strip(),
                    "description": str(data.get("description", "")).strip(),
                    "tags": data.get("tags") or [],
                    "hashtags": data.get("hashtags") or [],
                    "topic": topic,
                    "approved": False,
                    "generated_at": datetime.now().isoformat(timespec="seconds"),
                }
                if metadata["title"]:
                    merged = merge_with_channel_template(metadata, template)
                    save_publish_metadata(merged)
                    _log("✅ تم توليد العنوان والوصف والوسوم")
                    return merged
            except Exception as exc:
                if _is_quota_error(exc):
                    continue
                raise
    except Exception:
        pass

    _log("⚠️ تم توليد بيانات YouTube محلياً (بدون Gemini)")
    merged = merge_with_channel_template(_metadata_local(topic, scenes, template), template)
    save_publish_metadata(merged)
    return merged


def _load_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = [
        ROOT / "resources" / "fonts" / "font.ttf",
        Path("C:/Windows/Fonts/tahoma.ttf"),
        Path("C:/Windows/Fonts/arial.ttf"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    ]
    for path in candidates:
        if path.exists():
            try:
                return ImageFont.truetype(str(path), size)
            except OSError:
                continue
    return ImageFont.load_default()


def _wrap_text(text: str, font: ImageFont.FreeTypeFont | ImageFont.ImageFont, max_width: int) -> list[str]:
    words = text.split()
    if not words:
        return [text]
    lines: list[str] = []
    current = words[0]
    for word in words[1:]:
        trial = f"{current} {word}"
        bbox = font.getbbox(trial)
        if (bbox[2] - bbox[0]) <= max_width:
            current = trial
        else:
            lines.append(current)
            current = word
    lines.append(current)
    return lines


def _background_image(video_path: Path | None, scenes: list[Scene]) -> Image.Image:
    if video_path and video_path.exists():
        try:
            from moviepy.editor import VideoFileClip

            clip = VideoFileClip(str(video_path))
            frame = clip.get_frame(min(1.0, max(0.1, clip.duration / 3)))
            clip.close()
            return Image.fromarray(frame).convert("RGB")
        except Exception:
            pass

    for idx in range(4):
        candidate = IMAGE_DIR / f"part{idx}.png"
        if candidate.exists():
            return Image.open(candidate).convert("RGB")

    for scene in scenes:
        local_file = scene.get("local_file", "").strip()
        if local_file:
            path = ROOT / local_file if not Path(local_file).is_absolute() else Path(local_file)
            if path.exists() and path.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}:
                return Image.open(path).convert("RGB")

    img = Image.new("RGB", (1280, 720), (15, 23, 42))
    return img


def generate_thumbnail(
    topic: str,
    title: str,
    scenes: list[Scene],
    video_path: Path | None = None,
    log: Callable[[str], None] | None = None,
) -> Path:
    def _log(msg: str) -> None:
        if log:
            log(msg)

    _log("🖼️ توليد الصورة المصغرة...")
    template = load_channel_template()
    base = _background_image(video_path, scenes)
    thumb = ImageOps.fit(base, (1280, 720), method=Image.Resampling.LANCZOS)
    overlay = Image.new("RGBA", (1280, 720), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    draw.rectangle((0, 0, 1280, 720), fill=(0, 0, 0, 110))
    draw.rectangle((0, 520, 1280, 720), fill=(15, 23, 42, 220))

    title_font = _load_font(58)
    subtitle_font = _load_font(28)
    watermark_font = _load_font(24)

    display_title = title.strip() or topic.strip() or "فيديو تعليمي"
    lines = _wrap_text(display_title, title_font, 1180)
    y = 540
    for line in lines[:2]:
        bbox = title_font.getbbox(line)
        tw = bbox[2] - bbox[0]
        x = (1280 - tw) // 2
        draw.text((x + 2, y + 2), line, font=title_font, fill=(0, 0, 0, 180))
        draw.text((x, y), line, font=title_font, fill=(255, 255, 255, 255))
        y += (bbox[3] - bbox[1]) + 8

    channel = template.get("channel_name", "").strip()
    if channel:
        draw.text((40, 40), channel, font=subtitle_font, fill=(125, 211, 252, 255))

    watermark = template.get("watermark_text", "").strip() or channel
    if watermark:
        bbox = watermark_font.getbbox(watermark)
        ww = bbox[2] - bbox[0]
        draw.text((1280 - ww - 36, 672), watermark, font=watermark_font, fill=(203, 213, 225, 230))

    composed = Image.alpha_composite(thumb.convert("RGBA"), overlay)
    OUTPUTS.mkdir(parents=True, exist_ok=True)
    composed.convert("RGB").save(THUMBNAIL_PATH, format="JPEG", quality=92)
    _log(f"✅ تم حفظ الصورة المصغرة: {THUMBNAIL_PATH.name}")
    return THUMBNAIL_PATH


def save_custom_thumbnail(content: bytes) -> Path:
    OUTPUTS.mkdir(parents=True, exist_ok=True)
    img = Image.open(__import__("io").BytesIO(content)).convert("RGB")
    fitted = ImageOps.fit(img, (1280, 720), method=Image.Resampling.LANCZOS)
    fitted.save(THUMBNAIL_PATH, format="JPEG", quality=92)
    return THUMBNAIL_PATH


def prepare_publish_package(
    topic: str,
    scenes: list[Scene],
    video_path: Path,
    log: Callable[[str], None],
) -> dict:
    metadata = generate_publish_metadata(topic, scenes, log)
    generate_thumbnail(topic, metadata.get("title", topic), scenes, video_path, log)
    save_publish_metadata({**metadata, "approved": False})
    return {
        "metadata": load_publish_metadata(),
        "thumbnail_ready": THUMBNAIL_PATH.exists(),
        "preview_ready": video_path.exists(),
    }
