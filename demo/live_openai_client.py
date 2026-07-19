"""Run a real OpenAI Responses API call through the Foxy SDK.

This is a client-style integration demo, not a simulated provider. OpenAI sees
the prompt because it is the chosen model provider; Foxy receives only the SDK's
local commitments and bounded operational metadata. ``audit_required=True``
makes the demo fail rather than claiming success when Foxy cannot issue a
durable server receipt.

Set OPENAI_API_KEY, FOXY_API_KEY, and FOXY_BACKEND_URL before running:

    python demo/live_openai_client.py
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from typing import Any
from urllib import error as urllib_error
from urllib import request as urllib_request

OPENAI_RESPONSES_URL = "https://api.openai.com/v1/responses"


def _required_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise SystemExit(f"Missing {name}. Set it in your shell and try again.")
    return value


def _response_text(payload: dict[str, Any]) -> str:
    """Read a Responses API text result without depending on an SDK version."""
    direct = payload.get("output_text")
    if isinstance(direct, str) and direct.strip():
        return direct
    for item in payload.get("output", []):
        if not isinstance(item, dict):
            continue
        for content in item.get("content", []):
            if (isinstance(content, dict)
                    and content.get("type") == "output_text"
                    and isinstance(content.get("text"), str)):
                return content["text"]
    raise RuntimeError("OpenAI returned no text output")


def call_openai(prompt: str, model: str, api_key: str) -> str:
    """Make the customer's real model request using the Responses API."""
    body = {
        "model": model,
        "input": [{"role": "user", "content": [
            {"type": "input_text", "text": prompt},
        ]}],
        "max_output_tokens": 220,
    }
    request = urllib_request.Request(
        OPENAI_RESPONSES_URL,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib_request.urlopen(request, timeout=30) as response:
            return _response_text(json.loads(response.read().decode("utf-8")))
    except urllib_error.HTTPError as exc:
        raise RuntimeError(f"OpenAI request failed with HTTP {exc.code}") from exc
    except (urllib_error.URLError, TimeoutError, OSError, ValueError) as exc:
        raise RuntimeError(f"OpenAI request failed: {type(exc).__name__}") from exc


def verify_foxy_chain(endpoint: str, api_key: str) -> dict[str, Any]:
    """Verify the server-owned chain after the SDK has a durable receipt."""
    request = urllib_request.Request(
        endpoint.rstrip("/") + "/v1/verify",
        headers={"Authorization": f"Bearer {api_key}"},
        method="GET",
    )
    try:
        with urllib_request.urlopen(request, timeout=15) as response:
            return json.loads(response.read().decode("utf-8"))
    except (urllib_error.URLError, TimeoutError, OSError, ValueError) as exc:
        raise RuntimeError(f"Foxy verification request failed: {type(exc).__name__}") from exc


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Send a real GPT-5.6 call through the Foxy SDK and verify its receipt."
    )
    parser.add_argument(
        "--prompt",
        default="Give a concise explanation of why tamper-evident AI evidence matters.",
        help="Use non-sensitive demo content; this is sent to your chosen OpenAI model.",
    )
    parser.add_argument(
        "--model", default=os.getenv("OPENAI_MODEL", "gpt-5.6"),
        help="OpenAI Responses API model to call (default: OPENAI_MODEL or gpt-5.6).",
    )
    args = parser.parse_args()

    openai_api_key = _required_env("OPENAI_API_KEY")
    foxy_api_key = _required_env("FOXY_API_KEY")
    foxy_endpoint = _required_env("FOXY_BACKEND_URL")
    # Keep module import testable without SDK dependencies; the real command
    # still requires Foxy before it can make a provider call.
    from foxy_audit import FoxyClient

    foxy = FoxyClient(
        api_key=foxy_api_key,
        endpoint=foxy_endpoint,
        audit_required=True,
        desktop_ping=False,
    )

    @foxy.audit(policy="hackathon_demo", agent=args.model)
    def ask_customer_model(prompt: str, **_metadata: str) -> str:
        return call_openai(prompt, args.model, openai_api_key)

    print("[1/3] Calling the real OpenAI Responses API...")
    response = ask_customer_model(
        args.prompt,
        provider="openai",
        model=args.model,
        request_id=str(uuid.uuid4()),
    )
    print("[PASS] OpenAI response received:")
    print(response[:600])

    print("\n[2/3] Foxy received a durable content-blind audit receipt.")
    print("[PASS] Raw prompt/response stayed in the client process; Foxy received commitments.")

    print("\n[3/3] Verifying the server-owned hash chain...")
    verification = verify_foxy_chain(foxy_endpoint, foxy_api_key)
    if not verification.get("ok"):
        raise SystemExit(
            "Foxy verification reported a broken chain: "
            + json.dumps(verification, sort_keys=True)
        )
    print("[PASS] Foxy chain verified:", json.dumps(verification, sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
