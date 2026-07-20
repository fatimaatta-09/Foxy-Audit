"""Real no-LLM judge client for the Foxy Audit evaluation offer.

This script exercises the production SDK with a local deterministic function.
It does not seed evidence or fabricate a policy verdict. With an API key, the
metadata is sent to the configured backend and appears in the normal ledger;
without one, it only proves the local SDK-to-pet path.

Run from the repository root after installing the SDK:

    pip install -e ./sdk
    $env:FOXY_API_KEY = "foxy_sk_..."
    $env:FOXY_BACKEND_URL = "https://<deployed-api-origin>"
    python demo/judge_client.py
"""

from __future__ import annotations

import os

from foxy_audit import FoxyClient


foxy = FoxyClient()


@foxy.audit(policy="judge_smoke", agent="local-test-client")
def local_model(prompt: str) -> str:
    """A local stand-in so judges do not need an LLM account."""
    return "Local test client produced a bounded response."


def main() -> None:
    has_key = bool(os.getenv("FOXY_API_KEY", "").strip())
    endpoint = os.getenv("FOXY_BACKEND_URL", "http://127.0.0.1:8000").rstrip("/")
    local_model("Judge test: record this safe, non-sensitive interaction.")
    print("Foxy Audit judge client completed one real SDK call.")
    print(f"Backend upload: {'enabled' if has_key else 'disabled (local-only)'}")
    print(f"Endpoint: {endpoint}")
    print("The dashboard will show the record after the asynchronous upload refreshes.")


if __name__ == "__main__":
    main()
