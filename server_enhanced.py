from __future__ import annotations

import asyncio
import json
import threading
import time
from pathlib import Path
from typing import Any, cast

import uvicorn
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from scene_script import DEFAULT_SCRIPT_TEMPLATE, parse_scenes_full_text, scenes_to_script_text
from topic_research import prepare_scenes_for_media, research_for_topic, research_topic_with_agent
from video_duration import TARGET_DURATION_OPTIONS, clamp_duration, get_mode_info
from video_pipeline import (
    FORMAT_PRESETS,
    OUTPUTS,
    ROOT,
    SCENE_UPLOADS,
    Scene,
    _default_media_type,
    generate_scenes_from_topic,
    generate_video,
)
from publish_queue import (
    add_to_queue,
    get_queue_item,
    list_history,
    list_queue_items,
    process_queue_item,
    remove_queue_item,
)
from youtube_metadata import (
    THUMBNAIL_PATH,
    generate_publish_metadata,
    generate_thumbnail,
    load_channel_template,
    load_publish_metadata,
    prepare_publish_package,
    save_channel_template,
    save_custom_thumbnail,
    save_publish_metadata,
)
from youtube_uploader import (
    complete_oauth_flow,
    disconnect_oauth,
    fetch_channel_playlists,
    is_authorized,
    load_playlists_cache,
    start_oauth_flow,
    upload_queue_item,
)

SCENES_PATH = OUTPUTS / "scenes.json"

app = FastAPI(title="AI Video Generator", version="0.2.0")
templates = Jinja2Templates(directory=str(ROOT / "templates"))
app.mount("/resources", StaticFiles(directory=str(ROOT / "resources")), name="resources")
(OUTPUTS / "images").mkdir(parents=True, exist_ok=True)
app.mount("/scene-media", StaticFiles(directory=str(OUTPUTS / "images")), name="scene-media")

SETTINGS_PATH = OUTPUTS / "settings.json"
TOPICS_PATH = OUTPUTS / "topics.txt"
VOICE_OPTIONS = ["أدم", "سلمى", "حامد"]

DEFAULT_SETTINGS: dict[str, Any] = {
    "voice_name": "أدم",
    "media_source": "images",
    "caption_enabled": False,
    "font": "font.ttf",
    "fontsize": 100,
    "fontcolor": "#F0F0F0",
    "align": "center",
    "stroke_color": "#FFFFFF",
    "stroke_width": 2,
    "bgcolor_enabled": False,
    "bgcolor": "#FFFFFF",
    "music_enabled": True,
    "music_file": "music.mp3",
    "music_volume": 0.5,
    "upload_enabled": False,
    "youtube_privacy": "unlisted",
    "video_duration_sec": 300,
    "video_format": "normal",
    "script_source": "scenes",
    "custom_script": "",
    "custom_scenes": "[]",
    "custom_scenes_script": "",
    "content_profile": "auto",
    "include_quran": True,
    "include_hadith": False,
    "historical_accuracy": True,
    "visual_style": "cinematic_islamic",
    "narrator_style": "وثائقي",
    "tts_speed": 0.95,
    "arabic_font": "NotoNaskhArabic-Regular.ttf",
    "audio_driven_duration": True,
    "trim_to_target_duration": False,
    "scene_cache_enabled": True,
    "force_fresh_media": False,
    "max_ai_video_scenes": 3,
    "image_quality_retries": 2,
    "image_min_quality": 0.45,
    "image_max_face_score": 0.38,
    "image_max_text_score": 0.42,
    "islamic_max_text_score": 0.30,
    "scene_relevance_min_score": 80,
    "visual_quality_retries": 2,
    "imagerouter_model": "black-forest-labs/FLUX-1-schnell",
    "quality_gate_enabled": True,
    "hook_scene": True,
    "cliffhanger": True,
    "lesson_summary": True,
    "youtube_made_for_kids": False,
    "youtube_playlist_id": "",
    "youtube_schedule_at": "",
}

_log_messages: list[str] = []
_log_lock = threading.Lock()
_generation_lock = threading.Lock()
_upload_lock = threading.Lock()
_last_output: Path | None = None
_generation_started_at: float | None = None
_generation_last_log_at: float | None = None
_generation_topic: str | None = None
LOG_CLEAR_SENTINEL = "__LOG_CLEAR__"


def _log(message: str) -> None:
    with _log_lock:
        _log_messages.append(message)
    global _generation_last_log_at
    _generation_last_log_at = time.time()


def _reset_log() -> None:
    """Clear server log buffer and signal connected SSE clients to wipe the UI log."""
    with _log_lock:
        _log_messages.clear()
        _log_messages.append(LOG_CLEAR_SENTINEL)
    global _generation_last_log_at
    _generation_last_log_at = time.time()


@app.get("/generation/status")
async def generation_status():
    running = _generation_lock.locked()
    now = time.time()
    started_at = _generation_started_at
    last_log_at = _generation_last_log_at
    with _log_lock:
        last_message = _log_messages[-1] if _log_messages else None
        if last_message == LOG_CLEAR_SENTINEL:
            for message in reversed(_log_messages[:-1]):
                if message != LOG_CLEAR_SENTINEL:
                    last_message = message
                    break
            else:
                last_message = None
    session_payload: dict[str, Any] = {}
    try:
        from production_session import get_current_session, research_engine_label

        session = get_current_session()
        if session:
            session_payload = {
                "session_id": session.session_id,
                "research_source": session.research_source,
                "research_engine_label": research_engine_label(session.research_source) if session.research_source else None,
            }
    except ImportError:
        pass
    return {
        "ok": True,
        "running": running,
        "topic": _generation_topic,
        "started_at": started_at,
        "elapsed_sec": int(now - started_at) if started_at else 0,
        "last_log_at": last_log_at,
        "seconds_since_last_log": int(now - last_log_at) if last_log_at else None,
        "last_message": last_message,
        **session_payload,
    }


def _load_settings() -> dict[str, Any]:
    if SETTINGS_PATH.exists():
        try:
            data = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
            return {**DEFAULT_SETTINGS, **data}
        except json.JSONDecodeError:
            pass
    return dict(DEFAULT_SETTINGS)


def _save_settings(settings: dict[str, Any]) -> None:
    OUTPUTS.mkdir(parents=True, exist_ok=True)
    SETTINGS_PATH.write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8")


def _collect_settings(**params: Any) -> dict[str, Any]:
    current = _load_settings()
    for key, value in params.items():
        if value is None:
            continue
        current[key] = value
    return current


def _parse_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).lower() in {"1", "true", "yes", "on"}


def _parse_custom_scenes_list(raw: Any) -> list[dict[str, str]]:
    if isinstance(raw, list):
        return raw
    if not isinstance(raw, str) or not raw.strip():
        return []
    try:
        data = json.loads(raw)
        return data if isinstance(data, list) else []
    except json.JSONDecodeError:
        return []


def _load_custom_scenes_list() -> list[dict[str, str]]:
    return _parse_custom_scenes_list(_load_settings().get("custom_scenes", "[]"))


def _load_scenes_from_disk() -> list[Scene]:
    from video_pipeline import load_scenes_from_disk

    settings = _load_settings()
    topic = str(settings.get("last_topic") or _generation_topic or "").strip()
    loaded = load_scenes_from_disk(topic)
    if loaded:
        return loaded
    if SCENES_PATH.exists():
        try:
            data = json.loads(SCENES_PATH.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return cast(list[Scene], data)
        except json.JSONDecodeError:
            pass
    return cast(list[Scene], _load_custom_scenes_list())


def _resolve_output_path() -> Path | None:
    if _last_output and _last_output.exists():
        return _last_output
    for candidate in (OUTPUTS / "youtube_video.mp4", OUTPUTS / "youtube_short.mp4"):
        if candidate.exists():
            return candidate
    return None


def _publish_status_payload() -> dict[str, Any]:
    output = _resolve_output_path()
    metadata = load_publish_metadata()
    settings = _load_settings()
    return {
        "ready": bool(output and output.exists()),
        "filename": output.name if output else None,
        "download_url": "/download/video",
        "preview_video_url": "/preview/video" if output else None,
        "preview_thumbnail_url": "/preview/thumbnail" if THUMBNAIL_PATH.exists() else None,
        "metadata_ready": bool(metadata.get("title")),
        "metadata": metadata,
        "approved": bool(metadata.get("approved")),
        "youtube_connected": is_authorized(),
        "upload_available": is_authorized() and bool(metadata.get("approved")),
        "queue_count": len(list_queue_items()),
        "publish_settings": {
            "privacy": settings.get("youtube_privacy", "unlisted"),
            "category_id": settings.get("youtube_category_id", "27"),
            "made_for_kids": bool(settings.get("youtube_made_for_kids", False)),
            "playlist_id": settings.get("youtube_playlist_id", ""),
            "schedule_at": settings.get("youtube_schedule_at", ""),
        },
    }


@app.get("/stream")
async def stream():
    async def event_generator():
        sent = 0
        while True:
            await asyncio.sleep(0.3)
            with _log_lock:
                # After _log_messages.clear() the list shrinks — reset cursor or SSE stops forever.
                if len(_log_messages) < sent:
                    sent = 0
                if sent < len(_log_messages):
                    for message in _log_messages[sent:]:
                        yield f"data: {message}\n\n"
                    sent = len(_log_messages)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/", response_class=HTMLResponse)
async def get_form(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "settings": _load_settings(),
            "voice_options": VOICE_OPTIONS,
            "topics_file": str(TOPICS_PATH),
            "duration_options": TARGET_DURATION_OPTIONS,
            "custom_scenes_list": _load_custom_scenes_list(),
            "script_template": DEFAULT_SCRIPT_TEMPLATE,
            "custom_scenes_script": _load_settings().get("custom_scenes_script", ""),
            "channel_template": load_channel_template(),
            "publish_metadata": load_publish_metadata(),
            "playlists": load_playlists_cache(),
        },
    )


@app.get("/duration/info")
async def duration_info(video_duration_sec: int = 300):
    seconds = clamp_duration(video_duration_sec)
    return {"ok": True, **get_mode_info(seconds), "duration_sec": seconds}


@app.get("/topics_info")
async def topics_info():
    if not TOPICS_PATH.exists():
        return {"count": 0, "next": None}
    lines = [line.strip() for line in TOPICS_PATH.read_text(encoding="utf-8").splitlines() if line.strip()]
    return {"count": len(lines), "next": lines[0] if lines else None}


@app.get("/youtube/status")
async def youtube_status():
    return {
        "authorized": is_authorized(),
        "queue_count": len(list_queue_items()),
    }


@app.get("/youtube/auth/start")
async def youtube_auth_start():
    try:
        auth_url = start_oauth_flow()
        return RedirectResponse(auth_url)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/youtube/auth/callback")
async def youtube_auth_callback(
    code: str = "",
    state: str = "",
    error: str = "",
):
    if error:
        return RedirectResponse(f"/?youtube_error={error}")
    if not code:
        return RedirectResponse("/?youtube_error=missing_code")
    try:
        await asyncio.to_thread(complete_oauth_flow, code, state or None)
        await asyncio.to_thread(fetch_channel_playlists, _log)
        return RedirectResponse("/?youtube=connected")
    except Exception as exc:
        return RedirectResponse(f"/?youtube_error={exc}")


@app.post("/youtube/disconnect")
async def youtube_disconnect():
    disconnect_oauth()
    return {"ok": True, "authorized": False}


@app.get("/youtube/playlists")
async def youtube_playlists():
    if not is_authorized():
        return {"ok": True, "playlists": load_playlists_cache(), "cached": True}
    playlists = await asyncio.to_thread(fetch_channel_playlists, _log)
    return {"ok": True, "playlists": playlists, "cached": False}


@app.post("/youtube/playlists/refresh")
async def refresh_youtube_playlists():
    if not is_authorized():
        raise HTTPException(status_code=401, detail="اربط حساب YouTube أولاً")
    playlists = await asyncio.to_thread(fetch_channel_playlists, _log)
    return {"ok": True, "playlists": playlists}


@app.get("/channel/template")
async def get_channel_template():
    return {"ok": True, "template": load_channel_template()}


@app.post("/channel/template")
async def save_channel_template_endpoint(
    channel_name: str = Form(""),
    default_description: str = Form(""),
    default_tags: str = Form(""),
    watermark_text: str = Form(""),
    title_prefix: str = Form(""),
    title_suffix: str = Form(""),
):
    tags = [t.strip() for t in default_tags.replace("\n", ",").split(",") if t.strip()]
    template = save_channel_template(
        {
            "channel_name": channel_name,
            "default_description": default_description,
            "default_tags": tags,
            "watermark_text": watermark_text,
            "title_prefix": title_prefix,
            "title_suffix": title_suffix,
        }
    )
    return {"ok": True, "template": template}


@app.get("/publish/metadata")
async def get_publish_metadata():
    return {"ok": True, "metadata": load_publish_metadata()}


@app.post("/publish/metadata")
async def save_publish_metadata_endpoint(
    title: str = Form(""),
    description: str = Form(""),
    tags: str = Form(""),
    hashtags: str = Form(""),
):
    tag_list = [t.strip() for t in tags.replace("\n", ",").split(",") if t.strip()]
    hashtag_list = [t.strip() for t in hashtags.replace("\n", ",").split(",") if t.strip()]
    current = load_publish_metadata()
    metadata = save_publish_metadata(
        {
            **current,
            "title": title,
            "description": description,
            "tags": tag_list,
            "hashtags": hashtag_list,
            "approved": False,
        }
    )
    return {"ok": True, "metadata": metadata}


@app.post("/publish/metadata/generate")
async def generate_metadata_endpoint(
    topic: str = Form(...),
):
    if not topic.strip():
        raise HTTPException(status_code=400, detail="أدخل موضوع الفيديو أولاً")
    scenes = _load_scenes_from_disk()
    metadata = await asyncio.to_thread(
        generate_publish_metadata,
        topic.strip(),
        scenes,
        _log,
    )
    return {"ok": True, "metadata": metadata}


@app.post("/publish/approve")
async def approve_publish():
    metadata = load_publish_metadata()
    if not _resolve_output_path():
        raise HTTPException(status_code=400, detail="لا يوجد فيديو جاهز للاعتماد")
    if not metadata.get("title"):
        raise HTTPException(status_code=400, detail="أكمل بيانات النشر (العنوان والوصف) أولاً")
    metadata = save_publish_metadata({**metadata, "approved": True})
    _log("✅ تم اعتماد الفيديو — يمكنك الآن إضافته إلى قائمة النشر")
    return {"ok": True, "metadata": metadata}


def _start_queue_upload(item_id: str) -> None:
    if not is_authorized():
        raise HTTPException(status_code=401, detail="اربط حساب YouTube أولاً")
    if not _upload_lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="هناك عملية رفع قيد التشغيل")

    def worker():
        try:
            process_queue_item(item_id, upload_queue_item, _log)
        except Exception as exc:
            _log(f"❌ فشل الرفع: {exc}")
        finally:
            _upload_lock.release()

    threading.Thread(target=worker, daemon=True).start()


@app.post("/publish/queue/add")
async def queue_add_current(
    privacy: str = Form("unlisted"),
    category_id: str = Form("27"),
    made_for_kids: bool = Form(False),
    scheduled_time: str = Form(""),
    playlist_id: str = Form(""),
    playlist_name: str = Form(""),
):
    metadata = load_publish_metadata()
    if not metadata.get("approved"):
        raise HTTPException(status_code=400, detail="اعتمد الفيديو أولاً قبل الإضافة إلى قائمة النشر")
    output = _resolve_output_path()
    if not output:
        raise HTTPException(status_code=400, detail="لا يوجد فيديو جاهز")

    playlist_label = playlist_name.strip()
    if playlist_id and not playlist_label:
        for playlist in load_playlists_cache():
            if playlist.get("id") == playlist_id:
                playlist_label = playlist.get("name", "")
                break

    item = await asyncio.to_thread(
        add_to_queue,
        title=metadata.get("title") or "",
        description=metadata.get("description") or "",
        tags=list(metadata.get("tags") or []),
        topic=metadata.get("topic") or "",
        video_path=output,
        thumbnail_path=THUMBNAIL_PATH if THUMBNAIL_PATH.exists() else None,
        privacy=privacy,
        category_id=category_id,
        made_for_kids=_parse_bool(made_for_kids),
        scheduled_time=scheduled_time.strip() or None,
        playlist_id=playlist_id.strip() or None,
        playlist_name=playlist_label or None,
    )
    save_publish_metadata({**metadata, "approved": False, "queued_item_id": item["id"]})
    _log(f"📥 أُضيف إلى قائمة النشر: {item['title']}")
    return {"ok": True, "item": item}


@app.get("/publish/queue")
async def get_publish_queue():
    return {
        "ok": True,
        "items": list_queue_items(),
        "history": list_history(),
    }


@app.delete("/publish/queue/{item_id}")
async def delete_queue_item(item_id: str):
    if not remove_queue_item(item_id):
        raise HTTPException(status_code=404, detail="العنصر غير موجود")
    return {"ok": True}


@app.post("/publish/queue/{item_id}/upload")
async def upload_queue_item_endpoint(item_id: str):
    item = get_queue_item(item_id)
    if not item:
        raise HTTPException(status_code=404, detail="العنصر غير موجود في قائمة النشر")
    _start_queue_upload(item_id)
    return {"ok": True, "message": "upload_started", "item_id": item_id}


@app.post("/publish/queue/process")
async def process_publish_queue():
    items = [i for i in list_queue_items() if i.get("status") in {"pending", "scheduled"}]
    if not items:
        raise HTTPException(status_code=400, detail="قائمة النشر فارغة")
    _start_queue_upload(items[0]["id"])
    return {"ok": True, "message": "upload_started", "item_id": items[0]["id"]}


@app.post("/thumbnail/generate")
async def regenerate_thumbnail(
    topic: str = Form(""),
    title: str = Form(""),
):
    output = _resolve_output_path()
    if not output:
        raise HTTPException(status_code=404, detail="لا يوجد فيديو لتوليد الصورة المصغرة منه")
    scenes = _load_scenes_from_disk()
    thumb = await asyncio.to_thread(
        generate_thumbnail,
        topic.strip() or load_publish_metadata().get("topic", ""),
        title.strip() or load_publish_metadata().get("title", ""),
        scenes,
        output,
        _log,
    )
    return {"ok": True, "thumbnail_url": "/preview/thumbnail", "path": str(thumb)}


@app.post("/thumbnail/upload")
async def upload_thumbnail(file: UploadFile = File(...)):
    if not file.filename:
        raise HTTPException(status_code=400, detail="لم يتم اختيار صورة")
    content = await file.read()
    path = await asyncio.to_thread(save_custom_thumbnail, content)
    _log("✅ تم تحديث الصورة المصغرة يدوياً")
    return {"ok": True, "thumbnail_url": "/preview/thumbnail", "path": str(path)}


@app.get("/preview/video")
async def preview_video():
    output = _resolve_output_path()
    if not output:
        raise HTTPException(status_code=404, detail="لا يوجد فيديو للمعاينة")
    return FileResponse(path=str(output), media_type="video/mp4")


@app.get("/preview/thumbnail")
async def preview_thumbnail():
    if not THUMBNAIL_PATH.exists():
        raise HTTPException(status_code=404, detail="لا توجد صورة مصغرة")
    return FileResponse(path=str(THUMBNAIL_PATH), media_type="image/jpeg")


def _build_settings(
    *,
    voice_name: str,
    media_source: str = "images",
    caption_enabled: bool = True,
    font: str = "font.ttf",
    fontsize: int = 90,
    fontcolor: str = "#F0F0F0",
    align: str = "center",
    stroke_color: str = "#000000",
    stroke_width: int = 2,
    bgcolor_enabled: bool = False,
    bgcolor: str = "#FFFFFF",
    music_enabled: bool = True,
    music_file: str = "music.mp3",
    music_volume: float = 0.5,
    upload_enabled: bool = False,
    youtube_privacy: str = "unlisted",
    video_duration_sec: int = 60,
    video_format: str = "normal",
    script_source: str = "auto",
    custom_script: str = "",
    custom_scenes: str = "[]",
    custom_scenes_script: str = "",
    content_profile: str = "auto",
    include_quran: bool = True,
    include_hadith: bool = False,
    historical_accuracy: bool = True,
    visual_style: str = "cinematic_islamic",
    narrator_style: str = "وثائقي",
    tts_speed: float = 0.95,
    arabic_font: str = "NotoNaskhArabic-Regular.ttf",
    scene_cache_enabled: bool | None = None,
    force_fresh_media: bool | None = None,
    quality_gate_enabled: bool | None = None,
    hook_scene: bool | None = None,
    cliffhanger: bool | None = None,
    lesson_summary: bool | None = None,
) -> dict[str, Any]:
    valid_sources = {"auto", "gemini", "local", "manual", "scenes", "scenes_script"}
    valid_profiles = {"auto", "educational", "islamic_story", "general"}
    valid_styles = {"cinematic_islamic", "documentary", "watercolor", "none"}
    valid_narrators = {"هادئ", "وثائقي", "مؤثر"}
    base = _collect_settings(
        voice_name=voice_name,
        media_source=media_source,
        caption_enabled=_parse_bool(caption_enabled),
        font=font,
        fontsize=fontsize,
        fontcolor=fontcolor,
        align=align,
        stroke_color=stroke_color,
        stroke_width=stroke_width,
        bgcolor_enabled=_parse_bool(bgcolor_enabled),
        bgcolor=bgcolor,
        music_enabled=_parse_bool(music_enabled),
        music_file=music_file,
        music_volume=music_volume,
        upload_enabled=_parse_bool(upload_enabled),
        youtube_privacy=youtube_privacy,
        video_duration_sec=clamp_duration(video_duration_sec),
        video_format=video_format if video_format in FORMAT_PRESETS else "normal",
        script_source=script_source if script_source in valid_sources else "auto",
        custom_script=custom_script or "",
        custom_scenes=custom_scenes or "[]",
        custom_scenes_script=custom_scenes_script or "",
        content_profile=content_profile if content_profile in valid_profiles else "auto",
        include_quran=_parse_bool(include_quran, True),
        include_hadith=_parse_bool(include_hadith, False),
        historical_accuracy=_parse_bool(historical_accuracy, True),
        visual_style=visual_style if visual_style in valid_styles else "cinematic_islamic",
        narrator_style=narrator_style if narrator_style in valid_narrators else "وثائقي",
        tts_speed=max(0.7, min(1.3, tts_speed or 0.95)),
        arabic_font=arabic_font or "NotoNaskhArabic-Regular.ttf",
    )
    return _apply_islamic_form_flags(
        base,
        scene_cache_enabled=scene_cache_enabled,
        force_fresh_media=force_fresh_media,
        quality_gate_enabled=quality_gate_enabled,
        hook_scene=hook_scene,
        cliffhanger=cliffhanger,
        lesson_summary=lesson_summary,
    )


def _start_generation(topic: str, settings: dict[str, Any]) -> None:
    if not _generation_lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="هناك عملية إنشاء قيد التشغيل بالفعل")

    def worker():
        try:
            _run_generation(topic, settings)
        finally:
            _generation_lock.release()

    global _generation_started_at, _generation_topic
    _generation_started_at = time.time()
    _generation_topic = topic
    threading.Thread(target=worker, daemon=True).start()


@app.get("/save_settings")
async def save_settings_endpoint(
    voice_name: str,
    media_source: str = "images",
    caption_enabled: bool = True,
    font: str = "font.ttf",
    fontsize: int = 90,
    fontcolor: str = "#F0F0F0",
    align: str = "center",
    stroke_color: str = "#000000",
    stroke_width: int = 2,
    bgcolor_enabled: bool = False,
    bgcolor: str = "#FFFFFF",
    music_enabled: bool = True,
    music_file: str = "music.mp3",
    music_volume: float = 0.5,
    upload_enabled: bool = False,
    youtube_privacy: str = "unlisted",
    video_duration_sec: int = 60,
    video_format: str = "normal",
    script_source: str = "auto",
    custom_script: str = "",
    custom_scenes: str = "[]",
    custom_scenes_script: str = "",
    content_profile: str = "auto",
    include_quran: bool = True,
    include_hadith: bool = False,
    historical_accuracy: bool = True,
    visual_style: str = "cinematic_islamic",
    narrator_style: str = "وثائقي",
    tts_speed: float = 0.95,
    arabic_font: str = "NotoNaskhArabic-Regular.ttf",
    scene_cache_enabled: bool | None = None,
    force_fresh_media: bool | None = None,
    quality_gate_enabled: bool | None = None,
    hook_scene: bool | None = None,
    cliffhanger: bool | None = None,
    lesson_summary: bool | None = None,
):
    settings = _build_settings(
        voice_name=voice_name,
        media_source=media_source,
        caption_enabled=caption_enabled,
        font=font,
        fontsize=fontsize,
        fontcolor=fontcolor,
        align=align,
        stroke_color=stroke_color,
        stroke_width=stroke_width,
        bgcolor_enabled=bgcolor_enabled,
        bgcolor=bgcolor,
        music_enabled=music_enabled,
        music_file=music_file,
        music_volume=music_volume,
        upload_enabled=upload_enabled,
        youtube_privacy=youtube_privacy,
        video_duration_sec=video_duration_sec,
        video_format=video_format,
        script_source=script_source,
        custom_script=custom_script,
        custom_scenes=custom_scenes,
        custom_scenes_script=custom_scenes_script,
        content_profile=content_profile,
        include_quran=include_quran,
        include_hadith=include_hadith,
        historical_accuracy=historical_accuracy,
        visual_style=visual_style,
        narrator_style=narrator_style,
        tts_speed=tts_speed,
        arabic_font=arabic_font,
        scene_cache_enabled=scene_cache_enabled,
        force_fresh_media=force_fresh_media,
        quality_gate_enabled=quality_gate_enabled,
        hook_scene=hook_scene,
        cliffhanger=cliffhanger,
        lesson_summary=lesson_summary,
    )
    _save_settings(settings)
    return {"ok": True, "settings": settings}


@app.post("/cache/clear")
async def clear_cache_endpoint(topic: str = Form("")):
    from scene_cache import clear_all_cache, clear_topic_cache

    if topic.strip():
        removed = clear_topic_cache(topic.strip())
        return {"ok": True, "topic": topic.strip(), "entries_removed": removed}
    removed = clear_all_cache()
    return {"ok": True, "topics_cleared": removed}


@app.get("/video/status")
async def video_status():
    return _publish_status_payload()


@app.get("/publish/status")
async def publish_status():
    return _publish_status_payload()


@app.get("/download/video")
async def download_video():
    output = _resolve_output_path()
    if not output:
        raise HTTPException(status_code=404, detail="لا يوجد فيديو جاهز للتنزيل")
    return FileResponse(
        path=str(output),
        media_type="video/mp4",
        filename=output.name,
    )


def _apply_islamic_form_flags(settings: dict[str, Any], **flags: Any) -> dict[str, Any]:
    merged = dict(settings)
    for key in (
        "scene_cache_enabled",
        "force_fresh_media",
        "quality_gate_enabled",
        "hook_scene",
        "cliffhanger",
        "lesson_summary",
    ):
        if key in flags and flags[key] is not None:
            merged[key] = _parse_bool(flags[key], bool(merged.get(key, DEFAULT_SETTINGS.get(key, True))))
    return merged


def _log_generation_banner(settings: dict[str, Any]) -> None:
    from production_session import PIPELINE_VERSION
    from scene_cache import cache_enabled, _force_fresh_media
    from video_pipeline import _imagerouter_model

    cache_on = cache_enabled(settings)
    gate_on = bool(settings.get("quality_gate_enabled", True))
    fresh = _force_fresh_media(settings)
    _log(
        f"🔧 Pipeline v{PIPELINE_VERSION} | cache={'ON' if cache_on else 'OFF'} | "
        f"quality_gate={'ON' if gate_on else 'OFF'} | fresh_media={'YES' if fresh else 'NO'}"
    )
    _log(f"🎨 ImageRouter Model: {_imagerouter_model(settings)}")
    if fresh:
        _log("🧹 وضع الوسائط الجديدة: Cache معطّل — كل الصور تُولَّد من جديد")
    elif cache_on:
        _log("♻️ Cache مفعّل — قد تُستخدم صور/صوت محفوظة (عطّله لاختبار FLUX)")


def _run_generation(topic: str, settings: dict[str, Any]) -> None:
    global _last_output
    from production_report import start_production_run
    from production_session import start_production_session
    from topic_consistency import log_pipeline_context

    from video_pipeline import clamp_duration, uses_chapters

    settings = dict(settings)
    settings["last_topic"] = topic.strip()
    _save_settings(settings)
    start_production_session(topic, settings)
    duration_sec = clamp_duration(int(settings.get("video_duration_sec", 60)))
    start_production_run(topic, settings, chaptered=uses_chapters(duration_sec))
    _reset_log()
    _log_generation_banner(settings)
    log_pipeline_context(topic, settings, _log)
    _log(f"🚀 بدء إنشاء فيديو: {topic}")
    _log(f"⏱️ المدة المطلوبة: {settings.get('video_duration_sec')} ثانية")
    _log(f"📐 النوع: {'فيديو عادي 16:9' if settings.get('video_format') == 'normal' else 'شورت 9:16'}")
    _log("📋 خطوات التنفيذ:")
    _log("  0️⃣ وكيل البحث: تحليل الموضوع + استعلامات الوسائط")
    _log("  1️⃣ تحميل المشاهd والنص")
    _log("  2️⃣ توليد الصوت (TTS)")
    _log("  3️⃣ تجهيز الوسائط (صور / فيديو / شرائح)")
    _log("  4️⃣ تجميع الفيديو النهائي")
    _log("  5️⃣ تجهيز العنوان والوصف والصورة المصغرة")
    try:
        output = asyncio.run(generate_video(topic, settings, _log))
        _last_output = output
        scenes = _load_scenes_from_disk()
        prepare_publish_package(topic, scenes, output, _log)
        _log("🎉 اكتمل إنشاء الفيديو — راجع المعاينة وبيانات النشر قبل الرفع")
    except Exception as exc:
        _log(f"❌ خطأ: {exc}")


@app.post("/generate")
async def generate_video_post(
    topic: str = Form(...),
    voice_name: str = Form(...),
    media_source: str = Form("images"),
    caption_enabled: bool = Form(True),
    font: str = Form("font.ttf"),
    fontsize: int = Form(90),
    fontcolor: str = Form("#F0F0F0"),
    align: str = Form("center"),
    stroke_color: str = Form("#000000"),
    stroke_width: int = Form(2),
    bgcolor_enabled: bool = Form(False),
    bgcolor: str = Form("#FFFFFF"),
    music_enabled: bool = Form(True),
    music_file: str = Form("music.mp3"),
    music_volume: float = Form(0.5),
    upload_enabled: bool = Form(False),
    youtube_privacy: str = Form("unlisted"),
    video_duration_sec: int = Form(60),
    video_format: str = Form("normal"),
    script_source: str = Form("auto"),
    custom_script: str = Form(""),
    custom_scenes: str = Form("[]"),
    custom_scenes_script: str = Form(""),
    content_profile: str = Form("auto"),
    include_quran: bool = Form(True),
    include_hadith: bool = Form(False),
    historical_accuracy: bool = Form(True),
    visual_style: str = Form("cinematic_islamic"),
    narrator_style: str = Form("وثائقي"),
    tts_speed: float = Form(0.95),
    arabic_font: str = Form("NotoNaskhArabic-Regular.ttf"),
    scene_cache_enabled: bool = Form(True),
    force_fresh_media: bool = Form(False),
    quality_gate_enabled: bool = Form(True),
    hook_scene: bool = Form(True),
    cliffhanger: bool = Form(True),
    lesson_summary: bool = Form(True),
):
    settings = _build_settings(
        voice_name=voice_name,
        media_source=media_source,
        caption_enabled=caption_enabled,
        font=font,
        fontsize=fontsize,
        fontcolor=fontcolor,
        align=align,
        stroke_color=stroke_color,
        stroke_width=stroke_width,
        bgcolor_enabled=bgcolor_enabled,
        bgcolor=bgcolor,
        music_enabled=music_enabled,
        music_file=music_file,
        music_volume=music_volume,
        upload_enabled=upload_enabled,
        youtube_privacy=youtube_privacy,
        video_duration_sec=video_duration_sec,
        video_format=video_format,
        script_source=script_source,
        custom_script=custom_script,
        custom_scenes=custom_scenes,
        custom_scenes_script=custom_scenes_script,
        content_profile=content_profile,
        include_quran=include_quran,
        include_hadith=include_hadith,
        historical_accuracy=historical_accuracy,
        visual_style=visual_style,
        narrator_style=narrator_style,
        tts_speed=tts_speed,
        arabic_font=arabic_font,
        scene_cache_enabled=scene_cache_enabled,
        force_fresh_media=force_fresh_media,
        quality_gate_enabled=quality_gate_enabled,
        hook_scene=hook_scene,
        cliffhanger=cliffhanger,
        lesson_summary=lesson_summary,
    )
    _save_settings(settings)
    _start_generation(topic, settings)
    return {"ok": True, "message": "started"}


@app.get("/generate")
async def generate_shorts(
    topic: str,
    voice_name: str,
    media_source: str = "images",
    caption_enabled: bool = True,
    font: str = "font.ttf",
    fontsize: int = 90,
    fontcolor: str = "#F0F0F0",
    align: str = "center",
    stroke_color: str = "#000000",
    stroke_width: int = 2,
    bgcolor_enabled: bool = False,
    bgcolor: str = "#FFFFFF",
    music_enabled: bool = True,
    music_file: str = "music.mp3",
    music_volume: float = 0.5,
    upload_enabled: bool = False,
    youtube_privacy: str = "unlisted",
    video_duration_sec: int = 60,
    video_format: str = "normal",
    script_source: str = "auto",
):
    settings = _build_settings(
        voice_name=voice_name,
        media_source=media_source,
        caption_enabled=caption_enabled,
        font=font,
        fontsize=fontsize,
        fontcolor=fontcolor,
        align=align,
        stroke_color=stroke_color,
        stroke_width=stroke_width,
        bgcolor_enabled=bgcolor_enabled,
        bgcolor=bgcolor,
        music_enabled=music_enabled,
        music_file=music_file,
        music_volume=music_volume,
        upload_enabled=upload_enabled,
        youtube_privacy=youtube_privacy,
        video_duration_sec=video_duration_sec,
        video_format=video_format,
        script_source=script_source,
    )
    _save_settings(settings)
    _start_generation(topic, settings)
    return {"ok": True, "message": "started"}


def _merge_topic_settings(**overrides: Any) -> dict[str, Any]:
    settings = _load_settings()
    for key, value in overrides.items():
        if value is not None:
            settings[key] = value
    return settings


@app.post("/scenes/auto_generate")
async def auto_generate_scenes(
    topic: str = Form(...),
    video_duration_sec: int = Form(60),
    media_source: str = Form("images"),
    content_profile: str = Form("auto"),
):
    if not topic.strip():
        raise HTTPException(status_code=400, detail="أدخل موضوع الفيديو أولاً")
    duration = clamp_duration(video_duration_sec)
    settings = _merge_topic_settings(media_source=media_source, content_profile=content_profile)
    settings["last_topic"] = topic.strip()
    _save_settings(settings)
    default_media = _default_media_type(settings)
    from production_session import (
        research_engine_label,
        save_research_bundle,
        set_research_source,
        start_production_session,
    )

    session = start_production_session(topic.strip(), settings, force_new=True)
    _reset_log()
    _log(f"✨ توليد مشاهد تلقائي: {topic.strip()}")
    scenes, meta = await asyncio.to_thread(
        research_for_topic,
        topic.strip(),
        duration,
        default_media,
        settings,
        _log,
    )
    source = str(meta.get("source") or "agent")
    set_research_source(source)
    from media_router import route_scenes_media

    scenes = route_scenes_media(scenes, topic.strip(), settings)
    save_research_bundle(
        OUTPUTS / "research.json",
        topic.strip(),
        {**meta, "script_source": settings.get("script_source")},
        scenes,
    )
    from video_pipeline import _save_scenes

    _save_scenes(scenes, topic=topic.strip(), settings=settings)
    if "local" in source:
        _log("⚠️ توليد محلي — للدقة الأفضل: راجع المشاهد قبل الإنتاج")
    script_text = scenes_to_script_text(scenes)
    return {
        "ok": True,
        "scenes": scenes,
        "source": source,
        "research_source": session.research_source or source,
        "session_id": session.session_id,
        "research_engine_label": research_engine_label(source),
        "research": meta,
        "script_text": script_text,
        "content_profile": meta.get("content_profile"),
    }


@app.post("/scenes/parse_script")
async def parse_scenes_script_endpoint(scenes_script: str = Form(...)):
    try:
        parsed = parse_scenes_full_text(scenes_script)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    from video_pipeline import _default_media_type, _normalize_scene

    default_media = _default_media_type(_load_settings())
    scenes = [_normalize_scene(item, default_media) for item in parsed]
    scenes = prepare_scenes_for_media(scenes, default_media)
    return {"ok": True, "scenes": scenes, "count": len(scenes)}


@app.post("/research/topic")
async def research_topic_endpoint(
    topic: str = Form(...),
    video_duration_sec: int = Form(60),
    media_source: str = Form("images"),
    content_profile: str = Form("auto"),
):
    if not topic.strip():
        raise HTTPException(status_code=400, detail="أدخل موضوع الفيديو")
    settings = _merge_topic_settings(media_source=media_source, content_profile=content_profile)
    settings["last_topic"] = topic.strip()
    _save_settings(settings)
    default_media = _default_media_type(settings)
    from production_session import (
        research_engine_label,
        save_research_bundle,
        set_research_source,
        start_production_session,
    )

    session = start_production_session(topic.strip(), settings, force_new=True)
    _reset_log()
    _log(f"🔎 بدء البحث: {topic.strip()}")
    scenes, meta = await asyncio.to_thread(
        research_for_topic,
        topic.strip(),
        clamp_duration(video_duration_sec),
        default_media,
        settings,
        _log,
    )
    source = str(meta.get("source") or "agent")
    set_research_source(source)
    from media_router import route_scenes_media

    scenes = route_scenes_media(scenes, topic.strip(), settings)
    save_research_bundle(
        OUTPUTS / "research.json",
        topic.strip(),
        {**meta, "script_source": settings.get("script_source")},
        scenes,
    )
    from video_pipeline import _save_scenes

    _save_scenes(scenes, topic=topic.strip(), settings=settings)
    return {
        "ok": True,
        "topic": topic.strip(),
        "scenes": scenes,
        "research": meta,
        "source": source,
        "research_source": session.research_source or source,
        "session_id": session.session_id,
        "research_engine_label": research_engine_label(source),
    }


@app.get("/research/latest")
async def research_latest():
    path = OUTPUTS / "research.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail="لا يوجد بحث محفوظ بعد")
    return json.loads(path.read_text(encoding="utf-8"))


@app.get("/story/reference")
async def story_reference_endpoint():
    from story_reference import load_story_reference

    ref = load_story_reference()
    if not ref:
        raise HTTPException(status_code=404, detail="لا توجد مراجع قصة محفوظة — نفّذ البحث أولاً")
    return {"ok": True, "story_reference": ref}


@app.get("/quality/islamic")
async def islamic_quality_preview(video_duration_sec: int = 600):
    from islamic_quality_test import run_quality_preview, save_quality_report

    settings = _merge_topic_settings(content_profile="islamic_story")
    report = await asyncio.to_thread(run_quality_preview, clamp_duration(video_duration_sec), settings)
    save_quality_report(report)
    return {"ok": True, "report": report}


@app.get("/scenes/quality-gate")
async def scenes_quality_gate():
    from scene_quality_gate import GATE_REPORT_PATH

    if not GATE_REPORT_PATH.exists():
        raise HTTPException(status_code=404, detail="لا يوجد تقرير بوابة جودة — جهّز الوسائط أو أنشئ فيديوً أولاً")
    return json.loads(GATE_REPORT_PATH.read_text(encoding="utf-8"))


@app.get("/production/report")
async def production_report():
    from production_report import REPORT_PATH, load_production_report

    report = load_production_report()
    if not report:
        raise HTTPException(
            status_code=404,
            detail="لا يوجد تقرير إنتاج — أنشئ فيديوً أولاً",
        )
    return {"ok": True, "path": str(REPORT_PATH), "report": report}


@app.post("/upload/scene")
async def upload_scene(file: UploadFile = File(...)):
    if not file.filename:
        raise HTTPException(status_code=400, detail="لم يتم اختيار ملف")
    safe_name = Path(file.filename).name.replace("..", "_")
    if not safe_name:
        raise HTTPException(status_code=400, detail="اسم الملف غير صالح")
    SCENE_UPLOADS.mkdir(parents=True, exist_ok=True)
    dest = SCENE_UPLOADS / safe_name
    dest.write_bytes(await file.read())
    return {
        "ok": True,
        "filename": safe_name,
        "local_file": f"outputs/scenes/uploads/{safe_name}",
    }


@app.get("/generate_from_list")
async def generate_from_list():
    if not TOPICS_PATH.exists():
        raise HTTPException(status_code=404, detail="ملف topics.txt غير موجود")
    lines = [line.strip() for line in TOPICS_PATH.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not lines:
        raise HTTPException(status_code=404, detail="قائمة المواضيع فارغة")

    topic = lines.pop(0)
    TOPICS_PATH.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    settings = _load_settings()
    _start_generation(topic, settings)
    return {"ok": True, "topic": topic}


if __name__ == "__main__":
    OUTPUTS.mkdir(parents=True, exist_ok=True)
    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="info")
