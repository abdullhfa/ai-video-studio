from __future__ import annotations

VISUAL_STYLE_PACKS: dict[str, str] = {
    "cinematic_islamic": (
        "Ancient Arabian atmosphere, golden cinematic lighting, consistent color grading, "
        "historical Islamic setting, high detail, warm desert tones, film still quality"
    ),
    "documentary": (
        "Documentary photography style, natural lighting, muted earth tones, "
        "consistent grading, photorealistic, high detail"
    ),
    "watercolor": (
        "Soft watercolor illustration, pastel palette, consistent brush style, "
        "gentle lighting, artistic historical scene"
    ),
    "none": "",
}

DEFAULT_VISUAL_STYLE = "cinematic_islamic"


def normalize_visual_style(name: str | None) -> str:
    key = (name or DEFAULT_VISUAL_STYLE).strip().lower()
    if key in VISUAL_STYLE_PACKS:
        return key
    if key in {"", "default", "auto"}:
        return DEFAULT_VISUAL_STYLE
    return DEFAULT_VISUAL_STYLE


def style_suffix(style_name: str | None) -> str:
    key = normalize_visual_style(style_name)
    return VISUAL_STYLE_PACKS.get(key, VISUAL_STYLE_PACKS[DEFAULT_VISUAL_STYLE])


def append_visual_style(prompt: str, style_name: str | None) -> str:
    base = (prompt or "").strip().rstrip(",")
    suffix = style_suffix(style_name).strip()
    if not suffix:
        return base
    if suffix.lower() in base.lower():
        return base
    if not base:
        return suffix
    return f"{base}, {suffix}"
