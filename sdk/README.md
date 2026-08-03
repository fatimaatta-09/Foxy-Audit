# foxy-audit (SDK)

> Governance-as-Code for AI. One decorator -> a tamper-evident, content-blind audit trail.

The SDK creates customer-keyed HMAC commitments for supported LLM inputs and outputs locally,
throws raw text away before upload, and durably spools only metadata to the Foxy Audit backend. It also fires a best-effort local UDP ping so the
desktop "fox" companion shows local capture activity and backend grading alerts.

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
| Commitment key | `commitment_key` | `FOXY_COMMITMENT_KEY` | API key when omitted |
| Salt sidecar | `salt_sidecar_path` | `FOXY_SALT_SIDECAR` | _(none → commitments unsalted)_ |
| Durable spool | `spool_path` | `FOXY_SPOOL_PATH` | `~/.foxy-audit/spool.sqlite3` |
| Stable client id | `client_id` | `FOXY_CLIENT_ID` | persisted in the local spool when omitted |
| Required capture | `audit_required` | `FOXY_AUDIT_REQUIRED` | `False` |

### Salted commitments (optional)

Set `salt_sidecar_path` and each event gets a fresh 128-bit salt, mixed into the HMAC
canonically — `HMAC(key, {"s": salt, "v": <canonical text>})`, never by concatenation.
The salt is appended to that local JSONL file and **never leaves your process**: not the
wire, not our database, not a response, not a log line. Those rows report
`commitment_alg: "hmac-sha256-salted"`; leave the setting unset and commitments are
byte-identical to what the SDK has always produced.

The trade: lose that sidecar and you lose the ability to prove *which* text those
commitments cover (`foxy_verify.py --commitment-key --events` reports them as not
checked). You keep chain verification either way — tamper-evidence is recomputed from
stored fields and never needs the salt.

With no API key the SDK is a **graceful no-op for the cloud path**: it still runs your function and
still pings the desktop fox, but skips the HTTP upload. In the default mode, delivery is best-effort;
for regulated workflows, set `audit_required=True` so the decorator waits for a server receipt and
raises when durable delivery cannot be confirmed.

## Guarantees

- **Default path is asynchronous** — the HTTP upload runs on a background daemon thread after a local durable enqueue.
- **Retries do not discard events** — failed uploads remain in the SQLite/WAL spool.
- **Content-blind by design** — commitments, token counts, policy tags, and bounded identifiers leave the host; raw text is not sent by the SDK.
- Works with **sync, async, and generator** functions; host return values are passed through unchanged.

## What gets sent

To the backend (`POST /v1/logs`, `Authorization: Bearer <key>`):

```json
{"event_id": "<uuid>", "client_id": "...", "client_seq": 1, "commitment_alg": "hmac-sha256", "prompt_hash": "<64 hex>", "response_hash": "<64 hex>", "token_count": 123, "policy_tag": "hipaa_basic"}
```

To the desktop fox (UDP `127.0.0.1:9999`):

```json
{"event": "hash_ok", "policy": "hipaa_basic", "tokens": 123, "ts": 1719300000}
{"event": "policy_breach", "reason": "...", "risk_score": 87, "policy": "hipaa_basic", "ts": ...}
```
