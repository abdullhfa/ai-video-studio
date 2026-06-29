from __future__ import annotations

import asyncio
import json
import re
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Any, Callable, TypedDict, cast

import edge_tts  # pyright: ignore[reportMissingImports]
import google.generativeai as _genai  # pyright: ignore[reportMissingImports]
genai: Any = _genai
import numpy as np

from atomic_io import atomic_write_json
import requests  # pyrefly: ignore[untyped-import]
from moviepy.editor import (  # pyright: ignore[reportMissingImports]
    AudioClip,
    AudioFileClip,
    ColorClip,
    CompositeAudioClip,
    CompositeVideoClip,
    ImageClip,
    VideoFileClip,
    concatenate_audioclips,
    concatenate_videoclips,
)


def _loop_media_clip(clip: Any, duration: float) -> Any:
    """MoviePy loop() exists at runtime but is missing from type stubs."""
    return clip.loop(duration=duration)


def _scale_audio_volume(clip: AudioFileClip, volume: float) -> AudioFileClip:
    return cast(Any, clip).volumex(volume)
from PIL import Image, ImageDraw, ImageFont

from scene_script import parse_scenes_full_text, scenes_to_script_text
from arabic_text import format_quran_verse, line_width, load_arabic_font, prepare_arabic, wrap_arabic
from video_duration import (
    CHAPTER_LENGTH_SEC,
    CHAPTER_THRESHOLD_SEC,
    MAX_SCENES,
    assign_media_mix,
    clamp_duration,
    get_mode_info,
    get_video_mode,
    plan_chapters,
    segment_count,
    uses_chapters,
)


class FormatPreset(TypedDict):
    width: int
    height: int
    orientation: str
    filename: str


class Scene(TypedDict, total=False):
    narration: str
    visual: str
    duration_sec: float
    media_type: str
    local_file: str
    screen_text: str
    characters: str
    method: str
    voice_style: str
    search_query: str
    image_url: str
    content_profile: str
    scene_kind: str
    media_source: str
    ai_prompt: str
    router_locked: bool
    presentation: str
    quran_verse: str
    quran_reference: str
    hadith_text: str
    hadith_reference: str
    historical_note: str
    source_type: str
    confidence: int
    character_id: str
    character_ids: list[str]
    audio_duration_sec: float
    is_pivotal: bool
    media_priority: str
    visual_variation_seed: int
    quality_score: float
    face_visibility_score: float
    text_artifact_score: float
    engagement_role: str
    quality_gate: dict[str, Any]
    relevance_score: int
    bypass_image_cache: bool
    event: str
    visual_requirements: list[str]


def _scene_copy(scene: Scene) -> Scene:
    return cast(Scene, dict(cast(dict[str, Any], scene)))


ROOT = Path(__file__).resolve().parent
OUTPUTS = ROOT / "outputs"
AUDIO_DIR = OUTPUTS / "audio"
IMAGE_DIR = OUTPUTS / "images"
VIDEO_DIR = OUTPUTS / "videos"
SCENE_UPLOADS = OUTPUTS / "scenes" / "uploads"
CHAPTERS_DIR = OUTPUTS / "chapters"
API_DIR = ROOT / "api"
FFMPEG = ROOT / "AI_Video_Gen.exe_extracted" / "ffmpeg" / "ffmpeg.exe"
FFPROBE = FFMPEG.parent / "ffprobe.exe"
RENDER_FPS = 24

VOICE_MAP = {
    "أدم": "ar-EG-ShakirNeural",
    "سلمى": "ar-EG-SalmaNeural",
    "حامد": "ar-SA-HamedNeural",
}

NARRATOR_PRESETS: dict[str, dict[str, float | str]] = {
    "هادئ": {"rate_mul": 0.92, "pitch": "-2Hz"},
    "quiet": {"rate_mul": 0.92, "pitch": "-2Hz"},
    "calm": {"rate_mul": 0.92, "pitch": "-2Hz"},
    "وثائقي": {"rate_mul": 1.0, "pitch": "+0Hz"},
    "documentary": {"rate_mul": 1.0, "pitch": "+0Hz"},
    "storyteller": {"rate_mul": 0.97, "pitch": "-1Hz"},
    "مؤثر": {"rate_mul": 0.94, "pitch": "+3Hz"},
    "dramatic": {"rate_mul": 0.94, "pitch": "+3Hz"},
    "حماسي": {"rate_mul": 1.02, "pitch": "+2Hz"},
}

FORMAT_PRESETS: dict[str, FormatPreset] = {
    "short": {"width": 1080, "height": 1920, "orientation": "portrait", "filename": "youtube_short.mp4"},
    "normal": {"width": 1920, "height": 1080, "orientation": "landscape", "filename": "youtube_video.mp4"},
}


def _read_secret(name: str) -> str:
    path = API_DIR / name
    if not path.exists():
        raise FileNotFoundError(f"Missing API key file: {path}")
    return path.read_text(encoding="utf-8").strip()


def _configure_gemini() -> None:
    genai.configure(api_key=_read_secret("gemini_secret.txt"))


def _segment_count(duration_sec: int) -> int:
    return segment_count(duration_sec)


def _clean_output_dirs() -> None:
    for folder in (AUDIO_DIR, IMAGE_DIR, VIDEO_DIR):
        if folder.exists():
            shutil.rmtree(folder)
        folder.mkdir(parents=True, exist_ok=True)
    OUTPUTS.mkdir(parents=True, exist_ok=True)


def _save_script(lines: list[str]) -> None:
    full_text = " ".join(lines).strip()
    (OUTPUTS / "text.txt").write_text(full_text, encoding="utf-8")
    (OUTPUTS / "line_by_line.txt").write_text("\n".join(lines), encoding="utf-8")


def _save_scenes(scenes: list[Scene], *, topic: str = "", settings: dict | None = None) -> None:
    from production_session import get_current_session, stamp_session_fields

    payload: dict[str, Any]
    session = get_current_session()
    if session or topic:
        payload = stamp_session_fields(
            {
                "topic": topic or (session.topic if session else ""),
                "content_profile": (settings or {}).get("content_profile")
                or (session.content_profile if session else "auto"),
                "scenes": scenes,
            }
        )
    else:
        payload = {"scenes": scenes}
    atomic_write_json(OUTPUTS / "scenes.json", payload)
    _save_script([scene.get("narration") or "" for scene in scenes])


def load_scenes_from_disk(topic: str = "") -> list[Scene]:
    path = OUTPUTS / "scenes.json"
    if not path.exists():
        return []
    try:
        from production_session import parse_scenes_document, scenes_meta_is_valid

        raw = json.loads(path.read_text(encoding="utf-8"))
        scenes_raw, meta = parse_scenes_document(raw)
        if not scenes_meta_is_valid(meta, topic):
            return []
        if isinstance(scenes_raw, list):
            return cast(list[Scene], scenes_raw)
    except json.JSONDecodeError:
        pass
    return []


def _normalize_scene(item: dict, default_media: str = "pexels") -> Scene:
    narration = str(item.get("narration") or item.get("say") or "").strip()
    visual = str(item.get("visual") or item.get("visual_prompt") or item.get("show") or "").strip()
    screen_text = str(item.get("screen_text") or item.get("title") or "").strip()
    characters = str(item.get("characters") or item.get("شخصيات") or "").strip()
    method = str(item.get("method") or item.get("الطريقة") or "").strip()
    voice_style = str(item.get("voice_style") or item.get("نمط_الصوت") or "").strip()
    media_type = str(item.get("media_type") or default_media).strip().lower()
    if media_type in {"videos", "video", "pexels"}:
        media_type = "pexels"
    elif media_type in {"images", "image", "ai"}:
        media_type = "ai"
    elif media_type in {"slide", "slides", "text", "text_slide"}:
        media_type = "slide"
    elif media_type in {"screen", "screen_recording", "screenshot", "screencast"}:
        media_type = "screen"
    elif media_type in {"local", "file"}:
        media_type = "local"
    elif media_type not in {"pexels", "ai", "local", "slide", "screen"}:
        media_type = default_media

    duration_raw = item.get("duration_sec") or item.get("duration") or 0
    try:
        duration_sec = max(0, min(120, int(duration_raw)))
    except (TypeError, ValueError):
        duration_sec = 0

    local_file = str(item.get("local_file") or "").strip()
    search_query = str(item.get("search_query") or item.get("pexels_query") or "").strip()
    image_url = str(item.get("image_url") or "").strip()
    ai_prompt = str(item.get("ai_prompt") or "").strip()
    scene_kind = str(item.get("scene_kind") or "").strip()
    media_source = str(item.get("media_source") or "").strip()
    content_profile = str(item.get("content_profile") or "").strip()
    router_locked = bool(item.get("router_locked", False))
    presentation = str(item.get("presentation") or "").strip()
    quran_verse = str(item.get("quran_verse") or "").strip()
    quran_reference = str(item.get("quran_reference") or "").strip()
    hadith_text = str(item.get("hadith_text") or "").strip()
    hadith_reference = str(item.get("hadith_reference") or "").strip()
    if not visual and narration:
        visual = narration

    scene: Scene = {
        "narration": narration,
        "visual": visual,
        "duration_sec": duration_sec,
        "media_type": media_type,
        "local_file": local_file,
        "screen_text": screen_text,
        "characters": characters,
        "method": method,
        "voice_style": voice_style,
    }
    if search_query:
        scene["search_query"] = search_query
    if image_url:
        scene["image_url"] = image_url
    if ai_prompt:
        scene["ai_prompt"] = ai_prompt
    if scene_kind:
        scene["scene_kind"] = scene_kind
    if media_source:
        scene["media_source"] = media_source
    if content_profile:
        scene["content_profile"] = content_profile
    if router_locked:
        scene["router_locked"] = True
    if presentation:
        scene["presentation"] = presentation
    if quran_verse:
        scene["quran_verse"] = quran_verse
    if quran_reference:
        scene["quran_reference"] = quran_reference
    if hadith_text:
        scene["hadith_text"] = hadith_text
    if hadith_reference:
        scene["hadith_reference"] = hadith_reference
    return scene


def scene_visual_prompt(scene: Scene) -> str:
    parts: list[str] = []
    visual = (scene.get("visual") or "").strip()
    if visual:
        parts.append(visual)
    characters = (scene.get("characters") or "").strip()
    if characters and characters.lower() not in {"لا شخصيات", "none", "no characters"}:
        parts.append(f"characters: {characters}")
    method = (scene.get("method") or "").strip()
    if method:
        parts.append(method)
    narration = (scene.get("narration") or "").strip()
    if not parts and narration:
        parts.append(narration[:120])
    return ", ".join(parts)


def _default_media_type(settings: dict) -> str:
    return "pexels" if settings.get("media_source", "images") == "videos" else "ai"


def parse_custom_scenes(raw: str, default_media: str, log: Callable[[str], None]) -> list[Scene]:
    if not (raw or "").strip():
        raise RuntimeError("أضف مشهدًا واحدًا على الأقل: ما يُقال + Prompt بصري")

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        try:
            data = parse_scenes_full_text(raw)
        except ValueError as exc:
            raise RuntimeError("صيغة المشاهd غير صحيحة — JSON أو سيناريو (--- مشهد 1 ---)") from exc

    if not isinstance(data, list) or not data:
        raise RuntimeError("أضف مشهدًا واحدًا على الأقل")

    scenes: list[Scene] = []
    for idx, item in enumerate(data, start=1):
        if not isinstance(item, dict):
            raise RuntimeError(f"المشهد رقم {idx} غير صالح")
        scene = _normalize_scene(item, default_media=default_media)
        if not scene.get("narration"):
            raise RuntimeError(f"المشهد {idx}: أدخل ما يجب قوله")
        if scene.get("media_type") in {"local", "screen"} and not scene.get("local_file"):
            raise RuntimeError(f"المشهد {idx}: ارفع ملفًا محليًا أو لقطة شاشة")
        scenes.append(scene)

    if len(scenes) > MAX_SCENES:
        scenes = scenes[:MAX_SCENES]
        log(f"⚠️ تم استخدام أول {MAX_SCENES} مشهدًا فقط")

    _save_scenes(scenes)
    log(f"✅ تم تحميل {len(scenes)} مشهدًا")
    return scenes


def _topic_category(topic: str) -> str:
    t = topic.lower()
    islamic_markers = (
        "يوسف", "موسى", "محمد", "عيسى", "قرآن", "سيرة", "إسلام", "الانبياء", "الأنبياء",
        "prophet", "quran", "islamic", "sura", "سورة",
    )
    tech_markers = (
        "python", "flutter", "java", "programming", "btec", "تثبيت", "تنزيل", "download",
        "install", "code", "برمجة", "coding", "api", "database",
    )
    if any(m in topic or m in t for m in islamic_markers):
        return "islamic"
    if any(m in topic or m in t for m in tech_markers):
        return "tech"
    return "general"


def _generate_local_scenes_smart(
    topic: str,
    segments: int,
    per_scene: int,
    use_mix: bool,
    total_duration_sec: int,
) -> list[Scene]:
    category = _topic_category(topic)
    if category == "islamic":
        steps = [
            (
                f"في بداية قصة {topic}، نتعرف على السياق والمكان.",
                "ancient middle eastern rocky mountains at sunset, wide cinematic landscape",
                "المقدمة",
                "ai",
                "راوٍ",
                "سرد قصصي",
                "هادئ storyteller",
                "landscape",
            ),
            (
                f"نبدأ بأحداث {topic} كما وردت في القصة.",
                "ancient believers walking through mountains toward refuge, cinematic Islamic historical scene, no faces clearly visible",
                "بداية القصة",
                "ai",
                "رسوم تفسيرية",
                "سرد قصصي",
                "هادئ",
                "historical_event",
            ),
            (
                f"نتابع تطور الأحداث في {topic}.",
                "ancient cave in mountain with warm sunlight entering, sleeping travelers silhouette, cinematic atmosphere",
                "الأحداث",
                "ai",
                "مشاهد تاريخية",
                "سرد مشهدي",
                "جاد",
                "historical_event",
            ),
            (
                f"نركز على الدرس المستفاد من {topic}.",
                "soft golden light rays over ancient landscape, hopeful spiritual atmosphere, Islamic art inspired",
                "العبرة",
                "ai",
                "راوٍ",
                "شريحة معنوية",
                "هادئ",
                "closing_lesson",
            ),
            (
                f"نختتم بملخص {topic} وما تعلمناه.",
                "peaceful desert sunset reflection, cinematic closing scene, respectful tone",
                "الختام",
                "ai",
                "راوٍ",
                "ختام",
                "هادئ",
                "closing_lesson",
            ),
        ]
    elif category == "tech":
        steps = [
            (f"مرحباً، سنتعلم {topic} خطوة بخطوة على جهاز الكمبيوتر.", "computer desktop tutorial setup screen", "Introduction", "screen", "مقدّم", "شرح على الشاشة", "واضح تعليمي"),
            (f"أولاً: نفتح الموقع أو البرنامج الرسمي لـ {topic}.", "browser opening official website download page", "Step 1", "screen", "بدون شخصيات", "لقطة شاشة", "واضح"),
            (f"ثانياً: نحمّل الملفات المطلوبة وننتظر اكتمال التنزيل.", "download progress bar software installer", "Download", "pexels", "بدون شخصيات", "عرض خطوات", "واضح"),
            (f"ثالثاً: نثبت {topic} ونتبع معالج الإعداد.", "software installation wizard next button", "Install", "screen", "بدون شخصيات", "معالج تثبيت", "واضح"),
            (f"رابعاً: نختبر أن {topic} يعمل بنجاح.", "terminal success message green checkmark", "Testing", "ai", "بدون شخصيات", "اختبار عملي", "حماسي خفيف"),
            (f"نصائح مهمة عند استخدام {topic}.", "checklist tips infographic slide", "Tips", "slide", "راوٍ", "شريحة نصية", "هادئ"),
            (f"بهذا نكون أنهينا {topic} بنجاح.", "developer celebrating completed project", "Done", "pexels", "مقدّم", "ختام", "حماسي"),
        ]
    else:
        steps = [
            (f"مرحباً، في هذا الفيديو سنتعلم {topic}.", f"intro cinematic about {topic}", topic[:48], "ai", "راوٍ", "مقدمة", "هادئ"),
            (f"ما المقصود بـ {topic}؟", f"concept diagram explaining {topic}", "Introduction", "slide", "راوٍ", "شرح مفاهيم", "واضح"),
            (f"الخطوة الأولى في {topic}.", f"step one demonstration {topic}", "Step 1", "screen", "مقدّم", "شرح عملي", "واضح"),
            (f"الخطوة الثانية: نتابع {topic}.", f"step two hands on tutorial {topic}", "Step 2", "pexels", "مقدّم", "عرض خطوات", "واضح"),
            (f"نختبر النتيجة ونتأكد من نجاح {topic}.", f"testing results success {topic}", "Testing", "ai", "بدون شخصيات", "اختبار", "حماسي"),
            (f"ملخص ونصائح حول {topic}.", f"summary checklist {topic}", "Summary", "slide", "راوٍ", "ملخص", "هادئ"),
            (f"بهذا نكون أنهينا {topic}.", f"outro positive ending {topic}", "Done", "ai", "راوٍ", "ختام", "هادئ"),
        ]

    scenes: list[Scene] = []
    for idx in range(segments):
        step = steps[idx % len(steps)]
        narration, visual, title, media, characters, method, voice_style = step[:7]
        item: dict = {
            "narration": narration,
            "visual": visual,
            "screen_text": title,
            "duration_sec": per_scene,
            "media_type": media,
            "local_file": "",
            "characters": characters,
            "method": method,
            "voice_style": voice_style,
        }
        if len(step) > 7:
            item["scene_kind"] = step[7]
        if category == "islamic":
            item["content_profile"] = "islamic_story"
            item["ai_prompt"] = (
                f"{visual}, respectful Islamic historical illustration, cinematic, "
                "no visible faces, no modern objects, no text, no watermark"
            )
        scenes.append(item)  # type: ignore[arg-type]
    if use_mix and category != "islamic":
        scenes = assign_media_mix(scenes, total_duration_sec)
    return scenes


def generate_scenes_from_topic(
    topic: str,
    duration_sec: int,
    default_media: str,
    log: Callable[[str], None] | None = None,
    segments_override: int | None = None,
    total_duration_sec: int | None = None,
) -> tuple[list[Scene], str]:
    def _log(msg: str) -> None:
        if log:
            log(msg)

    duration_sec = clamp_duration(duration_sec)
    total_duration_sec = clamp_duration(total_duration_sec or duration_sec)
    segments = segments_override or _segment_count(duration_sec)
    per_scene = max(4, min(20, round(duration_sec / max(segments, 1))))
    use_mix = total_duration_sec >= 600
    media_hint = "pexels أو ai أو slide أو screen" if use_mix else "pexels أو ai"

    prompt = f"""أنت كاتب سيناريو فيديو تعليمي عربي احترافي.

الموضوع: {topic}
المدة: {duration_sec} ثانية — عدد المشاهd: {segments}

قواعد مهمة:
- لا تكرر قالب "معلم في فصل" أو "what is X" إلا إذا كان الموضوع يتطلب ذلك فعلاً
- كل مشهد = حدث أو خطوة محددة من الموضوع (ليس مقدمة عامة فقط)
- إذا الموضوع ديني/قصة أنبياء: استخدم مشاهd narrativе محترمة (قصر، صحراء، رؤيا، سجن، عفو...)
- إذا الموضوع تقني (Python/Flutter/BTEC): مشاهd عملية (موقع، تنزيل، تثبيت، Terminal...)
- visual بالإنجليزية — دقيق وقابل للبحث أو توليد صورة

لكل مشهد أعد:
- narration (الصوت)
- characters (الشخصيات)
- method (الطريقة/أسلوب العرض)
- voice_style (نمط الصوت: هادئ، storyteller، حماسي...)
- visual (Prompt بصري)
- screen_text
- duration_sec (~{per_scene})
- media_type ({media_hint})

JSON فقط:
{{"scenes":[{{"narration":"...","characters":"...","method":"...","voice_style":"...","visual":"...","screen_text":"...","duration_sec":8,"media_type":"ai"}}]}}"""

    try:
        _configure_gemini()
        for model_name in GEMINI_MODELS:
            try:
                _log(f"🤖 توليد المشاهد عبر {model_name}...")
                model = genai.GenerativeModel(model_name)
                response = model.generate_content(prompt)
                raw = (response.text or "").strip()
                match = re.search(r"\{.*\}", raw, re.DOTALL)
                if not match:
                    continue
                data = json.loads(match.group(0))
                items = data.get("scenes") or data.get("lines") or []
                scenes = [_normalize_scene(item, default_media) for item in items if isinstance(item, dict)]
                scenes = [s for s in scenes if s.get("narration")]
                if len(scenes) >= 3:
                    if len(scenes) > segments:
                        scenes = scenes[:segments]
                    if use_mix:
                        scenes = assign_media_mix(scenes, total_duration_sec)
                    _log(f"✅ تم توليد {len(scenes)} مشهدًا عبر Gemini")
                    return scenes, "gemini"
            except Exception as exc:
                if _is_quota_error(exc):
                    continue
                raise
    except Exception:
        pass

    _log("⚠️ Gemini غير متاح — تم توليد مشاهd محلية ذكية حسب نوع الموضوع")
    scenes = _generate_local_scenes_smart(topic, segments, per_scene, use_mix, total_duration_sec)
    return scenes, "local"

def _split_scenes_for_chapter(all_scenes: list[Scene], chapter_index: int, chapter_count: int) -> list[Scene]:
    if chapter_count <= 1:
        return all_scenes
    chunk = max(1, len(all_scenes) // chapter_count)
    start = (chapter_index - 1) * chunk
    end = len(all_scenes) if chapter_index == chapter_count else start + chunk
    return all_scenes[start:end]


def resolve_chapter_scenes(
    topic: str,
    chapter: dict,
    settings: dict,
    log: Callable[[str], None],
) -> list[Scene]:
    script_source = settings.get("script_source", "auto")
    default_media = _default_media_type(settings)
    total_duration = clamp_duration(int(settings.get("video_duration_sec", 60)))
    chapter_duration = clamp_duration(int(chapter.get("duration_sec", total_duration)))
    chapter_title = str(chapter.get("title") or topic)
    chapters_plan = plan_chapters(topic, total_duration)

    if script_source == "scenes":
        raw = settings.get("custom_scenes") or ""
        try:
            data = json.loads(raw)
            if isinstance(data, list) and data:
                all_scenes = [_normalize_scene(item, default_media) for item in data if isinstance(item, dict)]
                scenes = _split_scenes_for_chapter(all_scenes, int(chapter["index"]), len(chapters_plan))
                if scenes:
                    return scenes
        except json.JSONDecodeError:
            pass
        if int(chapter["index"]) == 1:
            return parse_custom_scenes(raw, default_media, log)
        return []

    if script_source == "manual" and int(chapter["index"]) == 1:
        lines = write_script(topic, chapter_duration, settings, log)
        scenes = [
            _normalize_scene({"narration": line, "visual": line, "media_type": default_media}, default_media)
            for line in lines
        ]
    else:
        scenes, _source = generate_scenes_from_topic(
            chapter_title,
            chapter_duration,
            default_media,
            log,
            segments_override=int(chapter.get("scene_target") or _segment_count(chapter_duration)),
            total_duration_sec=total_duration,
        )

    if total_duration >= 600:
        scenes = assign_media_mix(scenes, total_duration)
    return scenes


def _finalize_scenes(
    scenes: list[Scene],
    topic: str,
    settings: dict,
    duration_sec: int,
    log: Callable[[str], None],
) -> list[Scene]:
    from content_profiles import detect_content_profile, profile_label_ar, is_islamic_story_profile
    from media_router import route_scenes_media
    from story_reference import load_story_reference_for_topic, story_display_name

    profile = detect_content_profile(topic, settings)
    log(f"📂 نوع المحتوى: {profile_label_ar(profile)}")
    if is_islamic_story_profile(profile):
        story_ref = load_story_reference_for_topic(topic)
        log(f"📖 تحميل مرجع القصة: {story_display_name(topic, story_ref)}")
        if settings.get("include_quran", True):
            log("🕌 إدراج آيات مرتبطة بالقصة")
        log(f"🎨 visual_style={settings.get('visual_style', 'cinematic_islamic')}")
    scenes = route_scenes_media(scenes, topic, settings)
    if profile != "islamic_story" and duration_sec >= 600:
        scenes = assign_media_mix(scenes, duration_sec)
    return scenes


def resolve_scenes(topic: str, duration_sec: int, settings: dict, log: Callable[[str], None]) -> list[Scene]:
    from topic_consistency import log_pipeline_context, topic_script_conflict

    settings = dict(settings)
    script_source = str(settings.get("script_source", "auto"))
    default_media = _default_media_type(settings)
    duration_sec = clamp_duration(duration_sec)
    log_pipeline_context(topic, settings, log)

    conflict, topic_profile, script_profile = topic_script_conflict(topic, settings)
    if script_source in {"scenes", "scenes_script"}:
        from content_profiles import is_islamic_story_profile
        from story_engagement import script_has_stale_visuals

        if is_islamic_story_profile(topic_profile) and script_has_stale_visuals(topic, settings):
            log("⚠️ السيناريو المحفوظ يحتوي أوصافاً بصرية قديمة (مثل rocky mountains)")
            log("🔄 سيتم استبدالها تلقائياً — أو اختر script_source=auto لسيناريو جديد")
    if conflict and script_source in {"scenes", "scenes_script"}:
        from content_profiles import profile_label_ar

        log(
            f"⚠️ تعارض: الموضوع «{topic.strip()}» ({profile_label_ar(topic_profile)}) "
            f"≠ السيناريو المحفوظ ({profile_label_ar(script_profile or 'general')})"
        )
        log("🔄 تجاهل السيناريو القديم — إعادة البحث من الموضوع الحالي")
        script_source = "auto"
        settings["script_source"] = "auto"

    if script_source == "scenes":
        scenes = parse_custom_scenes(settings.get("custom_scenes") or "", default_media, log)
        from topic_research import prepare_scenes_for_media

        scenes = prepare_scenes_for_media(scenes, default_media, topic)
        scenes = _finalize_scenes(scenes, topic, settings, duration_sec, log)
        _save_scenes(scenes, topic=topic, settings=settings)
        return scenes
    if script_source == "scenes_script":
        script_text = (settings.get("custom_scenes_script") or "").strip()
        if not script_text:
            raise RuntimeError("الصق السيناريو النصي الكامل في خانة «سيناريو المشاهd»")
        parsed = parse_scenes_full_text(script_text)
        scenes = [_normalize_scene(item, default_media) for item in parsed]
        from topic_research import prepare_scenes_for_media

        scenes = prepare_scenes_for_media(scenes, default_media, topic)
        scenes = _finalize_scenes(scenes, topic, settings, duration_sec, log)
        _save_scenes(scenes, topic=topic, settings=settings)
        log(f"✅ تم تحميل {len(scenes)} مشهدًا من السيناريو النصي")
        return scenes

    from production_session import save_research_bundle, set_research_source
    from topic_research import research_for_topic

    scenes, meta = research_for_topic(topic, duration_sec, default_media, settings, log)
    set_research_source(str(meta.get("source") or ""))
    save_research_bundle(OUTPUTS / "research.json", topic, {**meta, "script_source": settings.get("script_source")}, scenes)
    scenes = _finalize_scenes(scenes, topic, settings, duration_sec, log)
    _save_scenes(scenes, topic=topic, settings=settings)
    return scenes


GEMINI_MODELS = (
    "gemini-2.0-flash-lite",
    "gemini-2.0-flash",
    "gemini-1.5-flash",
    "gemini-1.5-flash-8b",
)


def _is_quota_error(exc: BaseException) -> bool:
    message = str(exc).lower()
    return any(token in message for token in ("429", "quota", "rate limit", "resource exhausted"))


def _parse_script_lines(raw: str, segments: int) -> list[str]:
    lines = [line.strip() for line in re.split(r"[\n\r]+", raw) if line.strip()]
    if len(lines) < 3:
        raise RuntimeError("النص قصير جدًا — أدخل 3 جمل على الأقل (سطر لكل جملة)")
    if len(lines) > segments:
        lines = lines[:segments]
    return lines


def write_script_local(topic: str, segments: int, log: Callable[[str], None]) -> list[str]:
    templates = [
        f"مرحبًا بك، في هذا الفيديو سنتعرّف على {topic} خطوة بخطوة.",
        "قبل البدء، تأكد من اتصالك بالإنترنت وتوفر مساحة كافية على جهازك.",
        f"أول خطوة: ابحث عن المصدر الرسمي أو الطريقة الموثوقة لـ {topic}.",
        "اتبع التعليمات الظاهرة على الشاشة بدقة ودون تخطّي أي مرحلة.",
        "انتظر حتى يكتمل التحميل أو التثبيت ولا تغلق النافذة قبل الانتهاء.",
        "بعد اكتمال العملية، تحقق من نجاحها عبر فتح التطبيق أو تنفيذ أمر الاختبار.",
        "إذا ظهرت رسالة خطأ، راجع المتطلبات الأساسية ثم أعد المحاولة.",
        "تأكد من تحديث الأدوات إلى آخر إصدار مستقر قبل المتابعة.",
        "احفظ الملفات في مجلد واضح يسهل الوصول إليه لاحقًا.",
        "يمكنك ضبط الإعدادات الافتراضية لتسريع العمل في المرات القادمة.",
        "راجع إعدادات النظام مثل الصلاحيات أو متغيرات البيئة إذا لزم الأمر.",
        "اختبر النتيجة على مشروع بسيط قبل الانتقال للمشاريع الكبيرة.",
        "إذا واجهت بطءًا، جرّب إيقاف التطبيقات غير الضرورية في الخلفية.",
        "استخدم الوثائق الرسمية كمرجع عند أي خطوة غير واضحة.",
        f"بهذه الخطوات تكون قد أتممت {topic} بنجاح.",
        "لا تنسَ مشاركة الفيديو إن كان مفيدًا والاشتراك للمزيد.",
        "شكرًا لمتابعتك، وإلى اللقاء في فيديو جديد.",
        "تذكّر دائمًا استخدام مصادر موثوقة عند تنزيل أي برنامج.",
        "يمكنك إعادة مشاهدة أي خطوة وإيقاف الفيديو عند الحاجة.",
        "الآن أنت جاهز للبدء في التطبيق العملي بنفسك.",
        "هذا كل ما تحتاجه للانطلاق بسرعة وأمان.",
        "بالتوفيق في رحلتك التعليمية القادمة.",
        "نراك في الشرح التالي مع موضوع جديد ومفيد.",
        "إلى اللقاء.",
    ]
    lines: list[str] = []
    idx = 0
    while len(lines) < segments:
        lines.append(templates[idx % len(templates)])
        idx += 1
    log("⚠️ تم استخدام نص محلي (بدون Gemini) بسبب مشكلة في مفتاح API أو الحصة")
    return lines[:segments]


def write_script_gemini(topic: str, duration_sec: int, segments: int, log: Callable[[str], None]) -> list[str]:
    _configure_gemini()
    prompt = f"""اكتب نصًا عربيًا للتعليق الصوتي لفيديو تعليمي عن: {topic}

المدة المستهدفة عند القراءة بصوت طبيعي: حوالي {duration_sec} ثانية.
قسّم النص إلى {segments} جمل قصيرة وواضحة (جملة واحدة لكل مقطع).
لا تستخدم ترقيمًا معقدًا أو رموزًا غير ضرورية.

أعد النتيجة فقط كـ JSON بهذا الشكل:
{{"lines": ["الجملة الأولى", "الجملة الثانية"]}}"""

    last_error: Exception | None = None
    for model_name in GEMINI_MODELS:
        try:
            log(f"🤖 محاولة Gemini ({model_name})...")
            model = genai.GenerativeModel(model_name)
            response = model.generate_content(prompt)
            raw = (response.text or "").strip()
            match = re.search(r"\{.*\}", raw, re.DOTALL)
            if not match:
                raise RuntimeError("تعذر قراءة النص من Gemini")

            data = json.loads(match.group(0))
            lines = [str(line).strip() for line in data.get("lines", []) if str(line).strip()]
            if len(lines) < 3:
                raise RuntimeError("النص المولّد قصير جدًا")
            if len(lines) > segments:
                lines = lines[:segments]
            log(f"✅ تم إنشاء {len(lines)} جمل عبر Gemini")
            return lines
        except Exception as exc:
            last_error = exc
            if _is_quota_error(exc):
                log(f"⚠️ {model_name}: تجاوز الحصة — تجربة نموذج آخر...")
                continue
            raise

    if last_error and _is_quota_error(last_error):
        raise RuntimeError("gemini_quota")
    if last_error:
        raise last_error
    raise RuntimeError("تعذر توليد النص عبر Gemini")


def write_script(topic: str, duration_sec: int, settings: dict, log: Callable[[str], None]) -> list[str]:
    segments = _segment_count(duration_sec)
    script_source = settings.get("script_source", "auto")
    custom_script = (settings.get("custom_script") or "").strip()

    log(f"📝 كتابة النص لمدة ~{duration_sec} ثانية ({segments} مقاطع)...")

    if script_source == "manual":
        if not custom_script:
            raise RuntimeError("فعّلت «نص يدوي» — الصق النص في خانة النص المخصص (سطر لكل جملة)")
        lines = _parse_script_lines(custom_script, segments)
        _save_script(lines)
        log(f"✅ تم استخدام نص يدوي ({len(lines)} جمل)")
        return lines

    if script_source == "local":
        lines = write_script_local(topic, segments, log)
        _save_script(lines)
        log(f"✅ تم إنشاء {len(lines)} جمل (نص محلي)")
        return lines

    try:
        lines = write_script_gemini(topic, duration_sec, segments, log)
    except RuntimeError as exc:
        if str(exc) != "gemini_quota" and script_source == "gemini":
            raise RuntimeError(
                "انتهت حصة Gemini API. راجع https://ai.google.dev/gemini-api/docs/rate-limits "
                "أو غيّر مفتاح api/gemini_secret.txt"
            ) from exc
        if str(exc) == "gemini_quota" or _is_quota_error(exc):
            log("⚠️ حصة Gemini منتهية — التحويل إلى نص محلي تلقائيًا...")
            lines = write_script_local(topic, segments, log)
        else:
            raise
    except Exception as exc:
        if script_source == "gemini":
            raise
        if _is_quota_error(exc):
            log("⚠️ حصة Gemini منتهية — التحويل إلى نص محلي تلقائيًا...")
            lines = write_script_local(topic, segments, log)
        else:
            log(f"⚠️ فشل Gemini ({exc}) — التحويل إلى نص محلي...")
            lines = write_script_local(topic, segments, log)

    _save_script(lines)
    return lines


async def _synthesize_line(
    text: str,
    voice: str,
    out_path: Path,
    *,
    rate: str = "+0%",
    pitch: str = "+0Hz",
) -> None:
    communicate = edge_tts.Communicate(text, voice, rate=rate, pitch=pitch)
    await communicate.save(str(out_path))


def _resolve_narrator_preset(style: str) -> dict[str, float | str]:
    key = (style or "وثائقي").strip().lower()
    for token in (key, key.replace(" ", "")):
        if token in NARRATOR_PRESETS:
            return NARRATOR_PRESETS[token]
    for name, preset in NARRATOR_PRESETS.items():
        if name in key or key in name:
            return preset
    return NARRATOR_PRESETS["وثائقي"]


def tts_rate_string(settings: dict, voice_style: str = "") -> str:
    base_speed = float(settings.get("tts_speed", 1.0) or 1.0)
    preset = _resolve_narrator_preset(voice_style or str(settings.get("narrator_style") or "وثائقي"))
    rate_mul = float(preset.get("rate_mul", 1.0))  # type: ignore[arg-type]
    speed = base_speed * rate_mul
    pct = round((speed - 1.0) * 100)
    pct = max(-50, min(50, pct))
    return f"{pct:+d}%"


def tts_pitch_string(settings: dict, voice_style: str = "") -> str:
    preset = _resolve_narrator_preset(voice_style or str(settings.get("narrator_style") or "وثائقي"))
    return str(preset.get("pitch", "+0Hz"))


async def generate_voices(
    lines: list[str],
    voice_name: str,
    log: Callable[[str], None],
    settings: dict | None = None,
    voice_styles: list[str] | None = None,
) -> list[Path]:
    settings = settings or {}
    voice = VOICE_MAP.get(voice_name, VOICE_MAP["أدم"])
    narrator = settings.get("narrator_style", "وثائقي")
    speed = settings.get("tts_speed", 1.0)
    log(f"🎙️ توليد الصوت ({voice_name} — نمط {narrator} — سرعة {speed})...")
    paths: list[Path] = []
    for idx, line in enumerate(lines):
        style = voice_styles[idx] if voice_styles and idx < len(voice_styles) else ""
        rate = tts_rate_string(settings, style)
        pitch = tts_pitch_string(settings, style)
        out_path = AUDIO_DIR / f"part{idx}.mp3"
        await _synthesize_line(line, voice, out_path, rate=rate, pitch=pitch)
        paths.append(out_path)
    log(f"✅ تم توليد {len(paths)} ملفات صوت")
    return paths


async def generate_scene_voices(
    scenes: list[Scene],
    settings: dict,
    log: Callable[[str], None],
    topic: str = "",
) -> list[Path]:
    from scene_cache import (
        cache_enabled,
        copy_cached_to_output,
        get_cached_audio,
        save_cached_audio,
        scene_fingerprint,
    )

    voice_name = settings.get("voice_name", "أدم")
    voice = VOICE_MAP.get(voice_name, VOICE_MAP["أدم"])
    narrator = settings.get("narrator_style", "وثائقي")
    speed = settings.get("tts_speed", 1.0)
    log(f"🎙️ توليد الصوت ({voice_name} — نمط {narrator} — سرعة {speed})...")
    paths: list[Path] = []
    use_cache = cache_enabled(settings) and bool(topic.strip())

    for idx, scene in enumerate(scenes):
        out_path = AUDIO_DIR / f"part{idx}.mp3"
        if use_cache:
            fp = scene_fingerprint(scene, topic, settings)
            cached = get_cached_audio(topic, fp)
            if cached:
                copy_cached_to_output(cached, out_path)
                paths.append(out_path)
                log(f"  ♻️ مشهد {idx + 1}: صوت من الذاكرة")
                try:
                    from production_report import active_tracker

                    t = active_tracker()
                    if t:
                        t.record_cache_hit("audio")
                except ImportError:
                    pass
                continue

        try:
            from production_report import active_tracker

            t = active_tracker()
            if t:
                t.record_cache_miss()
        except ImportError:
            pass

        style = scene.get("voice_style") or ""
        rate = tts_rate_string(settings, style)
        pitch = tts_pitch_string(settings, style)
        await _synthesize_line(scene.get("narration") or "", voice, out_path, rate=rate, pitch=pitch)
        paths.append(out_path)
        try:
            from production_report import active_tracker

            t = active_tracker()
            if t:
                t.record_audio_generated()
        except ImportError:
            pass
        if use_cache:
            save_cached_audio(topic, scene_fingerprint(scene, topic, settings), out_path)

    log(f"✅ تم توليد {len(paths)} ملفات صوت")
    return paths


def _download_image(url: str, out_path: Path) -> None:
    response = requests.get(url, timeout=120)
    response.raise_for_status()
    out_path.write_bytes(response.content)


MAX_IMAGE_EDGE = 1920


def _prepare_image_file(path: Path, *, max_edge: int = MAX_IMAGE_EDGE) -> Path:
    """Ensure MoviePy gets a valid RGB image, downscaled for fast encoding."""
    prepared = path.with_suffix(".png")
    with Image.open(path) as img:
        rgb = img.convert("RGB")
        if max(rgb.width, rgb.height) > max_edge:
            rgb.thumbnail((max_edge, max_edge), Image.Resampling.LANCZOS)
        rgb.save(prepared, format="PNG")
    return prepared


def _short_error(exc: BaseException, limit: int = 120) -> str:
    text = str(exc).strip() or exc.__class__.__name__
    text = re.sub(r"\s+", " ", text)
    return text[:limit] + ("..." if len(text) > limit else "")


def fetch_one_pexels_photo(
    visual: str,
    topic: str,
    idx: int,
    orientation: str,
    width: int,
    height: int,
    scene: Scene | None = None,
    settings: dict | None = None,
    log: Callable[[str], None] | None = None,
) -> Path:
    from content_profiles import detect_content_profile
    from image_quality import evaluate_image_quality, passes_quality

    settings = settings or {}
    api_key = _read_secret("pexels_secret.txt")
    query = _visual_search_query(visual, topic, scene)
    headers = {"Authorization": api_key}
    results: list[dict] = []
    fallback_queries = [query, _visual_search_query(topic, topic, scene)]
    if detect_content_profile(topic, settings) != "islamic_story":
        fallback_queries.append(f"{topic} software tutorial")
    for search_query in fallback_queries:
        response = requests.get(
            "https://api.pexels.com/v1/search",
            headers=headers,
            params={"query": search_query, "orientation": orientation, "per_page": 15},
            timeout=60,
        )
        response.raise_for_status()
        batch = response.json().get("photos", [])
        results.extend(batch)
        if batch:
            break
    if not results:
        raise RuntimeError(f"لم يتم العثور على صورة Pexels: {query}")

    ranked = sorted(results, key=lambda item: _pexels_relevance_score(item, query, topic), reverse=True)
    max_text = float(settings.get("islamic_max_text_score", 0.30) or 0.30)
    min_quality = float(settings.get("image_min_quality", 0.45) or 0.45)
    max_face = float(settings.get("image_max_face_score", 0.38) or 0.38)
    last_path: Path | None = None

    for photo_idx, best in enumerate(ranked[:6]):
        src = best.get("src") or {}
        url = src.get("large2x") or src.get("large") or src.get("original")
        if not url:
            continue
        raw_path = IMAGE_DIR / f"pexels_{idx}_raw_{photo_idx}.jpg"
        _download_image(url, raw_path)
        out_path = _prepare_image_file(raw_path)
        last_path = out_path
        scores = evaluate_image_quality(out_path)
        if passes_quality(scores, min_quality=min_quality, max_face=max_face, max_text=max_text):
            if log and photo_idx > 0:
                log(f"  • Pexels: صورة بديلة #{photo_idx + 1} مقبولة")
            return out_path
        if log:
            log(
                f"  ⚠️ Pexels #{photo_idx + 1}: نص/جودة ضعيفة "
                f"(text={scores.get('text_artifact_score')}) — تجربة أخرى"
            )
    if last_path is None:
        raise RuntimeError("رابط صورة Pexels غير متاح")
    return last_path


def _imagerouter_model(settings: dict[str, Any] | None = None) -> str:
    settings = settings or {}
    model = str(settings.get("imagerouter_model") or "black-forest-labs/FLUX-1-schnell").strip()
    if model.lower() in {"test/test", "test", ""}:
        model = "black-forest-labs/FLUX-1-schnell"
    return model


def _imagerouter_generate_url(prompt: str, width: int, height: int, settings: dict[str, Any] | None = None) -> str:
    api_key = _read_secret("imagerouter_secret.txt")
    model = _imagerouter_model(settings)
    response = requests.post(
        "https://api.imagerouter.io/v1/openai/images/generations",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={
            "prompt": prompt,
            "model": model,
            "size": f"{width}x{height}",
            "response_format": "url",
            "output_format": "webp",
        },
        timeout=180,
    )
    response.raise_for_status()
    payload = response.json()
    return str(payload["data"][0]["url"])


def generate_single_ai_image(
    prompt: str,
    topic: str,
    idx: int,
    width: int,
    height: int,
    scene: Scene | None = None,
    settings: dict | None = None,
    log: Callable[[str], None] | None = None,
) -> Path:
    from image_quality import (
        evaluate_image_quality,
        passes_quality,
        regeneration_prompt_suffix,
    )
    from scene_cache import (
        cache_enabled,
        copy_cached_to_output,
        get_cached_image,
        save_cached_image,
        scene_fingerprint,
    )
    from visual_variation import append_visual_variation

    settings = settings or {}
    raw_seed = (scene or {}).get("visual_variation_seed")
    if isinstance(raw_seed, int) and raw_seed != 0:
        seed = raw_seed
    else:
        seed = idx + 1
    max_retries = max(0, min(4, int(settings.get("image_quality_retries", 2) or 2)))
    min_quality = float(settings.get("image_min_quality", 0.45) or 0.45)
    max_face = float(settings.get("image_max_face_score", 0.38) or 0.38)
    max_text = float(settings.get("image_max_text_score", 0.42) or 0.42)

    if (
        cache_enabled(settings)
        and scene
        and topic.strip()
        and not scene.get("bypass_image_cache")
    ):
        fp = scene_fingerprint(scene, topic, settings)
        cached = get_cached_image(topic, fp)
        if cached:
            out_path = IMAGE_DIR / f"part{idx}.png"
            copied = copy_cached_to_output(cached, out_path)
            from scene_quality_gate import validate_scene_visual, log_scene_quality_block

            cache_report = validate_scene_visual(copied, scene, topic, settings)
            if cache_report.get("ok"):
                if log:
                    log(f"  ♻️ مشهد {idx + 1}: صورة AI من الذاكرة (مقبولة)")
                    log_scene_quality_block(idx, cache_report, settings, log)
                try:
                    from production_report import active_tracker

                    t = active_tracker()
                    if t:
                        t.record_cache_hit("image")
                except ImportError:
                    pass
                return copied
            if log:
                log(
                    f"  ♻️ مشهد {idx + 1}: Cache قديم مرفوض "
                    f"({cache_report.get('fail_reason') or 'quality'}) — توليد جديد"
                )

    try:
        from production_report import active_tracker

        t = active_tracker()
        if t:
            t.record_cache_miss()
    except ImportError:
        pass

    base_prompt = (prompt or "").strip() or topic
    last_path: Path | None = None
    scores: dict[str, Any] = {}
    image_recorded = False

    for attempt in range(max_retries + 1):
        trial_prompt = base_prompt
        if attempt > 0:
            trial_prompt = append_visual_variation(base_prompt, seed, attempt=attempt)
            trial_prompt = f"{trial_prompt}, {regeneration_prompt_suffix(attempt)}"
        if attempt == 0 and log:
            log(f"  🎨 ImageRouter Model: {_imagerouter_model(settings)}")
        try:
            url = _imagerouter_generate_url(trial_prompt, width, height, settings)
        except requests.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else "?"
            raise RuntimeError(f"ImageRouter {status}: {exc.response.reason if exc.response else exc}") from exc
        except (requests.RequestException, KeyError, IndexError, TypeError) as exc:
            raise RuntimeError(f"ImageRouter: {_short_error(exc)}") from exc

        raw_path = IMAGE_DIR / f"part{idx}_raw_{attempt}"
        _download_image(url, raw_path)
        out_path = _prepare_image_file(raw_path)
        last_path = out_path

        scores = evaluate_image_quality(out_path)
        if scene is not None:
            scene["quality_score"] = scores["quality_score"]
            scene["face_visibility_score"] = scores["face_visibility_score"]
            scene["text_artifact_score"] = scores["text_artifact_score"]

        if passes_quality(
            scores,
            min_quality=min_quality,
            max_face=max_face,
            max_text=max_text,
        ):
            if log and attempt > 0:
                log(f"  ✅ مشهد {idx + 1}: صورة مقبولة بعد {attempt + 1} محاولة")
            try:
                from production_report import active_tracker

                t = active_tracker()
                if t:
                    t.record_image_generated(
                        regenerated=attempt > 0,
                        quality_score=float(scores.get("quality_score") or 0),
                    )
            except ImportError:
                pass
            image_recorded = True
            break
        if log:
            log(
                f"  ⚠️ مشهد {idx + 1}: جودة ضعيفة "
                f"(q={scores['quality_score']}, face={scores['face_visibility_score']}, "
                f"text={scores['text_artifact_score']}) — إعادة توليد"
            )
    if last_path is None:
        raise RuntimeError("فشل توليد الصورة")

    if not image_recorded:
        try:
            from production_report import active_tracker

            t = active_tracker()
            if t:
                t.record_image_generated(
                    regenerated=max_retries > 0,
                    quality_score=float(scores.get("quality_score") or 0),
                )
        except ImportError:
            pass

    if cache_enabled(settings) and scene and topic.strip():
        save_cached_image(
            topic,
            scene_fingerprint(scene, topic, settings),
            last_path,
            meta=scores,
        )
    return last_path


def generate_images(
    visual_queries: list[str],
    topic: str,
    width: int,
    height: int,
    log: Callable[[str], None],
    prompts: list[str] | None = None,
    settings: dict | None = None,
    scenes: list[Scene] | None = None,
) -> list[Path]:
    log("🖼️ توليد الصور بالذكاء الاصطناعي...")
    paths: list[Path] = []
    for idx, visual in enumerate(visual_queries):
        if prompts and idx < len(prompts) and prompts[idx]:
            prompt = prompts[idx]
        else:
            prompt = (
                f"Cinematic educational illustration: {visual}. "
                f"Video topic: {topic}. "
                f"No text, no watermark, high quality, suitable for {width}x{height} video."
            )
        scene = scenes[idx] if scenes and idx < len(scenes) else None
        paths.append(
            generate_single_ai_image(
                prompt,
                topic,
                idx,
                width,
                height,
                scene=scene,
                settings=settings,
                log=log,
            )
        )
        log(f"  • صورة {idx + 1}/{len(visual_queries)}")
    log(f"✅ تم توليد {len(paths)} صورة")
    return paths


def _visual_search_query(visual: str, topic: str, scene: Scene | None = None) -> str:
    if scene:
        search_query = scene.get("search_query")
        if search_query:
            return search_query[:100]
    query = re.sub(r"[^\w\s\u0600-\u06FF]", " ", visual).strip()
    topic_clean = re.sub(r"[^\w\s\u0600-\u06FF]", " ", topic).strip()
    if re.search(r"[\u0600-\u06FF]", topic_clean):
        topic_clean = ""
    if topic_clean and topic_clean.lower() not in query.lower():
        query = f"{topic_clean} {query}".strip()
    if len(query) < 4:
        query = topic_clean or re.sub(r"[^\w\s\u0600-\u06FF]", " ", topic).strip()
    return (query[:100] or "education tutorial computer").strip()


def _query_tokens(text: str) -> set[str]:
    return {t.lower() for t in re.findall(r"[a-z0-9]{3,}", text.lower())}


def _pexels_relevance_score(item: dict, query: str, topic: str) -> int:
    haystack = " ".join(
        str(item.get(key) or "")
        for key in ("alt", "url")
    ).lower()
    if item.get("user"):
        haystack += " " + str(item["user"].get("name") or "").lower()
    for vf in item.get("video_files") or []:
        haystack += f" {vf.get('quality', '')}"
    tokens = _query_tokens(f"{query} {topic}")
    if not tokens:
        return 0
    return sum(2 if token in haystack else 0 for token in tokens)


def _pick_pexels_file(video_item: dict, target_w: int, target_h: int) -> str:
    files = video_item.get("video_files", [])
    if not files:
        raise RuntimeError("لا توجد ملفات فيديو في Pexels")

    def score(item: dict) -> tuple[int, int]:
        w = item.get("width") or 0
        h = item.get("height") or 0
        return (abs(w - target_w) + abs(h - target_h), -(item.get("height") or 0))

    files = sorted(files, key=score)
    return files[0]["link"]


def search_videos(
    visual_queries: list[str],
    topic: str,
    orientation: str,
    target_w: int,
    target_h: int,
    log: Callable[[str], None],
    scenes: list[Scene] | None = None,
) -> list[Path]:
    api_key = _read_secret("pexels_secret.txt")
    log("🎬 جلب فيديوهات من Pexels...")
    headers = {"Authorization": api_key}
    paths: list[Path] = []

    for idx, visual in enumerate(visual_queries):
        scene = scenes[idx] if scenes and idx < len(scenes) else None
        query = _visual_search_query(visual, topic, scene)
        results: list[dict] = []
        for search_query in (query, _visual_search_query(topic, topic, scene), f"{topic} coding tutorial screen"):
            response = requests.get(
                "https://api.pexels.com/videos/search",
                headers=headers,
                params={"query": search_query, "orientation": orientation, "per_page": 15},
                timeout=60,
            )
            response.raise_for_status()
            batch = response.json().get("videos", [])
            results.extend(batch)
            if batch:
                break
        if not results:
            raise RuntimeError(f"لم يتم العثور على فيديو للمشهد: {visual}")

        best = max(results, key=lambda item: _pexels_relevance_score(item, query, topic))
        url = _pick_pexels_file(best, target_w, target_h)
        out_path = VIDEO_DIR / f"part{idx}.mp4"
        video_response = requests.get(url, timeout=180)
        video_response.raise_for_status()
        out_path.write_bytes(video_response.content)
        paths.append(out_path)
        log(f"  • فيديو {idx + 1}/{len(visual_queries)} — بحث: {query}")

    log(f"✅ تم جلب {len(paths)} فيديو")
    return paths


def fetch_one_ai_image(
    visual: str,
    topic: str,
    idx: int,
    width: int,
    height: int,
    scene: Scene | None = None,
    settings: dict | None = None,
    log: Callable[[str], None] | None = None,
) -> Path:
    prompt = (scene.get("ai_prompt") or visual) if scene else visual
    return generate_single_ai_image(
        prompt,
        topic,
        idx,
        width,
        height,
        scene=scene,
        settings=settings,
        log=log,
    )


def _scene_slide_fallback(scene: Scene, visual: str, width: int, height: int, idx: int) -> Path:
    return _create_text_slide(
        scene.get("screen_text", "") or visual,
        scene.get("narration", ""),
        width,
        height,
        idx,
    )


def _fetch_ai_visual_with_fallback(
    scene: Scene,
    visual: str,
    topic: str,
    idx: int,
    preset: FormatPreset,
    log: Callable[[str], None],
    settings: dict | None = None,
) -> Path:
    width = preset["width"]
    height = preset["height"]
    orientation = preset["orientation"]

    try:
        return fetch_one_ai_image(visual, topic, idx, width, height, scene, settings, log)
    except Exception as exc:
        log(f"  ⚠️ مشهد {idx + 1}: AI غير متاح ({_short_error(exc)})")

    islamic_locked = scene.get("router_locked") and scene.get("content_profile") == "islamic_story"

    try:
        log(f"  • مشهد {idx + 1}: بديل — صورة Pexels")
        return fetch_one_pexels_photo(visual, topic, idx, orientation, width, height, scene, settings, log)
    except Exception as exc:
        log(f"  ⚠️ مشهد {idx + 1}: Pexels صورة ({_short_error(exc)})")

    if islamic_locked:
        log(f"  • مشهد {idx + 1}: بديل — شريحة نصية")
        return _scene_slide_fallback(scene, visual, width, height, idx)

    try:
        log(f"  • مشهد {idx + 1}: بديل — فيديو Pexels")
        return fetch_one_pexels_video(visual, topic, idx, orientation, width, height, scene)
    except Exception as exc:
        log(f"  ⚠️ مشهد {idx + 1}: Pexels فيديو ({_short_error(exc)})")

    log(f"  • مشهد {idx + 1}: بديل — شريحة نصية")
    return _scene_slide_fallback(scene, visual, width, height, idx)


def fetch_one_pexels_video(
    visual: str,
    topic: str,
    idx: int,
    orientation: str,
    target_w: int,
    target_h: int,
    scene: Scene | None = None,
) -> Path:
    paths = search_videos([visual], topic, orientation, target_w, target_h, lambda _msg: None, [scene] if scene else None)
    return paths[0]


def _try_download_scene_url(scene: Scene, idx: int, width: int, height: int) -> Path | None:
    url = (scene.get("image_url") or "").strip()
    if not url or not url.startswith(("http://", "https://")):
        return None
    raw_path = IMAGE_DIR / f"web_{idx}_raw"
    _download_image(url, raw_path)
    return _prepare_image_file(raw_path)


def resolve_local_media_path(local_file: str) -> Path:
    raw = local_file.strip().strip('"')
    candidates = [
        Path(raw),
        ROOT / raw,
        SCENE_UPLOADS / raw,
        OUTPUTS / raw,
    ]
    for candidate in candidates:
        if candidate.exists() and candidate.is_file():
            return candidate
    raise RuntimeError(f"الملف المحلي غير موجود: {local_file}")


def _slide_font_override() -> str | None:
    try:
        settings_path = OUTPUTS / "settings.json"
        if settings_path.exists():
            data = json.loads(settings_path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return str(data.get("arabic_font") or data.get("font") or "").strip() or None
    except (json.JSONDecodeError, OSError):
        pass
    return None


def _pipeline_font(size: int, role: str = "body") -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    from arabic_text import FontRole

    valid: FontRole = role if role in {"body", "title", "quran", "caption"} else "body"  # type: ignore[assignment]
    return load_arabic_font(size, valid, _slide_font_override())


def _wrap_lines(text: str, font: ImageFont.FreeTypeFont | ImageFont.ImageFont, max_width: int) -> list[str]:
    return wrap_arabic(text, font, max_width)


def _create_text_slide(
    title: str,
    body: str,
    width: int,
    height: int,
    idx: int,
) -> Path:
    IMAGE_DIR.mkdir(parents=True, exist_ok=True)
    img = Image.new("RGB", (width, height), (15, 23, 42))
    draw = ImageDraw.Draw(img)
    title_font = _pipeline_font(max(36, width // 28), "title")
    body_font = _pipeline_font(max(24, width // 42), "body")

    display_title = prepare_arabic((title or "شرح تعليمي").strip())
    display_body = prepare_arabic((body or "").strip())
    y = height // 8
    for line in _wrap_lines(display_title, title_font, width - 120):
        bbox = title_font.getbbox(line)
        tw = bbox[2] - bbox[0]
        draw.text(((width - tw) // 2, y), line, font=title_font, fill=(125, 211, 252))
        y += (bbox[3] - bbox[1]) + 12

    y += 20
    for line in _wrap_lines(display_body, body_font, width - 160)[:8]:
        bbox = body_font.getbbox(line)
        draw.text((80, y), line, font=body_font, fill=(226, 232, 240))
        y += (bbox[3] - bbox[1]) + 10

    out_path = IMAGE_DIR / f"slide_{idx}.png"
    img.save(out_path, format="PNG")
    return out_path


def _create_quran_verse_slide(
    scene: Scene,
    width: int,
    height: int,
    idx: int,
) -> Path:
    IMAGE_DIR.mkdir(parents=True, exist_ok=True)
    verse_raw = (scene.get("quran_verse") or scene.get("narration") or "").strip()
    reference = (scene.get("quran_reference") or "").strip()
    hadith = (scene.get("hadith_text") or "").strip()
    hadith_ref = (scene.get("hadith_reference") or "").strip()

    top = (24, 38, 58)
    bottom = (8, 18, 32)
    img = Image.new("RGB", (width, height), bottom)
    draw = ImageDraw.Draw(img)
    for y in range(height):
        blend = y / max(height - 1, 1)
        color = tuple(int(top[i] * (1 - blend) + bottom[i] * blend) for i in range(3))
        draw.line([(0, y), (width, y)], fill=color)

    margin_x = max(48, width // 14)
    frame_top = height // 6
    frame_bottom = height - height // 6
    draw.rectangle(
        (margin_x, frame_top, width - margin_x, frame_bottom),
        outline=(180, 150, 80),
        width=4,
    )
    draw.rectangle(
        (margin_x + 8, frame_top + 8, width - margin_x - 8, frame_bottom - 8),
        outline=(120, 100, 60),
        width=1,
    )

    label_font = _pipeline_font(max(24, width // 44), "caption")
    verse_font = _pipeline_font(max(42, width // 22), "quran")
    ref_font = _pipeline_font(max(24, width // 40), "body")
    hadith_font = _pipeline_font(max(22, width // 48), "body")

    label = prepare_arabic((scene.get("screen_text") or "آية قرآنية").strip())
    bbox = label_font.getbbox(label)
    draw.text(((width - (bbox[2] - bbox[0])) // 2, frame_top - 36), label, font=label_font, fill=(125, 211, 252))

    if verse_raw:
        if line_width(verse_font, verse_raw) <= width - margin_x * 4:
            display_lines = [format_quran_verse(verse_raw)]
        else:
            display_lines = wrap_arabic(verse_raw, verse_font, width - margin_x * 4, max_lines=6)
        total_h = sum(
            (verse_font.getbbox(line)[3] - verse_font.getbbox(line)[1]) + 18 for line in display_lines
        )
        y = frame_top + max(30, (frame_bottom - frame_top - total_h) // 2)
        for display in display_lines:
            bbox = verse_font.getbbox(display)
            tw = bbox[2] - bbox[0]
            th = bbox[3] - bbox[1]
            draw.text(((width - tw) // 2, y), display, font=verse_font, fill=(252, 248, 236))
            y += th + 18

    if hadith:
        y = frame_bottom - 120
        for line in wrap_arabic(hadith, hadith_font, width - margin_x * 4, max_lines=2):
            display = prepare_arabic(line)
            bbox = hadith_font.getbbox(display)
            tw = bbox[2] - bbox[0]
            draw.text(((width - tw) // 2, y), display, font=hadith_font, fill=(200, 210, 220))
            y += (bbox[3] - bbox[1]) + 8
        if hadith_ref:
            display_ref = prepare_arabic(hadith_ref)
            bbox = ref_font.getbbox(display_ref)
            tw = bbox[2] - bbox[0]
            draw.text(((width - tw) // 2, y + 4), display_ref, font=ref_font, fill=(170, 150, 110))

    if reference:
        display_ref = prepare_arabic(reference)
        bbox = ref_font.getbbox(display_ref)
        tw = bbox[2] - bbox[0]
        draw.text(
            ((width - tw) // 2, frame_bottom + 18),
            display_ref,
            font=ref_font,
            fill=(212, 175, 95),
        )

    out_path = IMAGE_DIR / f"quran_{idx}.png"
    img.save(out_path, format="PNG")
    return out_path


def _create_map_slide(scene: Scene, topic: str, width: int, height: int, idx: int) -> Path:
    IMAGE_DIR.mkdir(parents=True, exist_ok=True)
    title = prepare_arabic((scene.get("screen_text") or "الموقع").strip())
    body = (scene.get("narration") or topic).strip()
    img = Image.new("RGB", (width, height), (12, 24, 38))
    draw = ImageDraw.Draw(img)
    draw.rectangle((width // 8, height // 6, width - width // 8, height - height // 6), fill=(20, 36, 56), outline=(100, 160, 200), width=2)
    title_font = _pipeline_font(max(36, width // 30), "title")
    body_font = _pipeline_font(max(24, width // 42), "body")

    bbox = title_font.getbbox(title)
    draw.text(((width - (bbox[2] - bbox[0])) // 2, height // 6 + 20), title, font=title_font, fill=(125, 211, 252))
    y = height // 3
    for line in _wrap_lines(body, body_font, width - width // 4)[:5]:
        draw.text((width // 8 + 30, y), line, font=body_font, fill=(226, 232, 240))
        y += 36

    out_path = IMAGE_DIR / f"map_{idx}.png"
    img.save(out_path, format="PNG")
    return out_path


def _ken_burns_image_clip(path: Path, width: int, height: int, duration: float, mode: str):
    max_edge = int(max(width, height) * 1.25)
    prepared = str(_prepare_image_file(path, max_edge=max_edge))
    base = ImageClip(prepared).set_duration(duration)
    bg = ColorClip(size=(width, height), color=(12, 18, 30)).set_duration(duration)

    if mode == "ken_burns_pan":
        wide = base.resize(width=int(width * 1.25))
        if wide.h < height:
            wide = wide.resize(height=height)
        max_x = max(0, wide.w - width)

        def pan_pos(t: float):
            progress = min(1.0, t / max(duration, 0.01))
            return (-int(max_x * progress), "center")

        moving = wide.set_position(pan_pos)
    else:
        # Pre-scale once; animate position only (avoid per-frame resize — very slow in MoviePy).
        zoom_end = 1.12
        big = base.resize(zoom_end)
        extra_x = max(0, big.w - width)
        extra_y = max(0, big.h - height)

        def zoom_pos(t: float):
            progress = min(1.0, t / max(duration, 0.01))
            return (-int(extra_x * progress / 2), -int(extra_y * progress / 2))

        moving = big.set_position(zoom_pos)

    return CompositeVideoClip([bg, moving], size=(width, height)).set_duration(duration)


def _resolve_cached_slide(
    scene: Scene,
    topic: str,
    settings: dict | None,
    idx: int,
    output_path: Path,
    create_fn: Callable[[], Path],
    log: Callable[[str], None],
    label: str,
) -> Path:
    from scene_cache import (
        cache_enabled,
        copy_cached_to_output,
        get_cached_image,
        save_cached_image,
        scene_fingerprint,
    )

    if cache_enabled(settings) and topic.strip():
        fp = scene_fingerprint(scene, topic, settings or {})
        cached = get_cached_image(topic, fp)
        if cached:
            log(f"  ♻️ مشهد {idx + 1}: {label} من الذاكرة")
            try:
                from production_report import active_tracker

                t = active_tracker()
                if t:
                    t.record_cache_hit("image")
            except ImportError:
                pass
            return copy_cached_to_output(cached, output_path)
    try:
        from production_report import active_tracker

        t = active_tracker()
        if t:
            t.record_cache_miss()
    except ImportError:
        pass
    path = create_fn()
    if cache_enabled(settings) and topic.strip():
        save_cached_image(topic, scene_fingerprint(scene, topic, settings or {}), path)
    return path


def fetch_scene_visual(
    scene: Scene,
    idx: int,
    topic: str,
    preset: FormatPreset,
    log: Callable[[str], None],
    settings: dict | None = None,
) -> Path:
    media_type = scene.get("media_type", "pexels")
    media_source = (scene.get("media_source") or "").strip()
    presentation = (scene.get("presentation") or "static").strip().lower()
    visual = scene_visual_prompt(scene) or scene.get("narration") or topic
    width = preset["width"]
    height = preset["height"]
    orientation = preset["orientation"]

    if presentation == "quran_text" or media_source == "quran_slide":
        log(f"  • مشهد {idx + 1}: شريحة آية قرآنية")
        out = IMAGE_DIR / f"quran_{idx}.png"
        return _resolve_cached_slide(
            scene,
            topic,
            settings,
            idx,
            out,
            lambda: _create_quran_verse_slide(scene, width, height, idx),
            log,
            "شريحة آية",
        )

    if presentation == "map_slide" or media_source == "map_slide":
        map_prompt = (
            f"Historical map illustration about {topic}, ancient middle east style, "
            "no text labels, cinematic, muted colors, no watermark"
        )
        map_scene: Scene = {**scene, "ai_prompt": map_prompt, "media_type": "ai"}
        try:
            log(f"  • مشهد {idx + 1}: خريطة AI")
            return fetch_one_ai_image(map_prompt, topic, idx, width, height, map_scene, settings, log)
        except Exception as exc:
            log(f"  ⚠️ مشهد {idx + 1}: خريطة ({_short_error(exc)}) — شريحة")
            out = IMAGE_DIR / f"map_{idx}.png"
            return _resolve_cached_slide(
                scene,
                topic,
                settings,
                idx,
                out,
                lambda: _create_map_slide(scene, topic, width, height, idx),
                log,
                "شريحة خريطة",
            )

    try:
        web_path = _try_download_scene_url(scene, idx, width, height)
        if web_path:
            log(f"  • مشهد {idx + 1}: صورة من الإنترنت")
            return web_path
    except Exception as exc:
        log(f"  ⚠️ مشهد {idx + 1}: فشل تحميل رابط ({_short_error(exc)})")

    if media_source == "pexels_photo":
        log(f"  • مشهد {idx + 1}: صورة Pexels — {_visual_search_query(visual, topic, scene)}")
        return fetch_one_pexels_photo(visual, topic, idx, orientation, width, height, scene, settings, log)
    if media_source == "ai_image":
        log(f"  • مشهد {idx + 1}: صورة AI مخصصة")
        return _fetch_ai_visual_with_fallback(scene, visual, topic, idx, preset, log, settings)
    if media_source == "web_image":
        try:
            web_path = _try_download_scene_url(scene, idx, width, height)
            if web_path:
                log(f"  • مشهد {idx + 1}: صورة من الويب")
                return web_path
        except Exception as exc:
            log(f"  ⚠️ مشهد {idx + 1}: فشل تحميل رابط ({_short_error(exc)})")
        return _fetch_ai_visual_with_fallback(scene, visual, topic, idx, preset, log, settings)

    if media_type == "local":
        path = resolve_local_media_path(scene.get("local_file", ""))
        if path.suffix.lower() in {".pdf", ".ppt", ".pptx"}:
            log(f"  • مشهد {idx + 1}: مستند → شريحة")
            return _create_text_slide(scene.get("screen_text", "") or path.stem, scene.get("narration", ""), width, height, idx)
        log(f"  • مشهد {idx + 1}: ملف محلي — {path.name}")
        return path
    if media_type == "screen":
        local_file = scene.get("local_file", "")
        if local_file:
            path = resolve_local_media_path(local_file)
            if path.suffix.lower() in {".pdf", ".ppt", ".pptx"}:
                log(f"  • مشهد {idx + 1}: Screen Recording (مستند) → شريحة")
                return _create_text_slide(scene.get("screen_text", "") or path.stem, scene.get("narration", ""), width, height, idx)
            log(f"  • مشهد {idx + 1}: Screen Recording — {path.name}")
            return path
        log(f"  • مشهد {idx + 1}: لا ملف شاشة — جلب من الإنترنت")
        try:
            return fetch_one_pexels_video(visual, topic, idx, orientation, width, height, scene)
        except Exception:
            return _fetch_ai_visual_with_fallback(scene, visual, topic, idx, preset, log, settings)
    if media_type == "slide":
        log(f"  • مشهد {idx + 1}: شريحة نصية")
        return _create_text_slide(
            scene.get("screen_text", "") or visual,
            scene.get("narration", ""),
            width,
            height,
            idx,
        )
    if media_type == "ai":
        log(f"  • مشهد {idx + 1}: صورة AI")
        return _fetch_ai_visual_with_fallback(scene, visual, topic, idx, preset, log, settings)
    try:
        log(f"  • مشهد {idx + 1}: Pexels — {_visual_search_query(visual, topic, scene)}")
        return fetch_one_pexels_video(visual, topic, idx, orientation, width, height, scene)
    except Exception as exc:
        log(f"  ⚠️ مشهد {idx + 1}: Pexels ({_short_error(exc)}) — شريحة نصية")
        return _scene_slide_fallback(scene, visual, width, height, idx)


def fetch_all_scene_visuals(
    scenes: list[Scene],
    topic: str,
    preset: FormatPreset,
    log: Callable[[str], None],
    settings: dict | None = None,
) -> list[Path]:
    from scene_quality_gate import log_scene_quality_block, run_pre_compose_gate, validate_scene_visual

    settings = settings or {}
    gate_enabled = settings.get("quality_gate_enabled", True)
    max_rounds = max(1, int(settings.get("visual_quality_retries", 2) or 2) + 1)
    log("🎬 تجهيز الوسائط لكل مشهد...")
    log(f"🎨 ImageRouter Model: {_imagerouter_model(settings)}")
    paths: list[Path] = []
    for idx, scene in enumerate(scenes):
        path = fetch_scene_visual(scene, idx, topic, preset, log, settings)
        if gate_enabled:
            for attempt in range(max_rounds):
                report = validate_scene_visual(path, scene, topic, settings)
                if report.get("ok"):
                    log_scene_quality_block(idx, report, settings, log)
                    break
                reason = report.get("fail_reason") or "quality"
                log_scene_quality_block(idx, report, settings, log)
                log(f"  ↻ إعادة الوسائط ({attempt + 1}/{max_rounds}) — السبب: {reason}")
                retry_scene = _scene_copy(scene)
                if reason in {"text_artifact", "low_quality", "face_visible"}:
                    retry_scene["media_source"] = "ai_image"
                    retry_scene["media_type"] = "ai"
                    retry_scene["bypass_image_cache"] = True
                    base_prompt = retry_scene.get("ai_prompt") or retry_scene.get("visual") or topic
                    retry_scene["ai_prompt"] = (
                        f"{base_prompt}, no text, no letters, no watermark, no words, no captions"
                    )
                elif reason == "low_relevance":
                    from story_db import enrich_scene_from_story_db, event_entry_for_index, load_story_db

                    db = load_story_db(topic)
                    if db:
                        entry = event_entry_for_index(db, idx, len(scenes))
                        if entry:
                            retry_scene = enrich_scene_from_story_db(
                                retry_scene, idx, topic, len(scenes)
                            )
                            retry_scene["bypass_image_cache"] = True
                        else:
                            db = None
                    if not db:
                        from scene_relevance import NARRATION_VISUAL_SIGNALS
                        from story_reference import load_story_reference_for_topic

                        ref = load_story_reference_for_topic(topic)
                        events = ref.get("key_events") or []
                        narration = scene.get("narration") or ""
                        if events:
                            event = events[idx % len(events)]
                            for n_markers, v_markers in NARRATION_VISUAL_SIGNALS:
                                if any(m in narration for m in n_markers):
                                    hints = ", ".join(v_markers[:4])
                                    retry_scene["visual"] = (
                                        f"Islamic historical scene: {event}. Must show: {hints}. "
                                        "Silhouettes only, no clear faces, cinematic golden light, no text"
                                    )
                                    break
                            else:
                                retry_scene["visual"] = (
                                    f"Islamic historical scene about {event}, ancient desert, silhouettes only, "
                                    "no clear faces, cinematic golden light, no text"
                                )
                            retry_scene["ai_prompt"] = retry_scene["visual"]
                    retry_scene["media_source"] = "ai_image"
                    retry_scene["media_type"] = "ai"
                    retry_scene["bypass_image_cache"] = True
                path = fetch_scene_visual(retry_scene, idx, topic, preset, log, settings)
        paths.append(path)
    if gate_enabled:
        run_pre_compose_gate(scenes, paths, topic, settings, log, log_each_scene=False)
    else:
        log(f"✅ تم تجهيز {len(paths)} وسيط")
        return paths
    log(f"✅ تم تجهيز {len(paths)} وسيط")
    return paths


def _fit_audio_duration(audio: AudioFileClip, target: float) -> AudioFileClip:
    """Pad audio with silence to target; never truncate narration."""
    if target <= 0:
        return audio
    if audio.duration < target - 0.05:
        silence = AudioClip(lambda t: [0, 0], duration=target - audio.duration, fps=audio.fps)
        return concatenate_audioclips([audio, silence])
    return audio


def _screen_text_overlay(text: str, width: int, height: int, duration: float, settings: dict):
    if not text.strip():
        return None

    fontsize = max(28, int(settings.get("fontsize", 72)) // 2)
    bar_height = min(180, max(90, fontsize + 50))

    img = Image.new("RGBA", (width, bar_height), (0, 0, 0, 170))
    draw = ImageDraw.Draw(img)
    font = load_arabic_font(fontsize, "caption", settings.get("arabic_font") or settings.get("font"))
    display = prepare_arabic(text.strip())
    bbox = draw.textbbox((0, 0), display, font=font)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    x = max(20, (width - tw) // 2)
    y = max(10, (bar_height - th) // 2)
    draw.text((x, y), display, font=font, fill=settings.get("fontcolor", "#FFFFFF"))

    overlay = ImageClip(np.array(img)).set_duration(duration)
    return overlay.set_position(("center", height - bar_height - 30))


def _render_screen_text_png(text: str, width: int, settings: dict) -> tuple[Path, int] | None:
    """Render caption bar to PNG for FFmpeg overlay."""
    if not text.strip():
        return None

    fontsize = max(28, int(settings.get("fontsize", 72)) // 2)
    bar_height = min(180, max(90, fontsize + 50))
    img = Image.new("RGBA", (width, bar_height), (0, 0, 0, 170))
    draw = ImageDraw.Draw(img)
    font = load_arabic_font(fontsize, "caption", settings.get("arabic_font") or settings.get("font"))
    display = prepare_arabic(text.strip())
    bbox = draw.textbbox((0, 0), display, font=font)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    x = max(20, (width - tw) // 2)
    y = max(10, (bar_height - th) // 2)
    draw.text((x, y), display, font=font, fill=settings.get("fontcolor", "#FFFFFF"))

    out_path = VIDEO_DIR / f"caption_{abs(hash(text)) % 10_000_000}.png"
    img.save(out_path, format="PNG")
    return out_path, bar_height


def _ffmpeg_run(cmd: list[str], *, label: str = "") -> None:
    result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if result.returncode != 0:
        tail = (result.stderr or result.stdout or "").strip()[-600:]
        raise RuntimeError(f"FFmpeg failed ({label}): {tail}")


def _probe_media_duration(path: Path) -> float:
    if not FFPROBE.exists():
        return 0.0
    cmd = [
        str(FFPROBE),
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if result.returncode != 0:
        return 0.0
    try:
        return max(0.0, float(result.stdout.strip()))
    except ValueError:
        return 0.0


def _is_image_visual(path: Path) -> bool:
    return path.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"} or path.name.endswith("_raw")


def _ffmpeg_video_filter(
    width: int,
    height: int,
    duration: float,
    presentation: str,
    *,
    is_image: bool,
) -> str:
    fps = RENDER_FPS
    cover = f"scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height},setsar=1"
    if is_image and presentation in {"ken_burns_zoom", "ken_burns_pan"}:
        frames = max(2, int(duration * fps))
        if presentation == "ken_burns_pan":
            pan_w = int(width * 1.25)
            max_x = max(1, pan_w - width)
            return (
                f"scale={pan_w}:-2,{cover},"
                f"zoompan=z='1':x='(on-1)*{max_x}/{frames}':y='(ih-oh)/2':"
                f"d={frames}:s={width}x{height}:fps={fps},format=yuv420p"
            )
        return (
            "scale=8000:-1,"
            f"zoompan=z='min(zoom+0.0012,1.12)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
            f"d={frames}:s={width}x{height}:fps={fps},format=yuv420p"
        )
    return f"{cover},fps={fps},format=yuv420p"


def _compose_scene_segment_ffmpeg(
    scene: Scene,
    audio_path: Path,
    visual_path: Path,
    width: int,
    height: int,
    clip_duration: float,
    settings: dict,
    segment_path: Path,
) -> None:
    segment_path.parent.mkdir(parents=True, exist_ok=True)
    is_image = _is_image_visual(visual_path)
    prepared = _prepare_image_file(visual_path) if is_image else visual_path
    presentation = ((scene.get("presentation") if scene else None) or "static").lower()
    vf = _ffmpeg_video_filter(width, height, clip_duration, presentation, is_image=is_image)
    caption = _render_screen_text_png(scene.get("screen_text", ""), width, settings)

    cmd: list[str] = [str(FFMPEG), "-y"]
    if is_image:
        cmd.extend(["-loop", "1", "-framerate", str(RENDER_FPS), "-i", str(prepared)])
    else:
        cmd.extend(["-i", str(prepared)])
    cmd.extend(["-i", str(audio_path)])

    if caption:
        caption_path, bar_height = caption
        y_pos = max(0, height - bar_height - 30)
        cmd.extend(["-loop", "1", "-i", str(caption_path)])
        filter_complex = (
            f"[0:v]{vf}[base];"
            f"[2:v]format=rgba,colorchannelmixer=aa=1[cap];"
            f"[base][cap]overlay=(W-w)/2:{y_pos}:shortest=1[vout]"
        )
        cmd.extend(["-filter_complex", filter_complex, "-map", "[vout]", "-map", "1:a"])
    else:
        cmd.extend(["-vf", vf, "-map", "0:v", "-map", "1:a"])

    cmd.extend(
        [
            "-t",
            f"{clip_duration:.3f}",
            "-af",
            f"apad=whole_dur={clip_duration:.3f}",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "23",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-movflags",
            "+faststart",
            str(segment_path),
        ]
    )
    _ffmpeg_run(cmd, label=f"scene segment {segment_path.name}")


def _compose_video_ffmpeg(
    scenes: list[Scene],
    audio_paths: list[Path],
    visual_paths: list[Path],
    settings: dict,
    log: Callable[[str], None],
    output_path: Path,
) -> Path:
    import time

    from scene_timing import SCENE_TAIL_PADDING_SEC

    preset = FORMAT_PRESETS[settings.get("video_format", "short")]
    width = preset["width"]
    height = preset["height"]
    segments_dir = VIDEO_DIR / "segments"
    segments_dir.mkdir(parents=True, exist_ok=True)
    for old in segments_dir.glob("seg_*.mp4"):
        old.unlink(missing_ok=True)

    log(f"🎞️ تجميع FFmpeg ({width}x{height}) — أسرع من MoviePy...")
    segment_paths: list[Path] = []

    for idx, (scene, audio_path, visual_path) in enumerate(zip(scenes, audio_paths, visual_paths)):
        audio_sec = _probe_media_duration(audio_path)
        clip_duration = float(scene.get("duration_sec") or 0)
        if clip_duration <= 0:
            clip_duration = audio_sec + SCENE_TAIL_PADDING_SEC
        elif clip_duration < audio_sec + 0.15:
            clip_duration = audio_sec + SCENE_TAIL_PADDING_SEC

        segment_path = segments_dir / f"seg_{idx:02d}.mp4"
        _compose_scene_segment_ffmpeg(
            scene, audio_path, visual_path, width, height, clip_duration, settings, segment_path
        )
        segment_paths.append(segment_path)

    concat_list = segments_dir / "concat_list.txt"
    concat_list.write_text(
        "\n".join(f"file '{p.resolve().as_posix()}'" for p in segment_paths),
        encoding="utf-8",
    )
    merged_path = segments_dir / "merged_no_music.mp4"
    _ffmpeg_run(
        [
            str(FFMPEG),
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat_list),
            "-c",
            "copy",
            str(merged_path),
        ],
        label="concat segments",
    )

    target_duration = int(settings.get("video_duration_sec", 60))
    trim_long = settings.get("trim_to_target_duration", False)
    music_enabled = settings.get("music_enabled", False)
    music_file = settings.get("music_file", "music.mp3")
    music_path = ROOT / "resources" / music_file
    output_path.parent.mkdir(parents=True, exist_ok=True)

    merged_duration = _probe_media_duration(merged_path)
    if trim_long and merged_duration > target_duration + 1:
        log(f"⚠️ قص الفيديو من {int(merged_duration)}s إلى {target_duration}s (الهدف)")
        trimmed = segments_dir / "merged_trimmed.mp4"
        _ffmpeg_run(
            [
                str(FFMPEG),
                "-y",
                "-i",
                str(merged_path),
                "-t",
                str(target_duration),
                "-c",
                "copy",
                str(trimmed),
            ],
            label="trim duration",
        )
        merged_path = trimmed
        merged_duration = _probe_media_duration(merged_path)

    if music_enabled and music_path.exists() and merged_duration > 0:
        volume = float(settings.get("music_volume", 0.3))
        _ffmpeg_run(
            [
                str(FFMPEG),
                "-y",
                "-i",
                str(merged_path),
                "-stream_loop",
                "-1",
                "-i",
                str(music_path),
                "-filter_complex",
                f"[1:a]volume={volume}[music];[0:a][music]amix=inputs=2:duration=first:dropout_transition=2[aout]",
                "-map",
                "0:v",
                "-map",
                "[aout]",
                "-c:v",
                "copy",
                "-c:a",
                "aac",
                "-b:a",
                "192k",
                "-t",
                f"{merged_duration:.3f}",
                "-movflags",
                "+faststart",
                str(output_path),
            ],
            label="mix music",
        )
    else:
        shutil.copy2(merged_path, output_path)

    log(f"✅ تم حفظ الفيديو: {output_path.name}")
    return output_path


def _fit_clip(clip, width: int, height: int):
    # Cover-fit: scale up so the frame is fully filled, then center-crop.
    if clip.w / clip.h >= width / height:
        clip = clip.resize(height=height)
    else:
        clip = clip.resize(width=width)
    x_center = clip.w / 2
    y_center = clip.h / 2
    return clip.crop(x_center=x_center, y_center=y_center, width=width, height=height)


def _visual_clip(path: Path, width: int, height: int, duration: float, scene: Scene | None = None):
    presentation = ((scene.get("presentation") if scene else None) or "static").lower()
    is_image = path.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"} or path.name.endswith("_raw")
    if is_image and presentation in {"ken_burns_zoom", "ken_burns_pan"}:
        return _ken_burns_image_clip(path, width, height, duration, presentation)
    if path.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"} or path.name.endswith("_raw"):
        clip = ImageClip(str(_prepare_image_file(path))).set_duration(duration)
    else:
        clip = VideoFileClip(str(path))
        if clip.duration > duration:
            clip = clip.subclip(0, duration)
        elif clip.duration < duration:
            clip = _loop_media_clip(clip, duration)
    fitted = _fit_clip(clip, width, height)
    if fitted.w != width or fitted.h != height:
        raise RuntimeError(f"تعذر ضبط المقطع {path.name} إلى {width}x{height} (النتيجة: {fitted.size})")
    return fitted.set_duration(duration)


def compose_video(
    scenes: list[Scene],
    audio_paths: list[Path],
    visual_paths: list[Path],
    settings: dict,
    log: Callable[[str], None],
    output_path: Path | None = None,
) -> Path:
    import time

    preset = FORMAT_PRESETS[settings.get("video_format", "short")]
    width = preset["width"]
    height = preset["height"]
    if output_path is None:
        output_path = OUTPUTS / preset["filename"]

    render_engine = str(settings.get("render_engine", "ffmpeg")).lower()
    use_ffmpeg = render_engine != "moviepy" and FFMPEG.exists()
    if use_ffmpeg:
        import time

        log(f"⏳ ترميز FFmpeg (~{sum(float(s.get('duration_sec') or 0) for s in scenes):.0f}s)...")
        stop_heartbeat = threading.Event()
        render_started = time.monotonic()

        def _encoding_heartbeat() -> None:
            elapsed = 0
            while not stop_heartbeat.wait(15):
                elapsed += 15
                log(f"  ⏳ جاري ترميز FFmpeg... {elapsed}s")

        heartbeat = threading.Thread(target=_encoding_heartbeat, daemon=True)
        heartbeat.start()
        try:
            result = _compose_video_ffmpeg(
                scenes, audio_paths, visual_paths, settings, log, output_path
            )
        except Exception as exc:
            log(f"⚠️ FFmpeg فشل ({_short_error(exc)}) — fallback إلى MoviePy...")
        else:
            stop_heartbeat.set()
            render_elapsed = time.monotonic() - render_started
            try:
                from production_report import active_tracker

                tracker = active_tracker()
                if tracker is not None:
                    tracker.record_phase_time("render", render_elapsed)
                    tracker.record_video_duration(_probe_media_duration(output_path))
            except ImportError:
                pass
            log(f"✅ اكتمل الترميز في {int(render_elapsed)}s (FFmpeg)")
            return result
        finally:
            stop_heartbeat.set()

    if FFMPEG.exists():
        import moviepy.config as moviepy_config  # pyright: ignore[reportMissingImports]

        moviepy_config.change_settings({"FFMPEG_BINARY": str(FFMPEG)})

    log(f"🎞️ تجميع الفيديو ({width}x{height})...")
    clips = []
    audio_clips = []

    for scene, audio_path, visual_path in zip(scenes, audio_paths, visual_paths):
        audio = AudioFileClip(str(audio_path))
        from scene_timing import SCENE_TAIL_PADDING_SEC

        audio_sec = float(audio.duration or 0)
        clip_duration = float(scene.get("duration_sec") or 0)
        if clip_duration <= 0:
            clip_duration = audio_sec + SCENE_TAIL_PADDING_SEC
        elif clip_duration < audio_sec + 0.15:
            clip_duration = audio_sec + SCENE_TAIL_PADDING_SEC

        audio = _fit_audio_duration(audio, clip_duration)

        visual = _visual_clip(visual_path, width, height, clip_duration, scene)
        overlay = _screen_text_overlay(scene.get("screen_text", ""), width, height, clip_duration, settings)
        if overlay is not None:
            visual = CompositeVideoClip([visual, overlay])
        visual = visual.set_audio(audio)
        clips.append(visual)
        audio_clips.append(audio)

    final = concatenate_videoclips(clips, method="compose")
    for clip in clips:
        if clip.w != width or clip.h != height:
            log(f"⚠️ تحذير: مقطع بحجم {clip.size} بدل {width}x{height}")

    music_enabled = settings.get("music_enabled", False)
    music_file = settings.get("music_file", "music.mp3")
    music_path = ROOT / "resources" / music_file
    if music_enabled and music_path.exists():
        music = _scale_audio_volume(AudioFileClip(str(music_path)), float(settings.get("music_volume", 0.3)))
        if music.duration < final.duration:
            music = _loop_media_clip(music, final.duration)
        else:
            music = music.subclip(0, final.duration)
        final = final.set_audio(
            CompositeAudioClip([final.audio, music]) if final.audio else music
        )

    target_duration = int(settings.get("video_duration_sec", 60))
    trim_long = settings.get("trim_to_target_duration", False)
    if trim_long and final.duration > target_duration + 1:
        log(f"⚠️ قص الفيديو من {int(final.duration)}s إلى {target_duration}s (الهدف)")
        final = final.subclip(0, target_duration)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    log(f"⏳ ترميز الفيديو (~{int(final.duration)}s) — قد يستغرق 1–5 دقائق...")

    stop_heartbeat = threading.Event()
    render_started = time.monotonic()

    def _encoding_heartbeat() -> None:
        elapsed = 0
        while not stop_heartbeat.wait(20):
            elapsed += 20
            log(f"  ⏳ جاري ترميز الفيديو... {elapsed}s")

    heartbeat = threading.Thread(target=_encoding_heartbeat, daemon=True)
    heartbeat.start()
    try:
        final.write_videofile(
            str(output_path),
            fps=24,
            codec="libx264",
            audio_codec="aac",
            threads=4,
            preset="veryfast",
            ffmpeg_params=["-crf", "23", "-movflags", "+faststart"],
            logger=None,
        )
    finally:
        stop_heartbeat.set()
        render_elapsed = time.monotonic() - render_started
        try:
            from production_report import active_tracker

            tracker = active_tracker()
            if tracker is not None:
                tracker.record_phase_time("render", render_elapsed)
        except ImportError:
            pass
        log(f"✅ اكتمل الترميز في {int(render_elapsed)}s")

    for clip in clips:
        clip.close()
    final.close()
    for audio in audio_clips:
        audio.close()

    try:
        from production_report import active_tracker

        t = active_tracker()
        if t:
            t.record_video_duration(float(final.duration or 0))
    except ImportError:
        pass

    log(f"✅ تم حفظ الفيديو: {output_path.name}")
    return output_path


def merge_chapter_videos(chapter_paths: list[Path], output_path: Path, log: Callable[[str], None]) -> Path:
    if not chapter_paths:
        raise RuntimeError("لا توجد فصول للدمج")
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if FFMPEG.exists() and len(chapter_paths) > 1:
        list_file = output_path.parent / "concat_list.txt"
        lines = []
        for path in chapter_paths:
            lines.append(f"file '{path.resolve().as_posix()}'")
        list_file.write_text("\n".join(lines), encoding="utf-8")
        log("🔗 دمج الفصول عبر FFmpeg...")
        cmd = [
            str(FFMPEG),
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(list_file),
            "-c",
            "copy",
            str(output_path),
        ]
        subprocess.run(cmd, check=True, capture_output=True)
        log(f"✅ تم دمج {len(chapter_paths)} فصول → {output_path.name}")
        return output_path

    log("🔗 دمج الفصول عبر MoviePy...")
    clips = [VideoFileClip(str(path)) for path in chapter_paths]
    final = concatenate_videoclips(clips, method="compose")
    final.write_videofile(str(output_path), fps=24, codec="libx264", audio_codec="aac", threads=4, logger=None)
    for clip in clips:
        clip.close()
    final.close()
    log(f"✅ تم دمج {len(chapter_paths)} فصول → {output_path.name}")
    return output_path


async def _produce_scenes(
    topic: str,
    scenes: list[Scene],
    settings: dict,
    preset: FormatPreset,
    log: Callable[[str], None],
    output_path: Path,
) -> Path:
    import time

    from production_report import active_tracker

    tracker = active_tracker()
    t0 = time.monotonic()
    audio_paths = await generate_scene_voices(scenes, settings, log, topic)
    if tracker:
        tracker.record_phase_time("tts", time.monotonic() - t0)
    from scene_timing import sync_scenes_to_audio

    scenes = sync_scenes_to_audio(scenes, audio_paths, settings, log)
    _save_scenes(scenes, topic=topic, settings=settings)
    t1 = time.monotonic()
    visual_paths = await asyncio.to_thread(fetch_all_scene_visuals, scenes, topic, preset, log, settings)
    if tracker:
        tracker.record_phase_time("media", time.monotonic() - t1)
    output = await asyncio.to_thread(compose_video, scenes, audio_paths, visual_paths, settings, log, output_path)
    if not settings.get("_chapter_index"):
        try:
            from production_report import finalize_production_run

            finalize_production_run(topic, scenes, settings, output, log)
        except ImportError:
            pass
    return output


async def generate_video_chaptered(topic: str, settings: dict, log: Callable[[str], None]) -> Path:
    duration_sec = clamp_duration(int(settings.get("video_duration_sec", 60)))
    preset = FORMAT_PRESETS[settings.get("video_format", "short")]
    chapters = plan_chapters(topic, duration_sec)
    mode = get_mode_info(duration_sec)

    (OUTPUTS / "chapters.json").write_text(
        json.dumps(chapters, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    log(f"📚 وضع {mode['label']} — {len(chapters)} فصول × ~{CHAPTER_LENGTH_SEC // 60} دقائق")
    log(f"🎯 إجمالي المشاهd المستهدف: {mode['target_scenes']}")

    CHAPTERS_DIR.mkdir(parents=True, exist_ok=True)
    chapter_paths: list[Path] = []
    all_scenes: list[Scene] = []

    for chapter in chapters:
        log(f"📖 الفصل {chapter['index']}/{len(chapters)}: {chapter['title']}")
        _clean_output_dirs()
        ch_settings = {
            **settings,
            "video_duration_sec": int(chapter["duration_sec"]),
            "_chapter_index": chapter["index"],
        }
        scenes = await asyncio.to_thread(resolve_chapter_scenes, topic, chapter, settings, log)
        if not scenes:
            log(f"⚠️ تخطي الفصل {chapter['index']} — لا مشاهd")
            continue
        all_scenes.extend(scenes)
        ch_out = CHAPTERS_DIR / f"chapter_{int(chapter['index']):02d}.mp4"
        await _produce_scenes(topic, scenes, ch_settings, preset, log, ch_out)
        chapter_paths.append(ch_out)

    if not chapter_paths:
        raise RuntimeError("لم يتم إنتاج أي فصل")

    _save_scenes(all_scenes, topic=topic, settings=settings)
    final_path = OUTPUTS / preset["filename"]
    if len(chapter_paths) == 1:
        shutil.copy2(chapter_paths[0], final_path)
        log(f"✅ تم حفظ الفيديو: {final_path.name}")
        try:
            from production_report import finalize_production_run

            finalize_production_run(topic, all_scenes, settings, final_path, log)
        except ImportError:
            pass
        return final_path

    merged = await asyncio.to_thread(merge_chapter_videos, chapter_paths, final_path, log)
    try:
        from production_report import finalize_production_run

        finalize_production_run(topic, all_scenes, settings, merged, log)
    except ImportError:
        pass
    return merged


async def generate_video(topic: str, settings: dict, log: Callable[[str], None]) -> Path:
    duration_sec = clamp_duration(int(settings.get("video_duration_sec", 60)))
    video_format = settings.get("video_format", "short")
    if video_format not in FORMAT_PRESETS:
        video_format = "short"
    settings = {**settings, "video_duration_sec": duration_sec, "video_format": video_format}

    mode = get_mode_info(duration_sec)
    log(f"📊 الوضع: {mode['label']} ({mode['description']})")
    log(f"🎯 المشاهd المستهدف: {mode['target_scenes']} | المدة: {duration_sec // 60} د {duration_sec % 60} ث")

    if uses_chapters(duration_sec):
        from production_session import start_production_session

        start_production_session(topic, settings)
        return await generate_video_chaptered(topic, settings, log)

    from production_session import start_production_session

    start_production_session(topic, settings)
    _clean_output_dirs()
    preset = FORMAT_PRESETS[video_format]

    scenes = await asyncio.to_thread(resolve_scenes, topic, duration_sec, settings, log)
    final_path = OUTPUTS / preset["filename"]
    return await _produce_scenes(topic, scenes, settings, preset, log, final_path)
