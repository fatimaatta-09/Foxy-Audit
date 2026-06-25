# Foxy Audit — Backend

FastAPI ingestion service: validates interaction metadata, writes it into a
tamper-evident **sequential hash chain** in PostgreSQL (with Row-Level Security
for tenant isolation), and grades each interaction with **Gemini 1.5 Pro**.

## Endpoints

| Method | Path          | Purpose                                            |
|--------|---------------|----------------------------------------------------|
| GET    | `/v1/health`  | Liveness + key check (the desktop app probes this) |
| POST   | `/v1/logs`    | Ingest metadata → chain → Gemini verdict → store   |
| GET    | `/v1/verify`  | Recompute the chain, detect tampering              |

All three require `Authorization: Bearer <org_api_key>`.

## Run locally

```bash
cd backend
cp .env.example .env                 # then set GEMINI_API_KEY
docker compose up -d                 # Postgres 16 on :5432
python -m venv .venv && . .venv/Scripts/activate   # (Windows: .venv\Scripts\activate)
pip install -r requirements.txt
alembic upgrade head                 # create tables + RLS policy
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
- **Inline Gemini:** `/v1/logs` grades synchronously and returns the verdict, so
  the SDK can drive the desktop fox's red alert. Redis/Celery + 202 is a later
  optimization (the `gemini.evaluate()` call is the seam to move behind a queue).
- **Fail-open judge:** if Gemini is unreachable the chain row is still written
  (the chain is the legal artifact); flip `GEMINI_FAIL_CLOSED=true` to flag instead.
- **RLS:** `auth.require_org` sets `app.current_org` via `set_config(..., true)`
  per transaction; the `org_isolation` policy (with `FORCE`) scopes every
  `audit_logs` query to the calling tenant.
