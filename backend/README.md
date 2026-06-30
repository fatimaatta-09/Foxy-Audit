# Foxy Audit — Backend

FastAPI ingestion service: validates interaction metadata, writes it into a
tamper-evident **sequential hash chain** in PostgreSQL (with Row-Level Security
for tenant isolation), and grades each interaction with **Gemini 1.5 Pro**.

## Endpoints

| Method | Path                   | Purpose                                              |
|--------|------------------------|------------------------------------------------------|
| GET    | `/v1/health`           | Liveness + key check (the desktop app probes this)   |
| POST   | `/v1/logs`             | Ingest metadata → chain → 202 → background Gemini   |
| GET    | `/v1/verify`           | Recompute the chain, detect tampering                |
| POST   | `/v1/passport`         | Generate a compliance passport (HTML report)         |
| POST   | `/v1/keys/rotate`      | Rotate the org's API key (invalidates the old one)   |
| POST   | `/v1/webhooks/stripe`  | Stripe subscription webhook (auto-provisions orgs)   |

All endpoints except `/v1/webhooks/stripe` require `Authorization: Bearer <org_api_key>`.

## Run locally

```bash
cd backend
cp .env.example .env                 # then set GEMINI_API_KEY
docker compose up -d                 # Postgres 16 on :5432
python -m venv .venv && . .venv/Scripts/activate   # (Windows: .venv\Scripts\activate)
pip install -r requirements.txt
alembic upgrade head                 # create tables + RLS policy + billing columns
python scripts/seed_org.py --name "Demo Corp"      # prints FOXY_API_KEY=foxy_sk_...
uvicorn app.main:app --port 8000 --reload
```

Smoke test:

```bash
curl -i -H "Authorization: Bearer foxy_sk_..." http://127.0.0.1:8000/v1/health   # 200
curl    -H "Authorization: Bearer foxy_sk_..." http://127.0.0.1:8000/v1/verify   # {"ok":true,...}
```

## Tamper-evidence demo

```bash
# break a historical row, then re-verify
psql "postgresql://foxy:foxy@localhost:5432/foxy" \
  -c "UPDATE audit_logs SET token_count = token_count + 1 WHERE seq = 1;"
curl -H "Authorization: Bearer foxy_sk_..." http://127.0.0.1:8000/v1/verify
#   → {"ok":false,"first_broken_seq":1,...}
python scripts/verify_chain.py        # per-row PASS/FAIL table
```

## Design notes

- **Sequential hash chain, not blockchain/Merkle:** `chain.py` is the single
  source of truth for `Hn = SHA256(data_blob || H_{n-1})`, imported by both the
  ingest route and the verifier so writer and reader can never diverge.
- **Async Gemini:** `/v1/logs` returns `202 Accepted` immediately after writing
  the chain row. A background `GeminiWorker` thread (`worker.py`) grades the
  metadata and patches the verdict back into the row asynchronously.
- **Fail-open judge:** if Gemini is unreachable the chain row is still written
  (the chain is the legal artifact); flip `GEMINI_FAIL_CLOSED=true` to flag instead.
- **RLS:** `auth.require_org` sets `app.current_org` via `set_config(..., true)`
  per transaction; the `org_isolation` policy (with `FORCE`) scopes every
  `audit_logs` query to the calling tenant.
- **Key rotation:** `POST /v1/keys/rotate` generates a new key, overwrites the
  hash — the old key is immediately invalid. Copy the new key on the spot.
- **Stripe billing:** `POST /v1/webhooks/stripe` auto-provisions orgs on
  checkout completion and tracks subscription status changes.
- **Compliance passport:** `POST /v1/passport` renders a self-verifiable HTML
  report with chain root hash, stats, and policy breakdown.

