"""Download recommended Arabic fonts into resources/fonts/."""
from __future__ import annotations

import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FONTS_DIR = ROOT / "resources" / "fonts"

FONT_URLS: dict[str, str] = {
    "NotoNaskhArabic-Regular.ttf": (
        "https://raw.githubusercontent.com/jenskutilek/free-fonts/master/"
        "Noto/Noto%20Naskh%20Arabic/TTF/NotoNaskhArabic-Regular.ttf"
    ),
    "NotoNaskhArabic-Bold.ttf": (
        "https://raw.githubusercontent.com/jenskutilek/free-fonts/master/"
        "Noto/Noto%20Naskh%20Arabic/TTF/NotoNaskhArabic-Bold.ttf"
    ),
    "Amiri-Regular.ttf": "https://cdn.jsdelivr.net/fontsource/fonts/amiri@5.0.8/arabic-400-normal.ttf",
    "Cairo-Regular.ttf": "https://cdn.jsdelivr.net/fontsource/fonts/cairo@5.0.8/arabic-400-normal.ttf",
    "Cairo-Bold.ttf": "https://cdn.jsdelivr.net/fontsource/fonts/cairo@5.0.8/arabic-700-normal.ttf",
}


def main() -> None:
    FONTS_DIR.mkdir(parents=True, exist_ok=True)
    for name, url in FONT_URLS.items():
        dest = FONTS_DIR / name
        if dest.exists() and dest.stat().st_size > 10_000:
            print(f"skip {name}")
            continue
        print(f"download {name}...")
        try:
            urllib.request.urlretrieve(url, dest)
            print(f"  ok ({dest.stat().st_size} bytes)")
        except Exception as exc:
            print(f"  failed: {exc}")


if __name__ == "__main__":
    main()
