from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from content_profiles import detect_content_profile
from scene_cache import topic_slug
from video_pipeline import ROOT

OUTPUTS = ROOT / "outputs"
CURRENT_SESSION_PATH = OUTPUTS / "current_session.json"
SESSION_COUNTER_PATH = OUTPUTS / "session_counter.json"
PIPELINE_VERSION = "2.4.0"

_current_session: ProductionSession | None = None


@dataclass
class ProductionSession:
    session_id: str
    topic: str
    content_profile: str
    started_at: str
    research_source: str = ""


def _topics_match(a: str, b: str) -> bool:
    return topic_slug(a) == topic_slug(b)


def _increment_counter(date_str: str, slug: str) -> int:
    counters: dict[str, int] = {}
    if SESSION_COUNTER_PATH.exists():
        try:
            loaded = json.loads(SESSION_COUNTER_PATH.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                counters = {str(k): int(v) for k, v in loaded.items()}
        except (json.JSONDecodeError, TypeError, ValueError):
            counters = {}
    key = f"{date_str}_{slug}"
    counters[key] = counters.get(key, 0) + 1
    OUTPUTS.mkdir(parents=True, exist_ok=True)
    SESSION_COUNTER_PATH.write_text(json.dumps(counters, ensure_ascii=False, indent=2), encoding="utf-8")
    return counters[key]


def create_session_id(topic: str) -> str:
    date_str = datetime.now().strftime("%Y%m%d")
    slug = topic_slug(topic)
    counter = _increment_counter(date_str, slug)
    return f"{date_str}_{slug}_{counter:03d}"


def _session_from_dict(data: dict[str, Any]) -> ProductionSession | None:
    session_id = str(data.get("session_id") or "").strip()
    topic = str(data.get("topic") or "").strip()
    if not session_id or not topic:
        return None
    profile = str(data.get("content_profile") or "auto").strip() or "auto"
    started_at = str(data.get("started_at") or "").strip() or datetime.now(timezone.utc).isoformat()
    research_source = normalize_research_source(str(data.get("research_source") or ""))
    return ProductionSession(
        session_id=session_id,
        topic=topic,
        content_profile=profile,
        started_at=started_at,
        research_source=research_source,
    )


def _load_stored_session() -> ProductionSession | None:
    if not CURRENT_SESSION_PATH.exists():
        return None
    try:
        data = json.loads(CURRENT_SESSION_PATH.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return _session_from_dict(data)
    except json.JSONDecodeError:
        return None
    return None


def _persist_session(session: ProductionSession) -> None:
    OUTPUTS.mkdir(parents=True, exist_ok=True)
    payload = {**asdict(session), "pipeline_version": PIPELINE_VERSION}
    CURRENT_SESSION_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def start_production_session(
    topic: str,
    settings: dict[str, Any] | None,
    *,
    force_new: bool = False,
) -> ProductionSession:
    """Start or resume a production session for the given topic."""
    global _current_session
    settings = settings or {}
    topic = topic.strip()
    profile = detect_content_profile(topic, settings)
    resolved_profile = str(settings.get("content_profile") or profile)

    if not force_new:
        stored = _current_session or _load_stored_session()
        if stored and _topics_match(stored.topic, topic):
            _current_session = stored
            return stored

    session = ProductionSession(
        session_id=create_session_id(topic),
        topic=topic,
        content_profile=resolved_profile,
        started_at=datetime.now(timezone.utc).isoformat(),
    )
    _current_session = session
    _persist_session(session)
    return session


def get_current_session() -> ProductionSession | None:
    return _current_session


def set_research_source(source: str) -> None:
    global _current_session
    if _current_session is None:
        return
    _current_session.research_source = normalize_research_source(source)
    _persist_session(_current_session)
    try:
        from production_report import active_tracker

        tracker = active_tracker()
        if tracker is not None:
            tracker.research_source = _current_session.research_source
    except ImportError:
        pass


def normalize_research_source(source: str) -> str:
    value = (source or "").lower()
    if "gemini" in value:
        return "gemini"
    return "local"


def research_engine_label(source: str) -> str:
    if normalize_research_source(source) == "gemini":
        return "🟢 Gemini Research"
    return "🟡 Local Story Engine"


def is_pipeline_compatible(data: dict[str, Any] | None) -> bool:
    """Reject stamped files from a different pipeline schema version."""
    if not isinstance(data, dict):
        return False
    version = data.get("pipeline_version")
    if version is None:
        return True
    return str(version) == PIPELINE_VERSION


def stamp_session_fields(payload: dict[str, Any]) -> dict[str, Any]:
    session = get_current_session()
    stamped = dict(payload)
    stamped["pipeline_version"] = PIPELINE_VERSION
    if session is None:
        return stamped
    stamped["session_id"] = session.session_id
    stamped["topic"] = session.topic
    stamped["content_profile"] = session.content_profile
    if session.research_source:
        stamped["research_source"] = session.research_source
    return stamped


def file_matches_current_session(data: dict[str, Any] | None) -> bool:
    if not isinstance(data, dict):
        return False
    session = get_current_session()
    file_sid = str(data.get("session_id") or "").strip()
    if session is not None:
        if not file_sid:
            return False
        return file_sid == session.session_id
    if file_sid:
        return True
    return True


def file_matches_topic_session(data: dict[str, Any] | None, topic: str) -> bool:
    if not isinstance(data, dict):
        return False
    if not is_pipeline_compatible(data):
        return False
    session = get_current_session()
    file_sid = str(data.get("session_id") or "").strip()
    if session is not None:
        return bool(file_sid) and file_sid == session.session_id
    if file_sid:
        file_topic = str(data.get("topic") or "").strip()
        return bool(file_topic) and _topics_match(file_topic, topic)
    return False


def parse_scenes_document(raw: Any) -> tuple[list[Any], dict[str, Any] | None]:
    if isinstance(raw, list):
        return raw, None
    if isinstance(raw, dict):
        scenes = raw.get("scenes")
        if isinstance(scenes, list):
            meta = {
                key: raw[key]
                for key in (
                    "session_id",
                    "topic",
                    "content_profile",
                    "research_source",
                    "pipeline_version",
                )
                if key in raw
            }
            return scenes, meta
    return [], None


def scenes_meta_is_valid(meta: dict[str, Any] | None, topic: str) -> bool:
    if meta and not is_pipeline_compatible(meta):
        return False
    session = get_current_session()
    if meta and meta.get("session_id"):
        if session:
            return str(meta["session_id"]) == session.session_id
        file_topic = str(meta.get("topic") or "").strip()
        return bool(file_topic) and _topics_match(file_topic, topic)
    if session is not None:
        return False
    return True


def save_research_bundle(path: Path, topic: str, meta: dict[str, Any], scenes: list[Any]) -> None:
    payload = stamp_session_fields(
        {
            "topic": topic,
            "script_source": meta.get("script_source"),
            "content_profile": meta.get("content_profile"),
            **meta,
            "scenes": scenes,
        }
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
