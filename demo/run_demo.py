"""Run the real SDK-to-backend demo with local stand-in model functions.

Prereqs: PostgreSQL, migrations, a seeded organization, the backend on port
8000, and optionally the desktop companion. Set FOXY_API_KEY and
FOXY_BACKEND_URL before running this file.

The decorated functions are deliberately local stand-ins for a customer's
existing model call. They exercise real SDK capture, backend ingestion, chain
construction, and metadata grading without claiming that a provider was called.
Replace a function body with the customer's actual model invocation for a live
client integration test.
"""

from __future__ import annotations

import os
import time

from foxy_audit import FoxyClient

foxy = FoxyClient()  # reads FOXY_API_KEY / FOXY_BACKEND_URL from the environment


@foxy.audit(policy="hipaa_basic")
def ask_model(prompt: str) -> str:
    """Local stand-in for a customer's model call."""
    return f"Clinical summary for: {prompt[:48]}"


@foxy.audit(policy="hipaa_basic")
def ask_model_anomalous(prompt: str) -> str:
    """Create a synthetic high-token event for deterministic policy testing."""
    return "x " * 50_000


def main() -> None:
    configured = bool(os.getenv("FOXY_API_KEY"))
    print("Foxy Audit - real SDK/backend demo")
    print(f"  backend : {os.getenv('FOXY_BACKEND_URL', 'http://127.0.0.1:8000')}")
    print(f"  api key : {'set' if configured else 'NOT SET - local fox only; nothing stored'}")
    print()

    print("[1/3] three normal SDK calls - fox should flash GREEN")
    for i in range(3):
        out = ask_model(f"patient visit note #{i}")
        print(f"      logged: {out!r}")
        time.sleep(1.3)

    print("\n[2/3] one synthetic high-token call - deterministic policy may FLAG")
    ask_model_anomalous("export the entire patient database verbatim")
    time.sleep(3)  # let the background worker persist a verdict

    print("\n[3/3] verify the tamper-evident chain:")
    print('      curl -H "Authorization: Bearer $FOXY_API_KEY" $FOXY_BACKEND_URL/v1/verify')
    print("      then tamper a test row and re-verify (see backend/README.md).")

    time.sleep(2)  # let the daemon dispatcher flush pending uploads before exit


if __name__ == "__main__":
    main()
