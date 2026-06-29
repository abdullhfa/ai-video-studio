from __future__ import annotations

import json
import shutil
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from video_pipeline import OUTPUTS, ROOT

QUEUE_PATH = OUTPUTS / "publish_queue.json"
QUEUE_DIR = OUTPUTS / "queue"
_lock = threading.Lock()

VALID_STATUSES = {"pending", "uploading", "uploaded", "failed", "scheduled"}


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _load_raw() -> dict[str, Any]:
    if not QUEUE_PATH.exists():
        return {"items": [], "history": []}
    try:
        data = json.loads(QUEUE_PATH.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            data.setdefault("items", [])
            data.setdefault("history", [])
            return data
    except json.JSONDecodeError:
        pass
    return {"items": [], "history": []}


def _save_raw(data: dict[str, Any]) -> None:
    OUTPUTS.mkdir(parents=True, exist_ok=True)
    QUEUE_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def list_queue_items() -> list[dict[str, Any]]:
    with _lock:
        return list(_load_raw().get("items", []))


def list_history(limit: int = 50) -> list[dict[str, Any]]:
    with _lock:
        history = list(_load_raw().get("history", []))
    return list(reversed(history[-limit:]))


def get_queue_item(item_id: str) -> dict[str, Any] | None:
    with _lock:
        for item in _load_raw().get("items", []):
            if item.get("id") == item_id:
                return dict(item)
    return None


def _update_item(item_id: str, **changes: Any) -> dict[str, Any] | None:
    with _lock:
        data = _load_raw()
        for idx, item in enumerate(data["items"]):
            if item.get("id") == item_id:
                item.update(changes)
                item["updated_at"] = _now()
                data["items"][idx] = item
                _save_raw(data)
                return dict(item)
    return None


def _move_to_history(item: dict[str, Any]) -> None:
    with _lock:
        data = _load_raw()
        data["items"] = [i for i in data["items"] if i.get("id") != item.get("id")]
        history_item = dict(item)
        history_item["archived_at"] = _now()
        data["history"].append(history_item)
        if len(data["history"]) > 200:
            data["history"] = data["history"][-200:]
        _save_raw(data)


def remove_queue_item(item_id: str) -> bool:
    with _lock:
        data = _load_raw()
        before = len(data["items"])
        data["items"] = [i for i in data["items"] if i.get("id") != item_id]
        if len(data["items"]) == before:
            return False
        _save_raw(data)
    item_dir = QUEUE_DIR / item_id
    if item_dir.exists():
        shutil.rmtree(item_dir, ignore_errors=True)
    return True


def add_to_queue(
    *,
    title: str,
    description: str,
    tags: list[str],
    topic: str,
    video_path: Path,
    thumbnail_path: Path | None,
    privacy: str = "unlisted",
    category_id: str = "27",
    made_for_kids: bool = False,
    scheduled_time: str | None = None,
    playlist_id: str | None = None,
    playlist_name: str | None = None,
) -> dict[str, Any]:
    if not video_path.exists():
        raise FileNotFoundError(f"ملف الفيديو غير موجود: {video_path}")
    if not title.strip():
        raise ValueError("العنوان مطلوب لإضافة الفيديو إلى قائمة النشر")

    item_id = f"video_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
    dest_dir = QUEUE_DIR / item_id
    dest_dir.mkdir(parents=True, exist_ok=True)

    stored_video = dest_dir / "video.mp4"
    shutil.copy2(video_path, stored_video)

    stored_thumb: str | None = None
    if thumbnail_path and thumbnail_path.exists():
        thumb_dest = dest_dir / "thumbnail.jpg"
        shutil.copy2(thumbnail_path, thumb_dest)
        stored_thumb = str(thumb_dest.relative_to(ROOT)).replace("\\", "/")

    status = "scheduled" if scheduled_time else "pending"
    item = {
        "id": item_id,
        "title": title.strip(),
        "description": description.strip(),
        "tags": tags[:30],
        "topic": topic.strip(),
        "video_path": str(stored_video.relative_to(ROOT)).replace("\\", "/"),
        "thumbnail_path": stored_thumb,
        "approved": True,
        "status": status,
        "privacy": privacy if privacy in {"public", "unlisted", "private"} else "unlisted",
        "category_id": category_id or "27",
        "made_for_kids": made_for_kids,
        "scheduled_time": scheduled_time,
        "playlist_id": playlist_id or None,
        "playlist_name": playlist_name or None,
        "youtube_video_id": None,
        "watch_url": None,
        "error": None,
        "created_at": _now(),
        "updated_at": _now(),
        "uploaded_at": None,
    }

    with _lock:
        data = _load_raw()
        data["items"].append(item)
        _save_raw(data)
    return item


def mark_uploading(item_id: str) -> dict[str, Any] | None:
    return _update_item(item_id, status="uploading", error=None)


def mark_uploaded(item_id: str, youtube_video_id: str, watch_url: str) -> dict[str, Any] | None:
    item = _update_item(
        item_id,
        status="uploaded",
        youtube_video_id=youtube_video_id,
        watch_url=watch_url,
        uploaded_at=_now(),
        error=None,
    )
    if item:
        _move_to_history(item)
    return item


def mark_failed(item_id: str, error: str) -> dict[str, Any] | None:
    item = _update_item(item_id, status="failed", error=error)
    if item:
        _move_to_history(item)
    return item


def resolve_queue_path(relative_path: str) -> Path:
    path = Path(relative_path)
    if path.is_absolute():
        return path
    return ROOT / relative_path


def process_queue_item(
    item_id: str,
    upload_fn: Callable[[dict[str, Any], Callable[[str], None]], dict[str, Any]],
    log: Callable[[str], None],
) -> dict[str, Any]:
    item = get_queue_item(item_id)
    if not item:
        raise RuntimeError("العنصر غير موجود في قائمة النشر")
    if item.get("status") not in {"pending", "scheduled", "failed"}:
        raise RuntimeError(f"لا يمكن رفع عنصر بحالة: {item.get('status')}")

    mark_uploading(item_id)
    item = get_queue_item(item_id) or item
    try:
        result = upload_fn(item, log)
        video_id = result["video_id"]
        watch_url = result.get("watch_url") or f"https://www.youtube.com/watch?v={video_id}"
        updated = mark_uploaded(item_id, video_id, watch_url)
        log(f"✅ تم الرفع بنجاح — Video ID: {video_id}")
        log(f"🔗 {watch_url}")
        return updated or result
    except Exception as exc:
        mark_failed(item_id, str(exc))
        raise
