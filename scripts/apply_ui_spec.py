"""Apply PRODUCT_SPEC_AR checklist UI changes to index.html."""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
p = ROOT / "templates" / "index.html"
text = p.read_text(encoding="utf-8")

old_block = """        <div class="mb-4">
          <label for="content_profile" class="block text-white text-sm font-bold mb-2">نوع المحتوى</label>
          <select name="content_profile" id="content_profile"
                  class="form-select form-select-rtl w-full rounded-xl text-white bg-[#2d2938] h-14 focus:outline-none focus:ring-2 focus:ring-emerald-400/40"
                  title="نوع المحتوى">
            <option value="auto" {% if settings.content_profile|default('auto') == 'auto' %}selected{% endif %}>تلقائي (يكتشف من الموضوع)</option>
            <option value="educational" {% if settings.content_profile|default('auto') == 'educational' %}selected{% endif %}>تعليمي / تقني (BTEC, Flutter...)</option>
            <option value="islamic_story" {% if settings.content_profile|default('auto') == 'islamic_story' %}selected{% endif %}>قصة إسلامية (AI Images)</option>
            <option value="general" {% if settings.content_profile|default('auto') == 'general' %}selected{% endif %}>عام</option>
          </select>
          <p class="text-[#a59db8] text-xs mt-2">للقصص الإسلامية: يُقسّم السيناريو زمنياً ويُولّد صور AI بدل فيديوهات stock العشوائية.</p>
        </div>

"""
if old_block in text:
    text = text.replace(old_block, "")
    print("removed content_profile select")
else:
    print("content_profile block already removed or not found")

text = text.replace(
    '<div class="mb-4">\n          <label for="script_source"',
    '<div class="mb-4 advanced-only">\n          <label for="script_source"',
    1,
)
text = text.replace(
    'id="islamic_options_box" class="mb-4 space-y-3',
    'id="islamic_options_box" class="advanced-only mb-4 space-y-3',
    1,
)
text = text.replace("settings.script_source|default('scenes')", "settings.script_source|default('auto')")

for el_id in (
    "research_preview_box",
    "quality_gate_box",
    "scenes_script_box",
    "scenes_box",
    "custom_script_box",
):
    text = text.replace(f'id="{el_id}" class="', f'id="{el_id}" class="advanced-only ', 1)

# caption block: the mb-4 before caption_enabled
text = text.replace(
    '<div class="mb-4">\n          <label for="caption_enabled"',
    '<div class="mb-4 advanced-only">\n          <label for="caption_enabled"',
    1,
)
text = text.replace(
    'id="caption_settings" class="space-y-4',
    'id="caption_settings" class="advanced-only space-y-4',
    1,
)
text = text.replace(
    '<label for="media_source" class="block text-white text-sm font-bold mb-2">',
    '<div class="mb-4 advanced-only"><label for="media_source" class="block text-white text-sm font-bold mb-2">',
    1,
)
# close extra div after media_source select block
text = text.replace(
    """          </select>
        </div>

        <div class="mb-4 advanced-only">
          <label for="caption_enabled\"""",
    """          </select>
        </div></div>

        <div class="mb-4 advanced-only">
          <label for="caption_enabled\"""",
    1,
)

# quality_gate_enabled checkbox after force_fresh_media
qg = """            <label class="flex items-center gap-2 text-sm text-violet-200 font-semibold">
              <input type="checkbox" name="quality_gate_enabled" id="quality_gate_enabled" class="rounded"
                {% if settings.quality_gate_enabled|default(true) %}checked{% endif %} />
              🛡️ Quality Gate — فحص جودة الصور قبل الترميز
            </label>
"""
if "id=\"quality_gate_enabled\"" not in text:
    text = text.replace(
        """              🧪 اختبار: وسائط جديدة 100% (تعطيل Cache + FLUX فعلي)
            </label>
            <label class="flex items-center gap-2 text-sm text-[#cbd5e1]">
              <input type="checkbox" name="hook_scene\"""",
        """              🧪 اختبار: وسائط جديدة 100% (تعطيل Cache + FLUX فعلي)
            </label>
"""
        + qg
        + """            <label class="flex items-center gap-2 text-sm text-[#cbd5e1]">
              <input type="checkbox" name="hook_scene\"""",
        1,
    )
    print("added quality_gate_enabled checkbox")

# CSS for advanced-only
css = """
    .ui-mode-beginner .advanced-only { display: none !important; }
"""
if ".ui-mode-beginner .advanced-only" not in text:
    text = text.replace("</style>", css + "  </style>", 1)

# JS: ui mode toggle + beginner defaults
js_toggle = """
    // ---- UI mode: beginner / advanced (PRODUCT_SPEC_AR) ----
    const uiModeBeginnerBtn = document.getElementById("ui_mode_beginner");
    const uiModeAdvancedBtn = document.getElementById("ui_mode_advanced");
    let uiMode = localStorage.getItem("ui_mode") || "beginner";

    function applyUiMode(mode) {
      uiMode = mode === "advanced" ? "advanced" : "beginner";
      localStorage.setItem("ui_mode", uiMode);
      document.querySelector(".app-shell")?.classList.toggle("ui-mode-beginner", uiMode === "beginner");
      const on = "rounded-full h-10 px-5 text-sm font-bold text-white ring-2 ";
      const off = "rounded-full h-10 px-5 text-sm font-bold text-white ";
      if (uiModeBeginnerBtn) {
        uiModeBeginnerBtn.className = off + (uiMode === "beginner" ? "bg-emerald-600 ring-emerald-400/60" : "bg-slate-700 hover:bg-slate-600");
      }
      if (uiModeAdvancedBtn) {
        uiModeAdvancedBtn.className = off + (uiMode === "advanced" ? "bg-indigo-600 ring-indigo-400/60" : "bg-slate-700 hover:bg-slate-600");
      }
    }
    if (uiModeBeginnerBtn) uiModeBeginnerBtn.onclick = () => applyUiMode("beginner");
    if (uiModeAdvancedBtn) uiModeAdvancedBtn.onclick = () => applyUiMode("advanced");
    applyUiMode(uiMode);

    function applyBeginnerFormDefaults(fd) {
      if (uiMode !== "beginner") return;
      fd.set("content_profile", "islamic_story");
      fd.set("script_source", "auto");
    }
"""
if "applyBeginnerFormDefaults" not in text:
    text = text.replace("    function appendIslamicOptions(fd) {", js_toggle + "\n    function appendIslamicOptions(fd) {", 1)

js_append = """
      const qgate = document.getElementById("quality_gate_enabled");
      if (qgate) fd.set("quality_gate_enabled", qgate.checked ? "true" : "false");
"""
if 'getElementById("quality_gate_enabled")' not in text:
    text = text.replace(
        '      if (lesson) fd.set("lesson_summary", lesson.checked ? "true" : "false");\n    }',
        '      if (lesson) fd.set("lesson_summary", lesson.checked ? "true" : "false");\n'
        + js_append
        + "    }",
        1,
    )

for fn in ("saveCurrentSettings", "form.onsubmit"):
    pass

# inject applyBeginnerFormDefaults in save and submit
text = text.replace(
    "      appendIslamicOptions(fd);\n      // add YouTube fields",
    "      appendIslamicOptions(fd);\n      applyBeginnerFormDefaults(fd);\n      // add YouTube fields",
    1,
)
text = text.replace(
    "      appendIslamicOptions(fd);\n      fd.append(\"upload_enabled\"",
    "      appendIslamicOptions(fd);\n      applyBeginnerFormDefaults(fd);\n      fd.append(\"upload_enabled\"",
    1,
)

# hidden script_source for beginner when select hidden
if 'id="script_source_beginner"' not in text:
    text = text.replace(
        '<input type="hidden" name="content_profile" id="content_profile" value="islamic_story" />',
        '<input type="hidden" name="content_profile" id="content_profile" value="islamic_story" />\n'
        '        <input type="hidden" id="script_source_beginner" value="auto" />',
        1,
    )

p.write_text(text, encoding="utf-8")
print("index.html updated")
