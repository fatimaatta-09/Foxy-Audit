"""Every payment setting must actually reach the container.

WRITTEN BY MAIN AT THE DEPLOYMENT GATE, not by an executor.

`deploy/docker-compose.prod.yml` declares an explicit `environment:` block, which
is an ALLOWLIST: a variable sitting in `deploy/.env` that is not named there never
reaches the process. Nothing enforced the two staying in step.

M2 added five `paddle_*` settings to `config.py` and to neither file. The owner
filled in `deploy/.env` correctly, deployed, and got a healthy backend with zero
Paddle configuration — an upgrade page listing no plans and a checkout that could
not be created. `printenv | grep -c PADDLE_` inside the container returned 0.

The failure mode is what makes it worth a guard: everything reports success. The
build succeeds, `/health/ready` is green, the settings all hold their defaults,
and the only symptom is a feature quietly behaving as though it were switched off.

Scoped to the payment settings rather than every field in `Settings`, because
plenty of settings are deliberately container-side defaults and a guard that
demands all of them would be noise. These are the ones whose absence is
indistinguishable from "not configured yet".
"""
from __future__ import annotations

import pathlib
import re

from app.config import Settings

_REPO = pathlib.Path(__file__).resolve().parents[3]
_COMPOSE = _REPO / "deploy" / "docker-compose.prod.yml"
_ENV_EXAMPLE = _REPO / "deploy" / ".env.example"

#: The prefixes whose absence looks exactly like "the owner has not set this up".
_PAYMENT_PREFIXES = ("paddle_", "stripe_")


def _payment_settings() -> set[str]:
    """Every payment-related field on Settings, as its ENV_VAR name."""
    return {
        name.upper()
        for name in Settings.model_fields
        if name.startswith(_PAYMENT_PREFIXES)
    }


def _declared_in_compose() -> set[str]:
    text = _COMPOSE.read_text(encoding="utf-8")
    # `- FOO=${FOO:-}` — the name to the LEFT of the '=' is what the container sees.
    return set(re.findall(r"^\s*-\s+([A-Z_][A-Z0-9_]*)=", text, re.M))


def test_every_payment_setting_is_passed_into_the_container() -> None:
    missing = sorted(_payment_settings() - _declared_in_compose())
    assert not missing, (
        "these payment settings exist in config.py but are NOT in the compose "
        "environment allowlist, so a filled-in deploy/.env cannot reach the "
        f"backend and the feature silently behaves as unconfigured: {missing}"
    )


def test_every_payment_setting_is_documented_in_env_example() -> None:
    """An operator cannot set a variable nobody told them about."""
    documented = set(re.findall(r"^([A-Z_][A-Z0-9_]*)=",
                                _ENV_EXAMPLE.read_text(encoding="utf-8"), re.M))
    missing = sorted(_payment_settings() - documented)
    assert not missing, (
        "these payment settings are undocumented in deploy/.env.example, so an "
        f"operator has no way to know they exist: {missing}"
    )


def test_the_guard_can_actually_see_the_compose_declarations() -> None:
    """The guard above passes trivially if the regex matches nothing.

    Both assertions are of the form "set difference is empty", which is satisfied
    just as well by an empty right-hand side that never parsed. Assert the parse
    found something real before trusting either.
    """
    declared = _declared_in_compose()
    assert len(declared) > 20, f"compose parse looks wrong — only {len(declared)} vars"
    assert "DATABASE_URL" in declared, "a known variable is missing from the parse"
