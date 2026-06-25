"""
Theme + persistent settings layer for the OmniAware Fox desktop pet.

- FoxSettings wraps QSettings (OS-native storage: registry / plist / .ini)
- THEMES holds the full design tokens for 14 distinct visual themes.
  Every theme exposes exactly the same keys so calling code never needs to
  special-case any particular style.

Shadow/border model
───────────────────
  shadow_blur   QGraphicsDropShadowEffect blurRadius (0 = hard neobrutalist/pixel)
  shadow_alpha  0-255 opacity of the drop-shadow colour
  shadow_dx     horizontal shadow offset  (negative = left)
  shadow_dy     vertical   shadow offset
  shadow_color  (r,g,b) tuple — lets Cyberpunk use coloured glow, Neobrutalism
                use pure black, Clay use warm brown, etc.

Input/border model
──────────────────
  border        CSS shorthand string used in QSS (e.g. "3px solid #000")
  radius        corner radius in px  (0 for sharp themes)
  input_border  overrides 'border' for the text input field only if present
  outline_focus CSS colour used for :focus outlines where applicable

Typography
──────────
  font          primary font-family name
  font_mono     monospace override for code-like widgets

Palette
───────
  bg            window / panel background
  panel         slightly-raised card surface inside the main panel
  accent        primary interactive colour (buttons, active borders)
  accent_dark   hover / pressed accent
  accent_glow   glow colour for neon themes (optional; fallback = accent)
  text          primary foreground
  text_muted    secondary / placeholder text
  bubble_user   user message bubble background
  bubble_fox    fox message bubble background
  header_bg     top-bar colour (if different from bg)
  header_text   top-bar text colour (if different from text)
"""

from PyQt6.QtCore import QSettings

ORG = "OmniAwareFox"
APP = "DesktopPet"

# ──────────────────────────────────────────────────────────────────── THEMES ──
THEMES: dict[str, dict] = {

    # ── 1 · Claymorphism  ────────────────────────────────────────────────────
    "claymorphism": {
        "label": "🧸 Claymorphism",
        "bg":          "#F4EFE8", "panel":        "#FBF6EE",
        "accent":      "#FF9F66", "accent_dark":  "#E8854A",
        "accent_glow": "#FF9F66",
        "text":        "#5A4A3C", "text_muted":   "#A08070",
        "bubble_user": "#FFD9B8", "bubble_fox":   "#FFFFFF",
        "header_bg":   "#FBF6EE", "header_text":  "#5A4A3C",
        "radius":      28,
        "border":      "none",
        "input_border": "none",
        "outline_focus": "#FF9F66",
        "font":        "Segoe UI", "font_mono":   "Consolas",
        "shadow_blur": 24, "shadow_alpha": 70,
        "shadow_dx": 0, "shadow_dy": 10,
        "shadow_color": (80, 50, 20),
    },

    # ── 2 · Glassmorphism  ───────────────────────────────────────────────────
    "glassmorphism": {
        "label": "🪟 Glassmorphism",
        "bg":          "rgba(255,255,255,50)",  "panel":       "rgba(255,255,255,80)",
        "accent":      "#7DD3FC",               "accent_dark": "#38BDF8",
        "accent_glow": "#7DD3FC",
        "text":        "#1E293B",               "text_muted":  "#64748B",
        "bubble_user": "rgba(125,211,252,160)", "bubble_fox":  "rgba(255,255,255,140)",
        "header_bg":   "rgba(255,255,255,60)",  "header_text": "#1E293B",
        "radius":      24,
        "border":      "1px solid rgba(255,255,255,130)",
        "input_border": "1px solid rgba(125,211,252,120)",
        "outline_focus": "#7DD3FC",
        "font":        "Segoe UI",   "font_mono": "Consolas",
        "shadow_blur": 32, "shadow_alpha": 50,
        "shadow_dx": 0, "shadow_dy": 8,
        "shadow_color": (30, 60, 120),
    },

    # ── 3 · Neobrutalism  ────────────────────────────────────────────────────
    "neobrutalism": {
        "label": "💥 Neobrutalism",
        "bg":          "#FFEE00",  "panel":       "#FFFFFF",
        "accent":      "#FF2D78",  "accent_dark": "#C9004A",
        "accent_glow": "#FF2D78",
        "text":        "#000000",  "text_muted":  "#444444",
        "bubble_user": "#FF2D78",  "bubble_fox":  "#0A0A0A",
        "header_bg":   "#000000",  "header_text": "#FFEE00",
        "radius":      0,
        "border":      "3px solid #000000",
        "input_border": "3px solid #000000",
        "outline_focus": "#FF2D78",
        "font":        "Arial Black", "font_mono": "Courier New",
        # Hard drop-shadow: 0 blur, large offset = signature brutalist box-shadow
        "shadow_blur": 0, "shadow_alpha": 255,
        "shadow_dx": 6, "shadow_dy": 6,
        "shadow_color": (0, 0, 0),
    },

    # ── 4 · Cyberpunk 2077  ──────────────────────────────────────────────────
    "cyberpunk": {
        "label": "⚡ Cyberpunk 2077",
        "bg":          "#0D0208",  "panel":       "#110312",
        "accent":      "#FFE800",  "accent_dark": "#CCBB00",
        "accent_glow": "#00FFF0",
        "text":        "#FFE800",  "text_muted":  "#888888",
        "bubble_user": "#FFE800",  "bubble_fox":  "#1A0421",
        "header_bg":   "#000000",  "header_text": "#FFE800",
        "radius":      0,
        "border":      "1px solid #FFE800",
        "input_border": "1px solid #00FFF0",
        "outline_focus": "#00FFF0",
        "font":        "Rajdhani",  "font_mono": "Share Tech Mono",
        # Coloured glow — the shadow_color drives a cyan bloom
        "shadow_blur": 22, "shadow_alpha": 190,
        "shadow_dx": 0, "shadow_dy": 0,
        "shadow_color": (0, 255, 240),
    },

    # ── 5 · Synthwave / Outrun  ──────────────────────────────────────────────
    "synthwave": {
        "label": "🌆 Synthwave",
        "bg":          "#1A0533",  "panel":       "#240944",
        "accent":      "#FF00C8",  "accent_dark": "#C800A0",
        "accent_glow": "#FF00C8",
        "text":        "#F8D5FF",  "text_muted":  "#9966AA",
        "bubble_user": "#FF00C8",  "bubble_fox":  "#350D5E",
        "header_bg":   "#0F021E",  "header_text": "#FF00C8",
        "radius":      6,
        "border":      "1px solid #FF00C8",
        "input_border": "1px solid #FF00C8",
        "outline_focus": "#00F0FF",
        "font":        "Trebuchet MS", "font_mono": "Courier New",
        "shadow_blur": 28, "shadow_alpha": 200,
        "shadow_dx": 0, "shadow_dy": 0,
        "shadow_color": (255, 0, 200),
    },

    # ── 6 · Nord  ────────────────────────────────────────────────────────────
    "nord": {
        "label": "🧊 Nord",
        "bg":          "#2E3440",  "panel":       "#3B4252",
        "accent":      "#88C0D0",  "accent_dark": "#5E81AC",
        "accent_glow": "#88C0D0",
        "text":        "#ECEFF4",  "text_muted":  "#8892A1",
        "bubble_user": "#5E81AC",  "bubble_fox":  "#3B4252",
        "header_bg":   "#2E3440",  "header_text": "#88C0D0",
        "radius":      10,
        "border":      "1px solid #4C566A",
        "input_border": "1px solid #4C566A",
        "outline_focus": "#88C0D0",
        "font":        "Segoe UI",  "font_mono": "Fira Code",
        "shadow_blur": 20, "shadow_alpha": 90,
        "shadow_dx": 0, "shadow_dy": 6,
        "shadow_color": (20, 30, 48),
    },

    # ── 7 · Dracula  ─────────────────────────────────────────────────────────
    "dracula": {
        "label": "🧛 Dracula",
        "bg":          "#282A36",  "panel":       "#1E1F29",
        "accent":      "#BD93F9",  "accent_dark": "#7756D6",
        "accent_glow": "#BD93F9",
        "text":        "#F8F8F2",  "text_muted":  "#6272A4",
        "bubble_user": "#BD93F9",  "bubble_fox":  "#343746",
        "header_bg":   "#1A1B24",  "header_text": "#BD93F9",
        "radius":      12,
        "border":      "1px solid #44475A",
        "input_border": "1px solid #6272A4",
        "outline_focus": "#FF79C6",
        "font":        "Segoe UI",  "font_mono": "Fira Code",
        "shadow_blur": 20, "shadow_alpha": 130,
        "shadow_dx": 0, "shadow_dy": 8,
        "shadow_color": (40, 20, 80),
    },

    # ── 8 · macOS Monterey  ──────────────────────────────────────────────────
    "macos": {
        "label": "🍎 macOS Monterey",
        "bg":          "#F5F5F7",  "panel":       "#FFFFFF",
        "accent":      "#0071E3",  "accent_dark": "#0051A8",
        "accent_glow": "#0071E3",
        "text":        "#1D1D1F",  "text_muted":  "#86868B",
        "bubble_user": "#0071E3",  "bubble_fox":  "#E8E8ED",
        "header_bg":   "#F5F5F7",  "header_text": "#1D1D1F",
        "radius":      14,
        "border":      "1px solid #D2D2D7",
        "input_border": "1px solid #D2D2D7",
        "outline_focus": "#0071E3",
        "font":        "SF Pro Display", "font_mono": "SF Mono",
        "shadow_blur": 28, "shadow_alpha": 45,
        "shadow_dx": 0, "shadow_dy": 10,
        "shadow_color": (0, 0, 0),
    },

    # ── 9 · Windows 95 (Retro)  ──────────────────────────────────────────────
    "win95": {
        "label": "💾 Windows 95",
        "bg":          "#C0C0C0",  "panel":       "#C0C0C0",
        "accent":      "#000080",  "accent_dark": "#00007A",
        "accent_glow": "#000080",
        "text":        "#000000",  "text_muted":  "#808080",
        "bubble_user": "#000080",  "bubble_fox":  "#FFFFFF",
        "header_bg":   "#000080",  "header_text": "#FFFFFF",
        "radius":      0,
        # Raised 3-D border: mimics Win32 WS_THICKFRAME
        "border":      "2px solid #FFFFFF",           # outer highlight
        "input_border": "2px inset #808080",
        "outline_focus": "#000080",
        "font":        "MS Sans Serif", "font_mono": "Fixedsys",
        "shadow_blur": 0, "shadow_alpha": 0,
        "shadow_dx": 0, "shadow_dy": 0,
        "shadow_color": (0, 0, 0),
    },

    # ── 10 · Skeuomorphism (Leather Notebook)  ───────────────────────────────
    "skeuomorphism": {
        "label": "📒 Skeuomorphism",
        "bg":          "#D9C7A3",  "panel":       "#EFE3C8",
        "accent":      "#B5651D",  "accent_dark": "#8B4A12",
        "accent_glow": "#B5651D",
        "text":        "#3B2A1A",  "text_muted":  "#7A5C3A",
        "bubble_user": "#E8C99B",  "bubble_fox":  "#FFF8E7",
        "header_bg":   "#C8A87A",  "header_text": "#3B2A1A",
        "radius":      14,
        "border":      "2px solid #8B6B3D",
        "input_border": "1px solid #8B6B3D",
        "outline_focus": "#B5651D",
        "font":        "Georgia",  "font_mono": "Courier New",
        "shadow_blur": 16, "shadow_alpha": 120,
        "shadow_dx": 2, "shadow_dy": 5,
        "shadow_color": (60, 30, 10),
    },

    # ── 11 · Pixelmorphism (Dark Pixel Art)  ─────────────────────────────────
    "pixelmorphism": {
        "label": "👾 Pixelmorphism",
        "bg":          "#1B1023",  "panel":       "#2A1736",
        "accent":      "#FF5DA2",  "accent_dark": "#C73E80",
        "accent_glow": "#5DD9FF",
        "text":        "#F2E9FF",  "text_muted":  "#9977BB",
        "bubble_user": "#5DD9FF",  "bubble_fox":  "#3A2752",
        "header_bg":   "#12091A",  "header_text": "#FF5DA2",
        "radius":      0,
        "border":      "3px solid #F2E9FF",
        "input_border": "2px solid #FF5DA2",
        "outline_focus": "#5DD9FF",
        "font":        "Consolas",  "font_mono": "Consolas",
        "shadow_blur": 0, "shadow_alpha": 0,
        "shadow_dx": 0, "shadow_dy": 0,
        "shadow_color": (0, 0, 0),
    },

    # ── 12 · Catppuccin Mocha  ───────────────────────────────────────────────
    "catppuccin": {
        "label": "🐱 Catppuccin Mocha",
        "bg":          "#1E1E2E",  "panel":       "#313244",
        "accent":      "#CBA6F7",  "accent_dark": "#A880F0",
        "accent_glow": "#CBA6F7",
        "text":        "#CDD6F4",  "text_muted":  "#7F849C",
        "bubble_user": "#CBA6F7",  "bubble_fox":  "#45475A",
        "header_bg":   "#181825",  "header_text": "#CDD6F4",
        "radius":      12,
        "border":      "1px solid #45475A",
        "input_border": "1px solid #585B70",
        "outline_focus": "#CBA6F7",
        "font":        "Segoe UI",  "font_mono": "Fira Code",
        "shadow_blur": 18, "shadow_alpha": 140,
        "shadow_dx": 0, "shadow_dy": 8,
        "shadow_color": (10, 10, 30),
    },

    # ── 13 · Solarized Light  ────────────────────────────────────────────────
    "solarized": {
        "label": "☀️ Solarized Light",
        "bg":          "#FDF6E3",  "panel":       "#EEE8D5",
        "accent":      "#268BD2",  "accent_dark": "#1A6EAF",
        "accent_glow": "#268BD2",
        "text":        "#073642",  "text_muted":  "#839496",
        "bubble_user": "#268BD2",  "bubble_fox":  "#EEE8D5",
        "header_bg":   "#EEE8D5",  "header_text": "#073642",
        "radius":      10,
        "border":      "1px solid #93A1A1",
        "input_border": "1px solid #93A1A1",
        "outline_focus": "#268BD2",
        "font":        "Segoe UI",  "font_mono": "Source Code Pro",
        "shadow_blur": 14, "shadow_alpha": 50,
        "shadow_dx": 0, "shadow_dy": 5,
        "shadow_color": (7, 54, 66),
    },

    # ── 14 · Terminal Green (Hacker)  ────────────────────────────────────────
    "terminal": {
        "label": "💻 Terminal Green",
        "bg":          "#0C0C0C",  "panel":       "#111111",
        "accent":      "#00FF41",  "accent_dark": "#00CC34",
        "accent_glow": "#00FF41",
        "text":        "#00FF41",  "text_muted":  "#007A1F",
        "bubble_user": "#00FF41",  "bubble_fox":  "#0A1A0A",
        "header_bg":   "#000000",  "header_text": "#00FF41",
        "radius":      0,
        "border":      "1px solid #00FF41",
        "input_border": "1px solid #00FF41",
        "outline_focus": "#00FF41",
        "font":        "Courier New",  "font_mono": "Courier New",
        "shadow_blur": 18, "shadow_alpha": 180,
        "shadow_dx": 0, "shadow_dy": 0,
        "shadow_color": (0, 255, 65),
    },
}

DEFAULT_THEME = "claymorphism"

# ─────────────────────────────────────────────────────────── AI providers ──
AI_PROVIDERS = ["anthropic", "openai", "ollama", "lmstudio", "custom"]

PROVIDER_DEFAULTS = {
    "anthropic": {"base_url": "https://api.anthropic.com/v1/messages",       "model": "claude-sonnet-4-6",  "local": False},
    "openai":    {"base_url": "https://api.openai.com/v1/chat/completions",   "model": "gpt-4o-mini",        "local": False},
    "ollama":    {"base_url": "http://localhost:11434/api/chat",               "model": "llama3",             "local": True},
    "lmstudio":  {"base_url": "http://localhost:1234/v1/chat/completions",     "model": "local-model",        "local": True},
    "custom":    {"base_url": "",                                              "model": "",                   "local": False},
}


# ────────────────────────────────────────────────────────── FoxSettings ──
class FoxSettings:
    """Typed convenience wrapper around QSettings.
    Safe to instantiate many times — QSettings is cheap."""

    def __init__(self):
        self._s = QSettings(ORG, APP)

    # ── theme ──
    def theme(self) -> str:
        return self._s.value("theme", DEFAULT_THEME, type=str)

    def set_theme(self, name: str):
        if name in THEMES:
            self._s.setValue("theme", name)

    def theme_tokens(self) -> dict:
        t = THEMES.get(self.theme(), THEMES[DEFAULT_THEME])
        # Always guarantee the optional keys exist so callers never KeyError.
        defaults = {
            "accent_glow": t.get("accent", "#888888"),
            "text_muted":  t.get("text",   "#888888"),
            "header_bg":   t.get("bg",     "#FFFFFF"),
            "header_text": t.get("text",   "#000000"),
            "font_mono":   "Consolas",
            "input_border": t.get("border", "none"),
            "outline_focus": t.get("accent", "#888888"),
            "shadow_color": (60, 60, 60),
            "shadow_dx": 0,
        }
        merged = {**defaults, **t}
        return merged

    # ── AI provider / keys ──
    def ai_provider(self) -> str:
        return self._s.value("ai/provider", "anthropic", type=str)

    def set_ai_provider(self, provider: str):
        self._s.setValue("ai/provider", provider)

    def is_local_provider(self, provider: str | None = None) -> bool:
        provider = provider or self.ai_provider()
        return PROVIDER_DEFAULTS.get(provider, {}).get("local", False)

    def api_key(self, provider: str | None = None) -> str:
        provider = provider or self.ai_provider()
        return self._s.value(f"ai/key/{provider}", "", type=str)

    def set_api_key(self, provider: str, key: str):
        self._s.setValue(f"ai/key/{provider}", key)

    def model(self, provider: str | None = None) -> str:
        provider = provider or self.ai_provider()
        default = PROVIDER_DEFAULTS.get(provider, {}).get("model", "")
        return self._s.value(f"ai/model/{provider}", default, type=str)

    def set_model(self, provider: str, model: str):
        self._s.setValue(f"ai/model/{provider}", model)

    def base_url(self, provider: str | None = None) -> str:
        provider = provider or self.ai_provider()
        default = PROVIDER_DEFAULTS.get(provider, {}).get("base_url", "")
        return self._s.value(f"ai/url/{provider}", default, type=str)

    def set_base_url(self, provider: str, url: str):
        self._s.setValue(f"ai/url/{provider}", url)

    # ── behaviour tuning ──
    def reaction_cooldown(self) -> float:
        return self._s.value("behavior/cooldown", 1.5, type=float)

    def set_reaction_cooldown(self, seconds: float):
        self._s.setValue("behavior/cooldown", seconds)

    def pat_sensitivity(self) -> int:
        return self._s.value("behavior/pat_sensitivity", 18, type=int)

    def set_pat_sensitivity(self, value: int):
        self._s.setValue("behavior/pat_sensitivity", value)

    def roaming_enabled(self) -> bool:
        return self._s.value("behavior/roam", False, type=bool)

    def set_roaming_enabled(self, value: bool):
        self._s.setValue("behavior/roam", value)

    def proximity_glance_enabled(self) -> bool:
        return self._s.value("behavior/glance", True, type=bool)

    def set_proximity_glance_enabled(self, value: bool):
        self._s.setValue("behavior/glance", value)
