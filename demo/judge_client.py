"""Real no-LLM judge client for the Foxy Audit evaluation offer.

This script exercises the production SDK with local deterministic functions and
NO real LLM. It produces the required judge deliverable: one ALLOWED interaction
plus one PHI interaction that the host-side preflight guard BLOCKS before any
model call (mode="block"), recording a content-blind 'blocked' audit event. It
does not seed evidence or fabricate a policy verdict. With an API key the
metadata is sent to the configured backend and appears in the normal ledger;
without one it only proves the local SDK-to-pet path. It runs either way.

Run from the repository root after installing the SDK:

    pip install -e ./sdk
    $env:FOXY_API_KEY = "foxy_sk_..."
    $env:FOXY_BACKEND_URL = "https://<deployed-api-origin>"
    python demo/judge_client.py
"""

from __future__ import annotations

import os

from foxy_audit import FoxyClient, FoxyPolicyBlocked


foxy = FoxyClient()


@foxy.audit(policy="judge_smoke", agent="local-test-client")
def local_model(prompt: str) -> str:
    """A local stand-in so judges do not need an LLM account."""
    return "Local test client produced a bounded response."


@foxy.audit(policy="hipaa", mode="block", agent="local-test-client")
def phi_model(prompt: str) -> str:
    """A PHI prompt hits this under mode='block'. The body MUST NEVER run:
    the preflight guard blocks it and raises before any model/LLM call."""
    raise AssertionError("LLM was called despite the preflight block")


def main() -> None:
    has_key = bool(os.getenv("FOXY_API_KEY", "").strip())
    endpoint = os.getenv("FOXY_BACKEND_URL", "http://127.0.0.1:8000").rstrip("/")

    # (1) One ALLOWED, non-sensitive interaction — the LLM stand-in runs normally.
    local_model("Judge test: record this safe, non-sensitive interaction.")
    print("[1/2] ALLOWED: one real SDK call recorded (observe path).")

    # (2) One BLOCKED PHI interaction — blocked locally BEFORE the LLM is called.
    phi_prompt = ("Patient John Doe, SSN 123-45-6789, DOB 1980-01-01, needs a "
                  "prescription refill and his email is john.doe@example.com.")
    try:
        phi_model(phi_prompt)
        print("[2/2] UNEXPECTED: PHI prompt was not blocked.")
    except FoxyPolicyBlocked as exc:
        print("[2/2] BLOCKED before any LLM call:")
        print(f"      {exc}")
        print("      The wrapped model function was NEVER invoked; a content-blind")
        print("      'blocked' audit event (hashes + signal labels only) was recorded.")

    print()
    print("Foxy Audit judge client: one allowed + one blocked-before-LLM event, no real LLM.")
    print(f"Backend upload: {'enabled' if has_key else 'disabled (local-only)'}")
    print(f"Endpoint: {endpoint}")
    print("The dashboard will show the records after the asynchronous upload refreshes.")


if __name__ == "__main__":
    main()
