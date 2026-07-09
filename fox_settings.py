"""
Theme + persistent settings layer for the OmniAware Fox desktop pet.

- FoxSettings wraps QSettings (OS-native storage: registry / plist / .ini)
- THEMES holds the full design tokens for the app's single visual theme
  (claymorphism, matching the web app). It's token-driven so a new theme is one
  fully-populated entry away, but the look is intentionally constant — there is no
  in-app theme picker.

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

    # ── Foxy Audit platform ──
    def org_api_key(self) -> str:
        return self._s.value("foxy/org_key", "", type=str)

    def set_org_api_key(self, key: str):
        self._s.setValue("foxy/org_key", key)

    def backend_url(self) -> str:
        return self._s.value("foxy/backend_url", "https://api.foxyaudit.dev", type=str)

    def set_backend_url(self, url: str):
        self._s.setValue("foxy/backend_url", url)

    def web_dashboard_url(self) -> str:
        """The browser dashboard (site 2), distinct from the desktop console."""
        return self._s.value("foxy/web_dashboard_url",
                             "https://app.foxyaudit.tech/dashboard", type=str)

    def set_web_dashboard_url(self, url: str):
        self._s.setValue("foxy/web_dashboard_url", url)

    # ── behaviour tuning ──
    def reaction_cooldown(self) -> float:
        return self._s.value("behavior/cooldown", 4.0, type=float)

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
