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

import json

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

    def __init__(self, settings: QSettings | None = None, secrets=None):
        # `settings`/`secrets` are injection seams for tests: without them a
        # test run reads AND writes the user's real store (HKCU\Software\
        # OmniAwareFox\DesktopPet on Windows), which both pollutes their app
        # and makes the suite order-dependent.
        self._s = settings if settings is not None else QSettings(ORG, APP)
        self._secrets = secrets if secrets is not None else default_secret_store()

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

    # ── console chrome state (D3) — mirrors the web's localStorage keys ──
    def breach_seen_seq(self) -> int:
        """Highest breach seq the user has already looked at (web:
        `foxy_breach_seen`). Everything above it counts toward the pip."""
        return self._s.value("chrome/breach_seen_seq", 0, type=int)

    def set_breach_seen_seq(self, seq: int):
        self._s.setValue("chrome/breach_seen_seq", int(seq))

    def dismissed_announcements(self) -> dict:
        """Per-id banner dismissals (web: `foxy_dash_ann_dismissed`). A banner
        the user closed must never reappear for the same id."""
        raw = self._s.value("chrome/ann_dismissed", "", type=str)
        try:
            data = json.loads(raw) if raw else {}
        except ValueError:
            return {}
        return data if isinstance(data, dict) else {}

    def dismiss_announcement(self, ident: str):
        data = self.dismissed_announcements()
        data[str(ident)] = 1
        self._s.setValue("chrome/ann_dismissed", json.dumps(data))

    # ── companion alerts (D12; D13 gives these their Settings UI) ──
    # Every default is what the companion ALREADY did before the toggles
    # existed: react to every breach, with a beep and a native toast. The
    # three new sources default on for the same reason the web's breach-alert
    # preference does — a compliance companion that stays quiet about running
    # out of capture credits is not doing its job. Adding the keys changes no
    # behaviour on its own; D13 adds the UI that can turn them off.
    def breach_alerts_enabled(self) -> bool:
        return self._s.value("alerts/breach", True, type=bool)

    def set_breach_alerts_enabled(self, value: bool):
        self._s.setValue("alerts/breach", bool(value))

    def alert_min_risk(self) -> int:
        """Below this a breach is still real and still in the ledger — the fox
        simply does not interrupt for it. 0 = interrupt for all."""
        return max(0, min(100, self._s.value("alerts/min_risk", 0, type=int)))

    def set_alert_min_risk(self, value: int):
        self._s.setValue("alerts/min_risk", max(0, min(100, int(value))))

    def alert_sound_enabled(self) -> bool:
        return self._s.value("alerts/sound", True, type=bool)

    def set_alert_sound_enabled(self, value: bool):
        self._s.setValue("alerts/sound", bool(value))

    def native_toasts_enabled(self) -> bool:
        return self._s.value("alerts/toasts", True, type=bool)

    def set_native_toasts_enabled(self, value: bool):
        self._s.setValue("alerts/toasts", bool(value))

    def quota_alerts_enabled(self) -> bool:
        return self._s.value("alerts/quota", True, type=bool)

    def set_quota_alerts_enabled(self, value: bool):
        self._s.setValue("alerts/quota", bool(value))

    def anchor_alerts_enabled(self) -> bool:
        return self._s.value("alerts/anchor", True, type=bool)

    def set_anchor_alerts_enabled(self, value: bool):
        self._s.setValue("alerts/anchor", bool(value))

    def grading_alerts_enabled(self) -> bool:
        return self._s.value("alerts/grading", True, type=bool)

    def set_grading_alerts_enabled(self, value: bool):
        self._s.setValue("alerts/grading", bool(value))

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
