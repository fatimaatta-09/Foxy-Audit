"""Configuration resolution for the Foxy Audit SDK.

Priority for every setting: explicit kwarg → environment variable → default.
The SDK is a graceful no-op for the HTTP path when no API key is configured
(it still fires the local UDP ping so the desktop fox reacts), so importing
and decorating is always safe even before a key/backend exist.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

# The UDP host/port are fixed by the desktop app's sdk_bridge listener.
DEFAULT_ENDPOINT = "http://127.0.0.1:8000"
DEFAULT_UDP_HOST = "127.0.0.1"
DEFAULT_UDP_PORT = 9999
DEFAULT_TIMEOUT = 5.0

# Preflight-guard modes. "observe" is the historical, fully back-compatible path
# (run first, hash after). "block"/"redact" evaluate policy BEFORE the wrapped
# function runs. Anything else resolves back to "observe".
DEFAULT_MODE = "observe"
_VALID_MODES = ("observe", "block", "redact")

# Org policy (P4 §B). ON by default: a workspace that tightens to block should
# reach every deployment without waiting for a redeploy. FOXY_ORG_POLICY=off
# stops the fetch entirely — for air-gapped installs, or teams who want code to
# be the only source of truth. Switching it off can only make enforcement
# weaker-or-equal to what the code already says, so it is not a way around local
# blocking.
DEFAULT_ORG_POLICY_TTL = 300.0        # 5 minutes ≈ 12 requests/hour per process
_FALSEY = {"0", "false", "no", "off"}


@dataclass(frozen=True)
class FoxyConfig:
    api_key: str = ""
    endpoint: str = DEFAULT_ENDPOINT
    udp_host: str = DEFAULT_UDP_HOST
    udp_port: int = DEFAULT_UDP_PORT
    desktop_ping: bool = True
    timeout: float = DEFAULT_TIMEOUT
    commitment_key: str = ""
    # Path to the customer-owned salt sidecar. Empty (the default) = commitments
    # are unsalted, byte-identical to every row already written. Setting it turns
    # per-event salting ON, because a salt with nowhere to live is a commitment
    # nobody can ever prove — the storage IS the feature switch.
    salt_sidecar_path: str = ""
    spool_path: str = ""
    client_id: str = ""
    audit_required: bool = False
    mode: str = DEFAULT_MODE
    # Whether `mode` was CHOSEN or is just the default. Load-bearing for P4 §B:
    # an org may fill in a mode where the code specified none, but must not
    # override a mode the code stated explicitly. Collapsing the two — which is
    # what `mode` alone does, since it always resolves to a concrete string —
    # would silently hand the org override rights it must not have.
    mode_is_explicit: bool = False
    org_policy_enabled: bool = True
    org_policy_ttl: float = DEFAULT_ORG_POLICY_TTL

    @classmethod
    def resolve(
        cls,
        api_key: str | None = None,
        endpoint: str | None = None,
        udp_host: str | None = None,
        udp_port: int | None = None,
        desktop_ping: bool = True,
        timeout: float = DEFAULT_TIMEOUT,
        commitment_key: str | None = None,
        salt_sidecar_path: str | None = None,
        spool_path: str | None = None,
        client_id: str | None = None,
        audit_required: bool | None = None,
        mode: str | None = None,
        org_policy: bool | None = None,
        org_policy_ttl: float | None = None,
    ) -> "FoxyConfig":
        key = api_key if api_key is not None else os.getenv("FOXY_API_KEY", "")
        ep = endpoint if endpoint is not None else os.getenv("FOXY_BACKEND_URL", DEFAULT_ENDPOINT)
        env_mode = os.getenv("FOXY_MODE")
        raw_mode = mode if mode is not None else (env_mode if env_mode is not None else DEFAULT_MODE)
        resolved_mode = str(raw_mode).strip().lower()
        # "Explicit" means a human said so — a kwarg or FOXY_MODE. An invalid
        # value still counts: they meant to choose something, and falling back to
        # observe should not hand the org override rights they did not intend.
        explicit = mode is not None or env_mode is not None
        if resolved_mode not in _VALID_MODES:
            resolved_mode = DEFAULT_MODE
        env_org = os.getenv("FOXY_ORG_POLICY")
        org_on = (org_policy if org_policy is not None
                  else (env_org.strip().lower() not in _FALSEY if env_org is not None else True))
        raw_ttl = (org_policy_ttl if org_policy_ttl is not None
                   else os.getenv("FOXY_ORG_POLICY_TTL"))
        try:
            ttl = float(raw_ttl) if raw_ttl is not None else DEFAULT_ORG_POLICY_TTL
        except (TypeError, ValueError):
            ttl = DEFAULT_ORG_POLICY_TTL
        if ttl <= 0:
            ttl = DEFAULT_ORG_POLICY_TTL
        return cls(
            api_key=key.strip(),
            endpoint=ep.rstrip("/"),
            udp_host=udp_host or DEFAULT_UDP_HOST,
            udp_port=int(udp_port or DEFAULT_UDP_PORT),
            desktop_ping=desktop_ping,
            timeout=timeout,
            commitment_key=(commitment_key if commitment_key is not None
                            else os.getenv("FOXY_COMMITMENT_KEY", key)).strip(),
            salt_sidecar_path=(salt_sidecar_path
                               or os.getenv("FOXY_SALT_SIDECAR", "")).strip(),
            spool_path=spool_path or os.getenv("FOXY_SPOOL_PATH", ""),
            # FoxyClient persists a generated identity in the local spool when
            # no explicit identity is supplied. Empty here is intentional.
            client_id=client_id or os.getenv("FOXY_CLIENT_ID", ""),
            audit_required=(audit_required if audit_required is not None
                            else os.getenv("FOXY_AUDIT_REQUIRED", "false").lower() in {"1", "true", "yes"}),
            mode=resolved_mode,
            mode_is_explicit=explicit,
            org_policy_enabled=bool(org_on),
            org_policy_ttl=ttl,
        )

    @property
    def enabled(self) -> bool:
        """True when the SDK should stream to the cloud backend.

        When False, the decorator still runs the wrapped function and still
        fires the local desktop ping — it just skips the HTTP POST.
        """
        return bool(self.api_key)
