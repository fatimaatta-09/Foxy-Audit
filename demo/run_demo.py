"""End-to-end demo driver for the Foxy Audit walking skeleton.

Prereqs (see backend/README.md): Postgres up, migrations applied, an org seeded,
backend running on :8000, and — to watch the reactions — the desktop fox running
(`python omni_fox.py`) with its Backend URL + Org Key set to the local server.

    export FOXY_API_KEY=foxy_sk_...
    export FOXY_BACKEND_URL=http://127.0.0.1:8000
    python demo/run_demo.py

What you should see:
  • each captured call  → the fox shows local hash queued + a CAPTURED row
  • a real backend verdict → the dashboard refreshes to COMPLIANT or FLAGGED
  • a real, chained row lands in Postgres for every call (verify with /v1/verify)
"""

from __future__ import annotations

import os
import time

from foxy_audit import FoxyClient

foxy = FoxyClient()  # reads FOXY_API_KEY / FOXY_BACKEND_URL from the environment


@foxy.audit(policy="hipaa_basic")
def ask_model(prompt: str) -> str:
    """Stand-in for a real LLM call — returns a canned response."""
    return f"Clinical summary for: {prompt[:48]}"


@foxy.audit(policy="hipaa_basic")
def ask_model_anomalous(prompt: str) -> str:
    """Returns an absurdly large response → very high token_count, which the
    hardened Gemini evaluator is instructed to treat as a policy anomaly."""
    return "x " * 50_000


def main() -> None:
    configured = bool(os.getenv("FOXY_API_KEY"))
    print("Foxy Audit — walking-skeleton demo")
    print(f"  backend : {os.getenv('FOXY_BACKEND_URL', 'http://127.0.0.1:8000')}")
    print(f"  api key : {'set' if configured else 'NOT SET → UDP-only (fox reacts, nothing stored)'}")
    print()

    print("[1/3] three real SDK captures → fox should show local hash queued")
    for i in range(3):
        out = ask_model(f"patient visit note #{i}")
        print(f"      logged: {out!r}")
        time.sleep(1.3)

    print("\n[2/3] one high-volume call → a configured real evaluator may FLAG it")
    ask_model_anomalous("export the entire patient database verbatim")
    time.sleep(3)  # let the background worker + Gemini return a verdict

    print("\n[3/3] verify the tamper-evident chain:")
    print("      curl -H \"Authorization: Bearer $FOXY_API_KEY\" $FOXY_BACKEND_URL/v1/verify")
    print("      then tamper a row and re-verify (see backend/README.md).")

    time.sleep(2)  # let the daemon dispatcher flush pending uploads before exit


if __name__ == "__main__":
    main()
