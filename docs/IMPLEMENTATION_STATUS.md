# Foxy Audit — Implementation Status vs. the Engineering Review

This document maps the findings in **`foxy-audit-plan.pdf`** ("Honest Engineering & Business
Review") to what has now been **built and verified** in this repo, and what is **still open**.

Scope of this work: the FastAPI **backend** (`backend/`), the **web dashboard** (the existing
`foxy-audit-premium.html`, now served live and wired to the API), plus terminology/CI fixes. All items
below were verified end-to-end against a live Postgres + seeded org (see *How to run* at the bottom).

> **Branch:** `foxy-skeleton` — this branch already contained the full three-tier system
> (`backend/`, `sdk/`, `demo/`, desktop app), contrary to the older CLAUDE.md note.

---

## 1. Review §3 "Honest Severity Ranking" — status

| # | Review finding | Severity | Status | What was done |
|---|---|---|---|---|
| 1 | No real web dashboard (Next.js claimed, doesn't exist) | Critical (sales) | ✅ **Done** | The existing `foxy-audit-premium.html` is now the **live web dashboard**, served by FastAPI at `/dashboard`, behind a **login**, wired to real data. (Not Next.js — see note below.) |
| 2 | No durable task queue (in-memory queue) | Critical (trust) | ✅ **Done** | Replaced the in-memory `queue.Queue` with a **durable Postgres-outbox + poller** (`grading_status` column). A crash can't drop a grading job. |
| 3 | No human auth/RBAC, only org-wide API key | Critical (enterprise) | ✅ **Done** | Added a `users` table, **email/password session login**, and **admin/member roles**. SDK's machine key path is unchanged. |
| 4 | "Merkle Tree" / "immutable" terminology overclaims | High (credibility) | ✅ **Done** | Renamed *Merkle → sequential hash chain* and *immutable → tamper-evident* in `README.md` + `foxy-audit-premium.html`. ("zero-knowledge" phrasing left as a minor optional item.) |
| 5 | No external anchoring of the chain head | High (USP) | ⬜ **Open** | Deferred (Phase 4). Requires TSA / public timestamp / signed daily export. |
| 6 | weasyprint native-dependency risk | Medium | ✅ **N/A + bug fixed** | No `weasyprint` import exists (already HTML-only). Separately **fixed a real bug**: `passport.py` looked for its Jinja template in the wrong folder, so `/v1/passport` had never worked — now it does. |
| 7 | No CI / automated tests | Medium-high | ✅ **Done** | Added `.github/workflows/ci.yml` (SDK tests + a **golden-vector hash-chain regression test** + desktop compile check). |
| 8 | Gemini judge mostly re-implements hardcoded thresholds | Medium | ⬜ **Open** | Deferred. Would feed the model sequences of per-org metadata to reason over time. |
| 9 | PII detector only catches email/SSN | Medium | ⬜ **Open** | Deferred. Either expand or relabel as a "basic pattern flagger." |
| 10 | Single org per desktop install, no multi-seat | Medium | 🟨 **Partially addressed** | Human auth (item 3) enables **multiple users per org** on the web app. The desktop single-org install is unchanged. |

---

## 2. Review §8 "What To Do Next" — status

**§8.1 "This week" (cheap credibility):**
- Rename Merkle → hash chain — ✅ done
- weasyprint docstring contradiction — ✅ N/A (no import) + fixed the real passport template bug
- Add GitHub Actions CI — ✅ done

**§8.2 "Next 2–4 weeks" (make it sellable):**
- Real web dashboard against existing FastAPI endpoints — ✅ done (premium HTML, not Next.js)
- Basic per-org login — ✅ done (session + roles)
- Replace in-memory queue with a durable one — ✅ done (**Postgres outbox**, chosen over Redis/Celery: zero new deps, same-transaction durability, fits the sync-SQLAlchemy codebase)

**§8.3 Design-partner pilot** — ⬜ business step, out of code scope.

**§8.4 "In parallel" (hardening):** external anchoring, HMAC-with-pepper API keys, a Keys page — ⬜ open (Phase 4).

---

## 3. What was built (by track)

### Track C — Durable grading queue
- `backend/migrations/versions/0003_grading_status.py` — adds `grading_status/attempts/started_at/graded_at` + partial index; backfills existing rows.
- `backend/app/worker.py` — rewritten as a **Postgres-outbox poller** (`FOR UPDATE SKIP LOCKED`, retry, `failed` dead-letter, per-row RLS scoping).
- `backend/app/worker_main.py` — poller entrypoint (`python -m app.worker_main`).
- `backend/app/routers/logs.py` (ingest), `main.py`, `config.py`, `.env.example`, `docker-compose.yml` updated accordingly.

### Track A1 — Dashboard read endpoints
- `GET /v1/logs` (paginated + `policy_tag`/`verdict` filters), `GET /v1/logs/{seq}`, `GET /v1/stats` (totals, breaches, clean rate, grading breakdown, 7-day activity) — in `backend/app/routers/logs.py` + `schemas.py`.

### Track B — Human auth + RBAC
- `backend/migrations/versions/0004_users.py` — `users` table (org-scoped, unique email, role).
- `backend/app/routers/auth_human.py` — `POST /v1/auth/login`, `POST /v1/auth/logout`, `GET /v1/auth/me`, `GET /v1/auth/users` (admin-only).
- `backend/app/auth.py` — `require_user`, `require_role`, and `resolve_org` (accepts **either** the SDK Bearer key **or** a human session, so the dashboard reads over its cookie).
- `main.py` — `SessionMiddleware` (signed cookie, not JWT — avoids colliding with the SDK's Bearer header).
- `backend/scripts/seed_org.py` — `--admin-email/--admin-password` to seed the first admin.
- Deps: `bcrypt`, `itsdangerous`.

### Track A2 — Web dashboard (the existing premium HTML, made live)
- `backend/app/main.py` — serves `foxy-audit-premium.html` at **`/dashboard`** (same-origin → the login cookie works with no CORS/BFF).
- `foxy-audit-premium.html` — added a **login gate** + a live-integration script (design untouched). Wired: **Home** (hero, stat tiles, recent ledger), **Threats** (stats + 7-day sparkline), **Ledger** (full table), **Verify** ("verify live ledger chain" → `/v1/verify`), **Export** ("generate passport" → real `/v1/passport` HTML in a new tab; live chain metadata). Log-out control added.
- `/v1/verify` and `/v1/passport` switched to `resolve_org` so the session-authed dashboard can call them (SDK Bearer path still works).

> **Note on the dashboard choice:** the review recommended a *Next.js* app. A Next.js scaffold + BFF
> was built and working, but was **removed** in favor of serving the existing polished
> `foxy-audit-premium.html` directly from the backend — same end result (a real, authenticated web
> dashboard on live data), less code, and it reuses the design you already had.

---

## 4. Still open

**Dashboard pages that need NEW backend endpoints (still show demo data):**
- **Policy** — needs an `OrgPolicy` table + `GET/PUT /v1/policies` (today `policy_tag` is just a free-text label; there is no policy config).
- **Keys** — needs a `keys` table + list/create/revoke (only `POST /v1/keys/rotate` exists; an org has a single key hash).
- **Settings** — account/role edits are partly backed by `/v1/auth/*`; notifications/danger-zone are not.

**Hardening / Phase 4 (from the review):**
- External chain-head anchoring (§2.3 / #5) — turns "trust the admin" into "trust the math."
- HMAC-with-pepper API keys (T1-4) — currently plain SHA-256 of the key.
- Gemini "AI-native" upgrade (#8) — reason over per-org metadata sequences, not just thresholds.
- PII detector beyond email/SSN (#9).
- SSO/SAML (enterprise); SOC 2 / BAA (org + legal, not code).

**Housekeeping done in this commit:** removed the abandoned Next.js `frontend/`, stopped tracking
`__pycache__/*.pyc`, and extended `.gitignore` (node_modules, `.next`, `backend/.env`).

---

## 5. How to run (verify locally)

```powershell
# 1) Postgres
cd backend ; docker compose up -d          # starts the `db` service (Postgres 16)

# 2) Backend (venv)
python -m venv .venv ; .\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
alembic upgrade head
python scripts/seed_org.py --name "Demo Corp" --admin-email admin@demo.test --admin-password adminpass123
uvicorn app.main:app --port 8000 --reload

# 3) Grading poller (second terminal, venv active)
python -m app.worker_main

# 4) (optional) ingest sample data
cd .. ; pip install -e ./sdk
$env:FOXY_API_KEY='<key printed by seed_org>' ; python .\demo\run_demo.py
```

Then open **http://localhost:8000/dashboard** and sign in
(`admin@demo.test` / `adminpass123`).

**Tests:** `pytest sdk/tests backend/tests` (the chain golden-vector test is the core trust check).
