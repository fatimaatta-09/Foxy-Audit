# foxy-audit (SDK)

> Governance-as-Code for AI. One decorator → a tamper-evident, privacy-preserving audit trail.

The SDK hashes your LLM prompt + response **locally** (SHA-256), throws the raw text away, and
streams only metadata to the Foxy Audit backend. It also fires a best-effort local UDP ping so the
desktop "fox" companion reacts in real time (green on every logged call, red on a policy breach).

## Install

```bash
pip install -e .            # from this sdk/ folder, for local development
```

Runtime dependency: `requests` only.

## Use

```python
import os
from foxy_audit import FoxyClient

foxy = FoxyClient(api_key=os.getenv("FOXY_API_KEY"))   # or just rely on the env var

@foxy.audit(policy="hipaa_basic")
def ask_model(prompt: str) -> str:
    return llm_client.generate(prompt)     # your existing code — unchanged
```

Every call to `ask_model` is now hashed, logged, and graded. Or use the module-level decorator,
which builds a client from the environment:

```python
from foxy_audit import audit

@audit(policy="soc2")
def summarize(text: str) -> str:
    ...
```

## Configuration

| Setting        | Kwarg          | Env var             | Default                  |
|----------------|----------------|---------------------|--------------------------|
| API key        | `api_key`      | `FOXY_API_KEY`      | _(none → HTTP disabled)_ |
| Backend URL    | `endpoint`     | `FOXY_BACKEND_URL`  | `http://127.0.0.1:8000`  |
| Desktop ping   | `desktop_ping` | —                   | `True` (127.0.0.1:9999)  |

With no API key the SDK is a **graceful no-op for the cloud path**: it still runs your function and
still pings the desktop fox, but skips the HTTP upload — so you can see the fox react before any
backend exists.

## Guarantees

- **Never blocks** your function — the HTTP upload runs on a background daemon thread.
- **Never raises** its own errors into your app — all telemetry failures are swallowed.
- **Never sees raw text** server-side — only SHA-256 digests + token count + policy tag leave the host.
- Works with both **sync and async** functions; the return value is always passed through unchanged.

## What gets sent

To the backend (`POST /v1/logs`, `Authorization: Bearer <key>`):

```json
{"prompt_hash": "<64 hex>", "response_hash": "<64 hex>", "token_count": 123, "policy_tag": "hipaa_basic"}
```

To the desktop fox (UDP `127.0.0.1:9999`):

```json
{"event": "hash_ok", "policy": "hipaa_basic", "tokens": 123, "ts": 1719300000}
{"event": "policy_breach", "reason": "...", "risk_score": 87, "policy": "hipaa_basic", "ts": ...}
```
