"""CLI entry for islamic quality preview."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from islamic_quality_test import run_quality_preview, save_quality_report  # noqa: E402


def main() -> None:
    duration = int(sys.argv[1]) if len(sys.argv) > 1 else 600
    report = run_quality_preview(duration)
    out = save_quality_report(report)
    print(f"Report saved: {out}")
    for story in report["stories"]:
        status = "OK" if story.get("ok") else "FAIL"
        print(
            f"[{status}] {story['topic']}: "
            f"{story.get('scene_count', 0)} scenes, "
            f"~{story.get('estimated_total_sec', 0)}s, "
            f"pivotal={story.get('pivotal_scenes', 0)}"
        )


if __name__ == "__main__":
    main()
