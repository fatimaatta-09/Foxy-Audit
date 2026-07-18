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


@dataclass(frozen=True)
class FoxyConfig:
    api_key: str = ""
    endpoint: str = DEFAULT_ENDPOINT
    udp_host: str = DEFAULT_UDP_HOST
    udp_port: int = DEFAULT_UDP_PORT
    desktop_ping: bool = True
    timeout: float = DEFAULT_TIMEOUT
    commitment_key: str = ""
    spool_path: str = ""
    client_id: str = ""
    audit_required: bool = False

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
        spool_path: str | None = None,
        client_id: str | None = None,
        audit_required: bool | None = None,
    ) -> "FoxyConfig":
        key = api_key if api_key is not None else os.getenv("FOXY_API_KEY", "")
        ep = endpoint if endpoint is not None else os.getenv("FOXY_BACKEND_URL", DEFAULT_ENDPOINT)
        return cls(
            api_key=key.strip(),
            endpoint=ep.rstrip("/"),
            udp_host=udp_host or DEFAULT_UDP_HOST,
            udp_port=int(udp_port or DEFAULT_UDP_PORT),
            desktop_ping=desktop_ping,
            timeout=timeout,
            commitment_key=(commitment_key if commitment_key is not None
                            else os.getenv("FOXY_COMMITMENT_KEY", key)).strip(),
            spool_path=spool_path or os.getenv("FOXY_SPOOL_PATH", ""),
            client_id=client_id or os.getenv("FOXY_CLIENT_ID", "") or __import__("uuid").uuid4().hex,
            audit_required=(audit_required if audit_required is not None
                            else os.getenv("FOXY_AUDIT_REQUIRED", "false").lower() in {"1", "true", "yes"}),
        )

    @property
    def enabled(self) -> bool:
        """True when the SDK should stream to the cloud backend.

        When False, the decorator still runs the wrapped function and still
        fires the local desktop ping — it just skips the HTTP POST.
        """
        return bool(self.api_key)
