from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

from publish_queue import resolve_queue_path
from video_pipeline import ROOT

API_DIR = ROOT / "api"
CLIENT_SECRETS_PATH = API_DIR / "client_secrets.json"
TOKEN_PATH = API_DIR / "token.json"
OAUTH_STATE_PATH = API_DIR / "oauth_state.json"
PLAYLISTS_CACHE_PATH = ROOT / "outputs" / "playlists_cache.json"

REDIRECT_URI = "http://127.0.0.1:8000/youtube/auth/callback"
SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube",
]

DEFAULT_PLAYLISTS = [
    {"name": "BTEC IT", "id": ""},
    {"name": "الصف العاشر", "id": ""},
    {"name": "الأول ثانوي", "id": ""},
    {"name": "التوجيهي", "id": ""},
]

YOUTUBE_CATEGORY_EDUCATION = "27"


def is_authorized() -> bool:
    return TOKEN_PATH.exists() and get_credentials() is not None


def _save_token(credentials: Credentials) -> None:
    API_DIR.mkdir(parents=True, exist_ok=True)
    TOKEN_PATH.write_text(credentials.to_json(), encoding="utf-8")


def get_credentials() -> Credentials | None:
    if not TOKEN_PATH.exists():
        return None
    try:
        creds = Credentials.from_authorized_user_file(str(TOKEN_PATH), SCOPES)
    except (ValueError, OSError):
        return None
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
        _save_token(creds)
    if not creds or not creds.valid:
        return None
    return creds


def start_oauth_flow() -> str:
    if not CLIENT_SECRETS_PATH.exists():
        raise FileNotFoundError("ملف client_secrets.json غير موجود في api/")
    flow = Flow.from_client_secrets_file(
        str(CLIENT_SECRETS_PATH),
        scopes=SCOPES,
        redirect_uri=REDIRECT_URI,
    )
    auth_url, state = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
    )
    API_DIR.mkdir(parents=True, exist_ok=True)
    OAUTH_STATE_PATH.write_text(json.dumps({"state": state}), encoding="utf-8")
    return auth_url


def complete_oauth_flow(code: str, state: str | None = None) -> None:
    if not CLIENT_SECRETS_PATH.exists():
        raise FileNotFoundError("ملف client_secrets.json غير موجود")
    saved_state = None
    if OAUTH_STATE_PATH.exists():
        try:
            saved_state = json.loads(OAUTH_STATE_PATH.read_text(encoding="utf-8")).get("state")
        except json.JSONDecodeError:
            pass

    flow = Flow.from_client_secrets_file(
        str(CLIENT_SECRETS_PATH),
        scopes=SCOPES,
        redirect_uri=REDIRECT_URI,
        state=saved_state or state,
    )
    flow.fetch_token(code=code)
    _save_token(flow.credentials)
    if OAUTH_STATE_PATH.exists():
        OAUTH_STATE_PATH.unlink(missing_ok=True)


def disconnect_oauth() -> None:
    if TOKEN_PATH.exists():
        TOKEN_PATH.unlink()


def _youtube_service():
    creds = get_credentials()
    if not creds:
        raise RuntimeError("يجب ربط حساب YouTube أولاً")
    return build("youtube", "v3", credentials=creds, cache_discovery=False)


def fetch_channel_playlists(log: Callable[[str], None] | None = None) -> list[dict[str, str]]:
    def _log(msg: str) -> None:
        if log:
            log(msg)

    youtube = _youtube_service()
    playlists: list[dict[str, str]] = []
    request = youtube.playlists().list(part="snippet", mine=True, maxResults=50)
    while request is not None:
        response = request.execute()
        for item in response.get("items", []):
            playlists.append(
                {
                    "id": item["id"],
                    "name": item["snippet"]["title"],
                }
            )
        request = youtube.playlists().list_next(request, response)

    merged: list[dict[str, str]] = []
    by_name = {p["name"].strip().lower(): p["id"] for p in playlists}
    for default in DEFAULT_PLAYLISTS:
        name = default["name"]
        merged.append({"name": name, "id": by_name.get(name.strip().lower(), default.get("id", ""))})
    for playlist in playlists:
        if not any(p["name"].strip().lower() == playlist["name"].strip().lower() for p in merged):
            merged.append(playlist)

    PLAYLISTS_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    PLAYLISTS_CACHE_PATH.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")
    _log(f"✅ تم جلب {len(playlists)} قائمة تشغيل من YouTube")
    return merged


def load_playlists_cache() -> list[dict[str, str]]:
    if PLAYLISTS_CACHE_PATH.exists():
        try:
            data = json.loads(PLAYLISTS_CACHE_PATH.read_text(encoding="utf-8"))
            if isinstance(data, list) and data:
                return data
        except json.JSONDecodeError:
            pass
    return [dict(p) for p in DEFAULT_PLAYLISTS]


def _normalize_scheduled_time(raw: str | None) -> str | None:
    if not raw or not raw.strip():
        return None
    value = raw.strip()
    if value.endswith("Z"):
        return value
    try:
        dt = datetime.fromisoformat(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    except ValueError:
        return None


def upload_queue_item(item: dict[str, Any], log: Callable[[str], None]) -> dict[str, Any]:
    youtube = _youtube_service()
    video_path = resolve_queue_path(item["video_path"])
    if not video_path.exists():
        raise FileNotFoundError(f"ملف الفيديو غير موجود: {video_path}")

    privacy = item.get("privacy", "unlisted")
    scheduled = _normalize_scheduled_time(item.get("scheduled_time"))
    status_body: dict[str, Any] = {
        "privacyStatus": privacy,
        "selfDeclaredMadeForKids": bool(item.get("made_for_kids", False)),
    }
    if scheduled:
        status_body["privacyStatus"] = "private"
        status_body["publishAt"] = scheduled

    body = {
        "snippet": {
            "title": item["title"][:100],
            "description": item.get("description", "")[:5000],
            "tags": item.get("tags", [])[:30],
            "categoryId": str(item.get("category_id") or YOUTUBE_CATEGORY_EDUCATION),
        },
        "status": status_body,
    }

    log("⬆️ جاري رفع الفيديو إلى YouTube...")
    media = MediaFileUpload(str(video_path), chunksize=1024 * 1024, resumable=True, mimetype="video/mp4")
    request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)
    response = None
    while response is None:
        status, response = request.next_chunk()
        if status:
            pct = int(status.progress() * 100)
            log(f"   ↳ تقدم الرفع: {pct}%")

    video_id = response["id"]
    log(f"✅ اكتمل رفع الفيديو — ID: {video_id}")

    thumb_rel = item.get("thumbnail_path")
    if thumb_rel:
        thumb_path = resolve_queue_path(thumb_rel)
        if thumb_path.exists():
            log("🖼️ جاري رفع الصورة المصغرة...")
            youtube.thumbnails().set(
                videoId=video_id,
                media_body=MediaFileUpload(str(thumb_path), mimetype="image/jpeg"),
            ).execute()
            log("✅ تم رفع الصورة المصغرة")

    playlist_id = item.get("playlist_id")
    if playlist_id:
        log(f"📂 إضافة الفيديو إلى قائمة: {item.get('playlist_name') or playlist_id}")
        youtube.playlistItems().insert(
            part="snippet",
            body={
                "snippet": {
                    "playlistId": playlist_id,
                    "resourceId": {"kind": "youtube#video", "videoId": video_id},
                }
            },
        ).execute()
        log("✅ تمت إضافة الفيديو إلى قائمة التشغيل")

    watch_url = f"https://www.youtube.com/watch?v={video_id}"
    return {"video_id": video_id, "watch_url": watch_url}
