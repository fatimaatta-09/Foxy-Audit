# foxy-audit (SDK)

> Governance-as-Code for AI. One decorator → a tamper-evident, privacy-preserving audit trail.

The SDK hashes your LLM prompt + response **locally** (SHA-256), throws the raw text away, and
streams only metadata to the Foxy Audit backend. It also fires best-effort local UDP signals so the
desktop "fox" companion can show local capture in real time. A green capture reaction means
"hashed locally and queued"; it is not a backend receipt or a clean policy verdict.

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

### Attributing the model (`agent`)

Pass `agent=` to record *which* model produced the interaction. The backend folds it into the
tamper-evident hash chain, so the attribution can't be altered after the fact:

```python
@foxy.audit(policy="soc2", agent="gpt-4o")
def ask_model(prompt: str) -> str:
    ...
```

`agent` is optional — rows logged without it hash exactly as before, so existing chains keep
verifying.

## Configuration

| Setting        | Kwarg          | Env var             | Default                  |
|----------------|----------------|---------------------|--------------------------|
| API key        | `api_key`      | `FOXY_API_KEY`      | _(none → HTTP disabled)_ |
| Backend URL    | `endpoint`     | `FOXY_BACKEND_URL`  | `http://127.0.0.1:8000`  |
| Desktop ping   | `desktop_ping` | —                   | `True` (127.0.0.1:9999)  |

With no API key the SDK is a **graceful no-op for the cloud path**: it still runs your function and
still pings the desktop fox with a `local_only` capture signal, but skips the HTTP upload. This is
useful for checking the local SDK-to-pet path, but no server record is created.

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
{"event": "evaluating", "policy": "hipaa_basic", "tokens": 123, "ts": 1719300000}
{"event": "hash_ok", "policy": "hipaa_basic", "tokens": 123, "ts": 1719300000}
{"event": "policy_breach", "reason": "...", "risk_score": 87, "policy": "hipaa_basic", "ts": ...}
```

`hash_ok` includes `delivery: "queued"` when an API key is configured and
`delivery: "local_only"` otherwise. Neither value means that the backend has
accepted or graded the record.
