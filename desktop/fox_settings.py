"""
Persistent settings layer for the Foxy Audit desktop app.

FoxSettings wraps QSettings (OS-native storage: registry / plist / .ini) with
typed getters.  Design tokens / fonts / QSS live in foxy_tokens.py — the one
design source — not here.

Secrets (the org API key and per-provider AI keys) are stored in the OS
keychain via foxy_client's secret store, NEVER in QSettings.  Values written
by older builds into QSettings are migrated to the keychain on first read and
removed from QSettings.

Window geometry (pet position, console position/size, chat size) persists
under the geometry/* keys — plain coordinates, no secrets.
"""

from __future__ import annotations

from PyQt6.QtCore import QByteArray, QPoint, QSettings, QSize

from foxy_client import default_secret_store
from foxy_tokens import resource_path  # noqa: F401  (legacy import site: `from fox_settings import resource_path`)

ORG = "OmniAwareFox"
APP = "DesktopPet"


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
        self._secrets = default_secret_store()

    # ── secret plumbing (keychain-first, one-time migration from QSettings) ──
    # The legacy QSettings copy is removed ONLY when the secret has landed in a
    # PERSISTENT store (the real keychain).  The in-memory fallback store also
    # reports set()=True, but destroying the only durable copy after migrating
    # into process memory would lose the key on the next launch.
    def _durable(self) -> bool:
        return getattr(self._secrets, "persistent", False)

    def _get_secret(self, name: str, legacy_key: str) -> str:
        val = self._secrets.get(name)
        if val:
            if self._durable() and self._s.contains(legacy_key):
                self._s.remove(legacy_key)  # scrub any stale plaintext copy
            return val
        legacy = self._s.value(legacy_key, "", type=str)
        if legacy:
            if self._secrets.set(name, legacy) and self._durable():
                self._s.remove(legacy_key)
            return legacy
        return ""

    def _set_secret(self, name: str, legacy_key: str, value: str) -> bool:
        """Store (or clear) a secret. Returns False when the keychain refused
        the write — the caller MUST surface that, because the value is not
        persisted and we never fall back to plaintext QSettings."""
        value = value or ""
        if value:
            stored = self._secrets.set(name, value)
            if stored and self._durable():
                self._s.remove(legacy_key)
            # A non-durable store (no keychain backend) holds the value only in
            # process memory — report that as a failure to persist.
            return bool(stored and self._durable())
        self._secrets.delete(name)
        self._s.remove(legacy_key)
        return True

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
        return self._get_secret(f"ai_key_{provider}", f"ai/key/{provider}")

    def set_api_key(self, provider: str, key: str) -> bool:
        return self._set_secret(f"ai_key_{provider}", f"ai/key/{provider}", key)

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
        return self._get_secret("org_api_key", "foxy/org_key")

    def set_org_api_key(self, key: str) -> bool:
        return self._set_secret("org_api_key", "foxy/org_key", key)

    def backend_url(self) -> str:
        return self._s.value("foxy/backend_url", "https://app.foxyaudit.tech", type=str)

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

    # ── window geometry (no secrets — plain coordinates) ──
    def pet_pos(self) -> QPoint | None:
        """Where the user last placed the fox, or None if never placed."""
        val = self._s.value("geometry/pet_pos")
        return val if isinstance(val, QPoint) else None

    def set_pet_pos(self, pos: QPoint):
        self._s.setValue("geometry/pet_pos", pos)

    def clear_pet_pos(self):
        self._s.remove("geometry/pet_pos")

    def console_geometry(self) -> QByteArray | None:
        val = self._s.value("geometry/console")
        return val if isinstance(val, QByteArray) and not val.isEmpty() else None

    def set_console_geometry(self, geo: QByteArray):
        self._s.setValue("geometry/console", geo)

    def chat_size(self) -> QSize | None:
        val = self._s.value("geometry/chat_size")
        return val if isinstance(val, QSize) and val.isValid() else None

    def set_chat_size(self, size: QSize):
        self._s.setValue("geometry/chat_size", size)

    # ── weekly-summary counters (previously raw QSettings pokes) ──
    def weekly_breaches(self) -> int:
        return self._s.value("weekly/breaches", 0, type=int)

    def bump_weekly_breaches(self):
        self._s.setValue("weekly/breaches", self.weekly_breaches() + 1)

    def reset_weekly_breaches(self):
        self._s.setValue("weekly/breaches", 0)

    def weekly_last_summary(self) -> str:
        return self._s.value("weekly/last_summary", "", type=str)

    def set_weekly_last_summary(self, iso_ts: str):
        self._s.setValue("weekly/last_summary", iso_ts)
