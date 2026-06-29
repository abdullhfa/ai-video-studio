"""Prepare and validate the 60s Yusuf smoke test (5 scenes, FLUX, quality gate).

Usage:
  py -3.12 scripts/run_yusuf_60s_test.py              # config check only
  py -3.12 scripts/run_yusuf_60s_test.py --clear-cache # wipe cache/ for topic
  py -3.12 scripts/run_yusuf_60s_test.py --start       # POST /generate if server is up
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

TOPIC = "قصة سيدنا يوسف عليه السلام"
SETTINGS_PATH = ROOT / "outputs" / "settings.json"
GATE_PATH = ROOT / "outputs" / "scene_quality_gate.json"
REPORT_PATH = ROOT / "outputs" / "production_report.json"

TEST_SETTINGS = {
    "video_duration_sec": 60,
    "video_format": "normal",
    "script_source": "auto",
    "content_profile": "islamic_story",
    "media_source": "images",
    "imagerouter_model": "black-forest-labs/FLUX-1-schnell",
    "quality_gate_enabled": True,
    "scene_relevance_min_score": 80,
    "visual_quality_retries": 2,
    "islamic_max_text_score": 0.30,
    "scene_cache_enabled": False,
    "force_fresh_media": True,
}


def _load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _merge_settings() -> dict:
    from server_enhanced import DEFAULT_SETTINGS, _load_settings, _save_settings

    base = dict(DEFAULT_SETTINGS)
    base.update(_load_settings())
    base.update(TEST_SETTINGS)
    _save_settings(base)
    return base


def _clear_cache(topic: str) -> None:
    from scene_cache import clear_topic_cache

    removed = clear_topic_cache(topic)
    print(f"Cleared cache for '{topic}': {removed} entries")


def _print_config(settings: dict) -> None:
    print("=== Yusuf 60s test config ===")
    for key in (
        "video_duration_sec",
        "script_source",
        "imagerouter_model",
        "quality_gate_enabled",
        "scene_cache_enabled",
        "force_fresh_media",
        "scene_relevance_min_score",
    ):
        print(f"  {key}: {settings.get(key)}")
    print()
    print("Before generating in the UI:")
    print("  1. Restart server (start_server.bat) — must show Pipeline v2.4.0")
    print("  2. Enable: 🧪 وسائط جديدة 100%")
    print("  3. Topic: قصة سيدنا يوسف | Duration: 60s | script_source: auto")
    print("  4. Log must show: Quality Gate blocks — NOT ♻️ from cache")
    print()


def _print_last_run() -> None:
    gate = _load_json(GATE_PATH)
    report = _load_json(REPORT_PATH)
    if gate:
        model = gate.get("imagerouter_model", "?")
        passed = gate.get("passed", 0)
        total = len(gate.get("scenes") or [])
        print(f"scene_quality_gate.json: {passed}/{total} passed | model={model}")
        for scene in gate.get("scenes") or []:
            idx = scene.get("index", "?")
            metrics = {
                "visual_quality": scene.get("visual_quality"),
                "story_relevance": scene.get("story_relevance"),
                "text_artifacts": scene.get("text_artifacts"),
            }
            gate_status = scene.get("quality_gate", "?")
            print(f"  scene {idx}: {gate_status} {metrics}")
    else:
        print("scene_quality_gate.json: (not found — run generation first)")

    if report:
        print(
            f"production_report: render={report.get('render_time_sec')}s "
            f"media={report.get('media_time_sec')}s "
            f"total={report.get('generation_time_sec')}s "
            f"duration={report.get('video_duration_sec')}s"
        )
        ok_render = (report.get("render_time_sec") or 999) < 300
        print(f"  render_time_sec < 300: {'PASS' if ok_render else 'FAIL'}")
    else:
        print("production_report.json: (not found)")


def _start_via_http(settings: dict) -> None:
    import urllib.parse
    import urllib.request

    params = {
        "topic": TOPIC,
        "voice_name": settings.get("voice_name", "أدم"),
        "media_source": settings.get("media_source", "images"),
        "video_duration_sec": settings.get("video_duration_sec", 60),
        "script_source": settings.get("script_source", "auto"),
        "content_profile": settings.get("content_profile", "islamic_story"),
        "scene_cache_enabled": "false",
        "force_fresh_media": "true",
    }
    url = "http://127.0.0.1:8000/generate?" + urllib.parse.urlencode(params)
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            print(resp.read().decode("utf-8", errors="replace"))
    except Exception as exc:
        print(f"Could not reach server: {exc}")
        print("Start with start_server.bat, then re-run with --start")


def main() -> None:
    if "--clear-cache" in sys.argv:
        _clear_cache(TOPIC)
    settings = _merge_settings()
    _print_config(settings)
    if "--start" in sys.argv:
        _start_via_http(settings)
    _print_last_run()


if __name__ == "__main__":
    main()
