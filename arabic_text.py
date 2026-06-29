from __future__ import annotations

import re
from pathlib import Path
from typing import Literal

from PIL import ImageFont

ROOT = Path(__file__).resolve().parent
FONTS_DIR = ROOT / "resources" / "fonts"

FontRole = Literal["body", "title", "quran", "caption"]

FONT_CANDIDATES: dict[FontRole, list[str]] = {
    "quran": [
        "Amiri-Regular.ttf",
        "NotoNaskhArabic-Regular.ttf",
        "Cairo-Regular.ttf",
        "font.ttf",
    ],
    "title": [
        "Cairo-Bold.ttf",
        "Cairo-SemiBold.ttf",
        "Cairo-Regular.ttf",
        "NotoNaskhArabic-Bold.ttf",
        "NotoNaskhArabic-Regular.ttf",
        "font.ttf",
    ],
    "body": [
        "Cairo-Regular.ttf",
        "NotoNaskhArabic-Regular.ttf",
        "Amiri-Regular.ttf",
        "font.ttf",
    ],
    "caption": [
        "Cairo-Regular.ttf",
        "NotoNaskhArabic-Regular.ttf",
        "font.ttf",
    ],
}

_ARABIC_RE = re.compile(r"[\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF\uFB50-\uFDFF\uFE70-\uFEFF]")
_BIDI_AVAILABLE = False
_RESHAPER_AVAILABLE = False

try:
    from arabic_reshaper.arabic_reshaper import ArabicReshaper

    _ARABIC_RESHAPER = ArabicReshaper(
        configuration={
            "delete_harakat": False,
            "support_ligatures": True,
        }
    )
    _RESHAPER_AVAILABLE = True
except ImportError:
    ArabicReshaper = None  # type: ignore[misc, assignment]
    _ARABIC_RESHAPER = None
    _RESHAPER_AVAILABLE = False

try:
    from bidi.algorithm import get_display as _bidi_display

    _BIDI_AVAILABLE = True
except ImportError:
    _bidi_display = None  # type: ignore[assignment]


def contains_arabic(text: str) -> bool:
    return bool(_ARABIC_RE.search(text or ""))


def prepare_arabic(text: str, *, for_display: bool = True) -> str:
    """Reshape and apply bidi so Arabic renders correctly in PIL (RTL, connected letters)."""
    raw = (text or "").strip()
    if not raw or not for_display:
        return raw
    if not contains_arabic(raw):
        return raw
    if _RESHAPER_AVAILABLE and _BIDI_AVAILABLE and _ARABIC_RESHAPER and _bidi_display:
        try:
            reshaped = _ARABIC_RESHAPER.reshape(raw)
            return str(_bidi_display(reshaped))
        except Exception:
            pass
    return raw


def line_width(font: ImageFont.FreeTypeFont | ImageFont.ImageFont, text: str) -> int:
    display = prepare_arabic(text)
    bbox = font.getbbox(display)
    return int(bbox[2] - bbox[0])


def wrap_arabic(
    text: str,
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    max_width: int,
    *,
    max_lines: int | None = None,
) -> list[str]:
    """Wrap Arabic (or mixed) text by measuring rendered width after shaping."""
    raw = (text or "").strip()
    if not raw:
        return []
    if max_width <= 0:
        return [prepare_arabic(raw)]

    words = raw.split()
    if not words:
        return [prepare_arabic(raw)]

    lines: list[str] = []
    current: list[str] = []

    for word in words:
        trial_words = current + [word]
        trial_raw = " ".join(trial_words)
        if line_width(font, trial_raw) <= max_width:
            current = trial_words
            continue
        if current:
            lines.append(prepare_arabic(" ".join(current)))
            if max_lines and len(lines) >= max_lines:
                return lines
            current = [word]
        else:
            # Single very long token — hard-split by characters
            chunk = ""
            for ch in word:
                test = chunk + ch
                if line_width(font, test) <= max_width:
                    chunk = test
                else:
                    if chunk:
                        lines.append(prepare_arabic(chunk))
                        if max_lines and len(lines) >= max_lines:
                            return lines
                    chunk = ch
            if chunk:
                current = [chunk]

    if current:
        lines.append(prepare_arabic(" ".join(current)))
    if max_lines:
        return lines[:max_lines]
    return lines


def resolve_font_path(role: FontRole = "body", explicit: str | None = None) -> Path | None:
    if explicit:
        candidates = [FONTS_DIR / explicit, ROOT / explicit]
    else:
        candidates = [FONTS_DIR / name for name in FONT_CANDIDATES.get(role, FONT_CANDIDATES["body"])]
    candidates.extend(
        [
            Path("C:/Windows/Fonts/trado.ttf"),
            Path("C:/Windows/Fonts/tahoma.ttf"),
        ]
    )
    for path in candidates:
        if path.exists() and path.is_file():
            return path
    return None


def load_arabic_font(
    size: int,
    role: FontRole = "body",
    explicit: str | None = None,
) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    path = resolve_font_path(role, explicit)
    if path:
        try:
            return ImageFont.truetype(str(path), size)
        except OSError:
            pass
    return ImageFont.load_default()


def format_quran_verse(verse: str) -> str:
    text = (verse or "").strip()
    if not text:
        return ""
    if "﴿" in text or "»" in text:
        return prepare_arabic(text)
    return prepare_arabic(f"﴿ {text} ﴾")
