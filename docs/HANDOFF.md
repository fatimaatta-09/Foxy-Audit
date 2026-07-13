# Foxy Audit — Session Handoff (start here)

> **New chat / new account: read this first**, then `CODEBASE_DEEP_DIVE.md` (deep technical map)
> and the honest status in `Foxy-Audit-Status-Dossier.html`. **When a doc and the code disagree,
> trust the code** — some older docs (`CLAUDE.md`, top-level `README.md`) are stale.
> This supersedes `PHASE7_HANDOFF.md` (kept for history).

Last updated: 2026-07-07.

## TL;DR of where we are

- **Branch `phase3-pilot-ready` == `main`** (everything below is pushed + deployed). Tip commit
  `b5a886f`. Deployed live to VM **34.18.4.58** → **foxyaudit.tech**, CI + CD green.
- **Tests:** backend **198 passed / 2 skipped**; SDK + verifier **21 passed**. Single migration head **0029**.
- **3 live sites:** `foxyaudit.tech` (sales), `app.foxyaudit.tech/dashboard` (customer), `admin.foxyaudit.tech/admin/` (staff).
- Recent commits (newest first): `b5a886f` review-cleanup · `5f2e81e` Google SSO · `5818113` anchor env fix ·
  `48842d7` gitleaks fix · `7d671a2` new sales page · `c67dfa1` Phase 7.

## What's REAL + production-live (do NOT rebuild — verified in code this session)

Hash chain + `/v1/verify` tamper detection · durable Postgres-outbox grading worker (`FOR UPDATE SKIP
LOCKED`, retry→dead-letter, heartbeat) · **RLS actually enforced** (confined `foxy_app` NOBYPASSRLS
role via `SET LOCAL ROLE`, mig 0021; proven by `backend/tests/integration/test_rls.py`) · human auth +
RBAC + email-OTP MFA (Brevo, live) · **customer login rate-limited** · HMAC-peppered multi-key API keys
(create/rotate/revoke) · **paid Stripe checkout provisions Org+ApiKey+admin User and emails credentials**
(`billing.py` `_handle_checkout`/`_deliver_credentials`) · Gemini judge (policy + 7-day temporal;
default **gemini-2.5-flash**) · broad PII (email/SSN/phone/card-Luhn/IP + optional Presidio) ·
open-source verifier (`verifier/foxy_verify.py`) · **desktop fox breach reaction is real** (polls
`/v1/logs/breaches` → red alert) · analytics aggregates in SQL + is session-or-Bearer callable · admin
ops console (site 3) with append-only `admin_actions`.

### Shipped this session (all pushed + deployed)
1. **Phase 7 "no-fakes"** (`c67dfa1`): SDK dropped vestigial `chain_hash`/`timestamp`; sales contact
   form persists message (mig 0028); anchoring safety rails (wallet-balance floor + failure/stale
   email alerts) in `backend/app/anchor.py` + `deploy/ANCHORING_RUNBOOK.md`; `/v1/stats` exposes real
   `judge_model` + `avg_seconds_to_verdict`; dashboard truthful labels; desktop "Open Web Dashboard"
   tray link; desktop console tiles wired to real `/v1/stats` + `/v1/verify` (removed self-heal fakes).
2. **New sales page** (`7d671a2`): the fox-reveal redesign (`foxy-sale-page/index.html` + `fox-reveal.html`),
   backend-wired (`/v1/track` beacon + lead-capture modal → `/v1/leads`, **no live checkout yet**),
   plan copy reconciled to real tiers (Starter free / Companion $4.99 / Guardian $39), Gemini 2.5, ZKP claim dropped.
3. **Anchoring flipped LIVE on Sepolia** (`5818113` wired the env): prod `deploy/.env` has
   `ANCHOR_ENABLED=true`, `ANCHOR_PROVIDER=evm`. Contract `0x4D4F3359cA43874f9e7e9E158f6E7209dD984F4E`.
   Funded wallet `0x835CFaf3Fc3656918762C5d84C8FED82A8144570` (~0.1 ETH). Worker logs
   "Public-chain anchoring ON". **Not yet proven end-to-end** — no fresh prod anchor has fired (wallet
   nonce still 2 = the dev-verification txs); needs a real log→anchor cycle to produce a `confirmed`
   receipt. Rails env vars are wired in compose but OFF by default (`ANCHOR_WALLET_MIN_BALANCE_WEI`/
   `_STALE_ALERT_SECONDS`/`_ALERT_COOLDOWN` + `ALERT_EMAIL`).
4. **Google SSO "Continue with Google"** (`5f2e81e`): `POST /v1/auth/google` (verify ID token via
   `google-auth`, login-or-provision, no OTP) + `GET /v1/auth/google/config`; `users.google_sub`
   (mig 0029); buttons on the dashboard login + sales lead modal. **Live** — client id
   `234729982344-rol6q5kfd1oq4077dov08oradapk9pd4.apps.googleusercontent.com` is set on the VM and the
   config endpoint confirms it. Still TODO: add authorized JS origins `https://app.foxyaudit.tech` +
   `https://foxyaudit.tech` in the Google console, and a real browser test (only static-checked here).
5. **Teammate-review cleanup** (`b5a886f`): removed SDK dead `transport.py`; admin IP allow-list logs a
   prod warning when empty (stays fail-open — staff login+MFA always required); dropped a hardcoded
   dev path + Makefile test-key literal; model-name/docstring drift fixes.

## What's genuinely OPEN / next (ranked)

1. **Secret rotation — OVERDUE + important.** In a prior chat the operator pasted the full prod
   `deploy/.env` (Stripe, Gemini, Brevo, Postgres password, both session secrets, API-key pepper, and
   the funded Sepolia key). Treat all as compromised → regenerate each in its console (Stripe/Google/
   Brevo/etc.), update the VM `deploy/.env`, recreate services. None of these are in git.
2. **Prove anchoring end-to-end** — generate a couple of prod logs, force an anchor (admin "anchor now"
   or temporarily `ANCHOR_INTERVAL_SECONDS=60`), confirm `/v1/anchors` shows `status:"confirmed"`,
   `chain:"sepolia"`, a real `tx_hash`; the wallet nonce should tick to 3. Then optionally turn the
   safety rails on.
3. **Finish Google SSO** — set the authorized JS origins in Google Cloud; browser-test both buttons.
4. **Top-level `README.md` still lies** about the stack — lists **Redis/BullMQ** and **Next.js/Tailwind**
   which aren't used (it's a Postgres outbox + PyQt/static HTML). Not yet fixed (only the model name was).
5. **Sales page live checkout** — currently lead-capture only; wire `/v1/signup` (free) +
   `/v1/billing/checkout-session` (companion/guardian) into the modal when desired.
6. Deferred / optional: desktop runtime verification of the console + tray link (needs PyQt6 + a
   display); the web dashboard's hardcoded "Active alerts" list + 99.3% gauge; sprite-disappear bug
   (B#6); enterprise SSO/SAML/SOC2 (7D/7E).

## Secrets & credentials (NOT in git)

- Real values live only in the **gitignored** `deploy/.env` (VM) and `backend/.env` (local) — a fresh
  clone will NOT have them. Get them from the operator; rotate per item 1 above.
- Prod logins (staff superadmin, first org admin) + the SDK/org API key are held by the operator (they
  were in the prior account's private notes). Get an org API key for `curl` from the dashboard: log in
  as admin at `app.foxyaudit.tech/dashboard` → **API keys → + new key** (shown once).
- The Google **client id** above is public (safe to keep in docs); the client *secret* is not needed.

## Setup from a fresh `git clone`

```bash
# Backend deps (into a venv the tooling expects at backend/.venv)
python -m venv backend/.venv
backend/.venv/Scripts/python.exe -m pip install -r backend/requirements.txt
backend/.venv/Scripts/python.exe -m pip install -e ./sdk        # SDK editable, for its + backend tests
# Desktop app (optional; needs a display): pip install PyQt6 psutil pynput requests

# Test database: a local Postgres named foxy_pytest on :5432 (user foxy / pw foxy, a SUPERUSER).
#   Docker Desktop must be running. If the container ("foxy_pg") already exists: docker start foxy_pg
#   Fresh: run postgres:16 on 5432 with POSTGRES_USER=foxy POSTGRES_PASSWORD=foxy, then create DB foxy_pytest.
#   (Details in memory 'backend-local-run-and-tests' / CODEBASE_DEEP_DIVE.md.)

# Run the suites (the conftest runs `alembic upgrade head` once per session):
DATABASE_URL='postgresql+psycopg://foxy:foxy@localhost:5432/foxy_pytest' API_KEY_PEPPER=testpepper \
  backend/.venv/Scripts/python.exe -m pytest backend/tests/integration -q     # expect 198 passed
backend/.venv/Scripts/python.exe -m pytest sdk/tests verifier/test_verify.py -q   # expect 21 passed
```

## Deploy

`git push origin HEAD:main` → GitHub Actions **CD** SSHes to the VM and runs
`docker compose -f deploy/docker-compose.prod.yml --env-file deploy/.env up --build -d`. **CI** runs
the full backend + SDK + gitleaks/trivy on every push. To reload after a VM `.env` change (env is not
re-read by `restart`): `docker compose -f deploy/docker-compose.prod.yml up -d --force-recreate <svc>`
(services: `foxy-backend`, `foxy-worker`).

## Gotchas (learned this session)

- **One pytest run at a time** against `foxy_pytest` — the suite TRUNCATEs between tests, so a second
  concurrent run (or a stray single-test run) clobbers it and produces bogus failures.
- **Docker must be up** for the backend suite. If every test errors with `ConnectionTimeout` to :5432,
  Docker Desktop/the `foxy_pg` container is down — start it, don't debug the code.
- Big HTML files (`foxy-sale-page/index.html`) embed a ~177 KB base64 image → the Read tool chokes;
  edit them with a small Python string-replace script instead (examples were used this session).
- `gitleaks` scans pushed commits; keep example secrets as obvious placeholders and add
  `# gitleaks:allow` to any that trip the `generic-api-key` heuristic (see `deploy/ANCHORING_RUNBOOK.md`).
- Parallel Claude sessions have touched this repo — `git status` before diagnosing a surprise diff.
- CRLF/LF warnings on commit are normal on Windows; harmless.

## Key files / pointers

- Deep map: `CODEBASE_DEEP_DIVE.md`. Honest status: `Foxy-Audit-Status-Dossier.html`. Teammate review
  (mostly stale — verified): `Foxy_Audit_Status_Latest.md`. History: `PHASE7_HANDOFF.md`.
- Backend: `backend/app/` (routers, `auth.py`, `anchor.py`, `worker.py`, `billing.py`, `config.py`);
  migrations `backend/migrations/versions/` (head 0029). SDK: `sdk/src/foxy_audit/`. Verifier: `verifier/`.
- Desktop: `omni_fox.py` (main), `dashboard.py` (console), `fox_settings.py`, `sdk_bridge.py`.
- Frontend: `foxy-sale-page/index.html` (+ `fox-reveal.html`), `foxy-dashboard/foxy-audit-premium.html`,
  `foxy-adminpage/index.html`. Deploy: `deploy/` (`docker-compose.prod.yml`, `.env.example`, runbook).
