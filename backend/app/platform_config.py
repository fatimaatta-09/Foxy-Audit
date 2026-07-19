"""Platform config overrides (P3 · §F).

Effective value = the override persisted in ``platform_config`` if present, else the
live ``Settings`` default. Only overrides are stored, so GET always reflects the real
running configuration. These are consumed by ADMIN routers (default quotas when a
staff applies a plan; the anchor-stale alert threshold), so edits take genuine effect
within the ops platform — nothing here is inert/fake.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import Settings, get_settings
from .models import PlatformConfig

# Editable keys -> metadata. `default` is read live from Settings (below) so the API
# never invents a value. Add a key here AND make sure some admin router reads it.
CONFIG_SCHEMA: dict[str, dict] = {
    "anchor_stale_alert_seconds": {
        "type": "int", "group": "Alerts", "min": 0,
        "label": "Anchor-stale alert threshold (seconds; 0 = off)",
        "help": "Raise a warning alert when an org's last confirmed anchor is older than this.",
    },
    "monthly_quota_free": {
        "type": "int", "group": "Default quotas", "min": 0,
        "label": "Free plan monthly quota",
        "help": "Applied when a staff sets a plan without an explicit quota override.",
    },
    "monthly_quota_pro": {
        "type": "int", "group": "Default quotas", "min": 0,
        "label": "Pro plan monthly quota",
    },
    "monthly_quota_max": {
        "type": "int", "group": "Default quotas", "min": 0,
        "label": "Max plan monthly quota",
    },
    "monthly_quota_premium": {
        "type": "int", "group": "Default quotas", "min": 0,
        "label": "Premium plan monthly quota (0 = unlimited by contract)",
    },
}


def get_int(db: Session, key: str, fallback: int) -> int:
    """Effective integer for a config key (override else fallback)."""
    row = db.get(PlatformConfig, key)
    if row is not None and row.value is not None:
        try:
            return int(row.value)
        except (TypeError, ValueError):
            return fallback
    return fallback


def effective_quota(db: Session, plan: str, settings: Settings) -> int | None:
    """The default monthly quota to apply for a plan — a config override if set,
    otherwise the Settings default. Mirrors Settings.quota_for()."""
    default = settings.quota_for(plan)
    canonical = settings.canonical_plan(plan)
    key = f"monthly_quota_{canonical}"
    if key in CONFIG_SCHEMA and default is not None:
        return get_int(db, key, default)
    return default


def effective(db: Session, settings: Settings | None = None) -> dict:
    """Every editable key with its effective value + default + override flag + meta."""
    settings = settings or get_settings()
    overrides = {r.key: r.value for r in db.execute(select(PlatformConfig)).scalars().all()}
    out: dict[str, dict] = {}
    for key, meta in CONFIG_SCHEMA.items():
        default = getattr(settings, key, None)
        overridden = key in overrides and overrides[key] is not None
        out[key] = {
            "value": overrides[key] if overridden else default,
            "default": default,
            "overridden": overridden,
            **meta,
        }
    return out


def _coerce(meta: dict, raw):
    if meta["type"] == "int":
        try:
            val = int(raw)
        except (TypeError, ValueError):
            raise ValueError(f"expected an integer, got {raw!r}")
        if val < meta.get("min", -(10**18)):
            raise ValueError(f"value {val} is below the minimum {meta.get('min')}")
        return val
    return raw


def set_values(db: Session, updates: dict, staff_id) -> list[str]:
    """Validate + upsert overrides. Raises ValueError on an unknown key or bad type.
    Does NOT commit — the caller commits together with the audit row."""
    changed: list[str] = []
    for key, raw in updates.items():
        if key not in CONFIG_SCHEMA:
            raise ValueError(f"unknown config key: {key}")
        val = _coerce(CONFIG_SCHEMA[key], raw)
        row = db.get(PlatformConfig, key)
        if row is None:
            row = PlatformConfig(key=key)
            db.add(row)
        row.value = val
        row.updated_by = staff_id
        changed.append(key)
    return changed
