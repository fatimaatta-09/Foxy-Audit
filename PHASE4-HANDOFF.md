# Foxy Audit — Phase 4 #1 Handoff: Admin Database + Strict Route-Guarding

> **Purpose of this file.** This is a complete, self-contained brief so a fresh chat can pick up
> exactly where we left off. It describes the product, the approved plan, **everything already built
> (and verified passing)**, how to run it, what remains, and the security rules that must never be
> broken. Paste this whole file into the new chat as the first message, or point the new chat at
> `PHASE4-HANDOFF.md` in the repo root.
>
> **Repo:** `c:\Users\Dell\Downloads\Foxy-Audit`  ·  **Branch:** `foxy-skeleton`  ·  **Status of this
> phase's code: implemented + all 72 backend integration tests passing.**

---

## 0. TL;DR for the new chat

We just built the **backend database + access-control foundation for Foxy Audit's internal admin
website ("site 3")**, plus full in/out traffic tracking and sales/usage data. It's done and tested.
**The main thing left is the admin site's HTML/JS UI (`foxy-audit-staff.html`)** that consumes the
`/admin/v1/*` endpoints we just created — that was deliberately deferred to the next phase.

Nothing has been committed to git yet (we only commit when explicitly asked). All work is on the
working tree of branch `foxy-skeleton`.

---

## 1. What Foxy Audit is

A three-tier compliance-audit product for AI/LLM usage:

- **Desktop app** (PyQt6) — an animated pixel-art fox mascot (in the repo root: `omni_fox.py`, etc.).
  Not relevant to this phase.
- **Backend** (`backend/`) — FastAPI + PostgreSQL + a Python SDK (`sdk/`). This is where all Phase 4
  work happened.
- The product is delivered as **three websites that all share ONE backend and ONE database**:

  | # | Site | Who | Lives in | Deploys to (planned) |
  |---|------|-----|----------|----------------------|
  | 1 | **Marketing / signup** | the public | `foxy_salepage/` (static, exists) | `foxyaudit.com` |
  | 2 | **Customer dashboard** | one paying tenant (sees only its own data) | `foxy-dashboard/foxy-audit-premium.html` | `app.foxyaudit.com` |
  | 3 | **Internal admin ("site 3")** | foxy.audit staff (see ALL orgs) | `foxy-adminpage/index.html` (UI **in progress**), served at `GET /admin/`; backend done | `admin.foxyaudit.com` |

The three sites are **three lenses on the same data**: site 1 creates tenants (signup → Stripe →
provision), site 2 shows one tenant, site 3 shows all tenants + traffic + sales. They are separated by
**folders + separate deployments/subdomains**, NOT git branches, and NOT separate databases.

### Backend core concepts you must know
- **Multi-tenant isolation** via a per-request Postgres RLS GUC `app.current_org`, set through
  `set_config('app.current_org', <org_id>, true)` (transaction-local). Tables `audit_logs`,
  `chain_anchors`, and now `invoices` + `usage_daily` have `ENABLE`+`FORCE ROW LEVEL SECURITY` with an
  `org_isolation` policy `USING (org_id = current_setting('app.current_org', true)::uuid)`.
- **⚠️ Load-bearing fact:** the docker Postgres role `foxy` is a **SUPERUSER, so it BYPASSES RLS
  entirely.** Real tenant isolation today is enforced by **app-level `WHERE org_id` filters**; RLS is
  defense-in-depth for a future hardened non-superuser role. This is what lets **staff read
  cross-org**: with `app.current_org` unset, a superuser sees all rows.
- **Hash chain:** `audit_logs` is an append-only per-org chain
  (`chain_hash = SHA256(org_id|prompt_hash|response_hash|token_count|policy_tag|seq + prev_hash)`).
- **API keys:** HMAC-with-pepper (`hash_key(token) = hmac_sha256(API_KEY_PEPPER, token)`), with a
  legacy plain-sha256 fallback. The pepper is reused to hash IPs/UAs in traffic tracking.
- **Two auth channels historically:** SDK machine keys (`Authorization: Bearer` → `require_org`) and
  human dashboard sessions (signed cookie → `require_user`). We added a **third**: platform staff.

---

## 2. This phase's goal + the two hard requirements

The user asked (verbatim intent):

1. **"Database Architecture first."** A comprehensive SQL plan where **all data + tracking (in/out) is
   fully visible and manageable via the 3rd administrative website.**
2. **Strict security constraint:** *"under no circumstances can any user bypass access controls to view
   or interact with any sub-page or domain they are not explicitly authorized to access."*

Two scope decisions the user confirmed:
- **Build the DB schema AND the backend access-control layer together** (not DB-only; not all the way
  to the admin UI). → The admin **UI** is the deferred next step.
- **Include full per-request traffic logging** (the heaviest "in/out" option), not just rollups.

The full approved plan is saved at `C:\Users\Dell\.claude\plans\tell-me-this-plan-cheerful-sunset.md`
(and is reproduced in section 4 in effect). This handoff supersedes it with "what actually got built."

---

## 3. Architecture decisions & the "why" (read before touching anything)

### 3.1 Three ASGI surfaces = sibling mounted sub-apps (NOT nested middleware)
The strict no-bypass rule is delivered by making the **customer** and **staff** channels *provably
disjoint at the ASGI layer.* `backend/app/main.py` now builds:

- `customer_api` (FastAPI) — all `/v1/*` routes + `/dashboard`; its own `SessionMiddleware`
  (cookie **`session`**, secret = `SESSION_SECRET`) + customer CORS + traffic middleware (`site="app"`).
- `admin_api` (FastAPI) — the `/admin/v1/*` routes; its own `SessionMiddleware`
  (cookie **`foxy_staff_session`**, a **DISTINCT** secret = `STAFF_SESSION_SECRET`, `same_site=strict`,
  `path=/admin`) + admin CORS (separate origin list) + IP allow-list guard + traffic middleware
  (`site="admin"`).
- `app` (root, bare) — **no middleware**; only `app.mount("/admin", admin_api)` then
  `app.mount("/", customer_api)`.

**Why sibling sub-apps and not two `SessionMiddleware` on one app (which the plan first suggested):**
Starlette's `SessionMiddleware` stores the session under `scope["session"]`. Two of them on one app
**collide** — the inner overwrites the outer, and on the way out the outer re-serializes the *inner's*
session into the *wrong* cookie (staff data could leak into the customer cookie). Likewise a single
global `CORSMiddleware` **short-circuits preflight OPTIONS** before a mounted sub-app's CORS can run.
Making them siblings under a bare root gives each surface a fully isolated middleware stack. **A
customer `session` cookie sent to `/admin/*` is simply ignored** (admin app only reads
`foxy_staff_session`), and the staff cookie (`path=/admin`) is never sent to `/v1/*`. This is tested.

### 3.2 Platform-staff auth
- New table `staff_users` (global, **no `org_id`, no RLS**), roles `viewer < operator < superadmin`.
- New dependencies in `backend/app/auth.py`:
  - `require_staff(request, db)` — reads **only** `request.session["staff_user_id"]`; **never** sets
    `app.current_org` (so staff read cross-org via the superuser bypass); rejects missing/disabled.
  - `require_platform_role(min_role)` — ordered ladder; superadmin passes everything.
  - `set_org_scope_for_staff(db, org_id)` — the *only* way a staff endpoint drills into one org's
    RLS-protected rows (reuses the RLS path instead of ad-hoc SQL).
- Staff login (`auth_staff.py`) queries **only** `staff_users`, so a customer credential can never mint
  a staff session; rotates the session on login (anti-fixation); rate-limited 10/min.

### 3.3 Traffic capture is OFF the request hot path
`backend/app/middleware/traffic.py` (`TrafficMiddleware`) records one row per request into the
partitioned `traffic_events` table via a **small threadpool** — the request never waits on the DB
write, and a write error is swallowed (telemetry must never break a request). It reads the logged-in
session (it's mounted *inside* each sub-app, after `SessionMiddleware`) to attribute `org_id`/`user_id`/
`staff_id`. **Privacy:** IP + User-Agent are HMAC-hashed with the pepper (never raw); path/referrer are
stripped of query strings; no bodies/headers/secrets stored. Marketing pageviews come via the public
`POST /v1/track` beacon (writes a `site='marketing'` row synchronously).

### 3.4 `traffic_events` is range-partitioned by month
Retention = an instant `DROP TABLE <old partition>` (not a vacuum-storming `DELETE` on a hot table);
time-bounded admin queries prune to one partition. A **DEFAULT partition** guarantees an insert never
fails if partition creation lags. The worker (`app/usage.py::maintain_partitions`) pre-creates next
month and drops partitions past `TRAFFIC_RETENTION_DAYS` (default 90). PK is `(id, created_at)` because
the partition key must be in the PK.

### 3.5 Stripe webhook is now durable + idempotent
`billing.py`: every verified event is FIRST inserted into `stripe_events`
(`INSERT … ON CONFLICT (stripe_event_id) DO NOTHING`) → a replay is a no-op returning `200`. Then it's
dispatched and the event row is stamped `processed|ignored|failed` **in the same transaction** as the
org/invoice mutation. Adds `invoices` upsert (on `invoice.paid|payment_failed|finalized`) and tags a
matching `marketing_leads` row as `converted` on checkout.

### 3.6 Usage rollups
`app/usage.py` runs in a **worker thread** (like `_anchor_loop`), incrementally upserting **today +
yesterday** into `usage_daily` (older days are immutable). Admin dashboards read aggregates from here
instead of scanning `audit_logs`.

### 3.7 Escalation fix shipped alongside
`PUT /v1/policies` used to accept a bare SDK Bearer key or any `member` (a privilege-escalation hole).
It now requires `require_role("admin")` (a human admin session). `GET /v1/policies` still reads via
`resolve_org`.

---

## 4. Everything implemented (file-by-file)

### 4.1 Migrations (all additive → the running app never breaks mid-migration)
Located in `backend/migrations/versions/`. Chain: `0009 → 0010 → … → 0016`. `alembic upgrade head`
verified clean (head = **0016**), and every new table + partition exists.

| Rev | File | Creates | RLS? |
|-----|------|---------|------|
| 0010 | `0010_staff_and_admin_audit.py` | `staff_users` (roles CHECK), `admin_actions` | **No** (platform-only) |
| 0011 | `0011_org_metadata.py` | ALTER `organizations` +6 cols (guarded, idempotent) | n/a |
| 0012 | `0012_traffic_events.py` | `traffic_events` **PARTITION BY RANGE(created_at)** + default + this/next month partitions + 4 indexes | **No** (org_id nullable) |
| 0013 | `0013_marketing_leads.py` | `marketing_leads` + partial-unique `lower(email) WHERE status<>'churned'` | **No** |
| 0014 | `0014_stripe_events.py` | `stripe_events` (UNIQUE `stripe_event_id`) | **No** |
| 0015 | `0015_invoices.py` | `invoices` | **Yes** (copies 0009 policy block) |
| 0016 | `0016_usage_daily.py` | `usage_daily` (UNIQUE org_id,day) + one-time backfill over `audit_logs` | **Yes** |

**RLS decision rule:** a table gets org-scoped RLS **iff a customer ever reads it** (invoices,
usage_daily). A **nullable `org_id`** forces platform-only-no-RLS (traffic_events, stripe_events),
because the RLS predicate can never match a NULL and would hide the rows staff need.

Exact `organizations` additions (0011): `contact_email VARCHAR(320)`, `trial_ends_at TIMESTAMPTZ`,
`deleted_at TIMESTAMPTZ` (soft delete — **never hard-delete an org**), `suspended BOOLEAN NOT NULL
DEFAULT false`, `suspended_reason VARCHAR(255)`, `monthly_log_quota INTEGER` (NULL = unlimited).

### 4.2 Models — `backend/app/models.py`
Added `Organization` columns above, plus 7 new ORM classes matching existing `Mapped`/`mapped_column`
style: `StaffUser`, `AdminAction`, `TrafficEvent`, `MarketingLead`, `StripeEvent`, `Invoice`,
`UsageDaily`. Each has a docstring stating its RLS/visibility model.

### 4.3 Config — `backend/app/config.py`
Added: `staff_session_secret`, `staff_session_max_age` (2h), `staff_cookie_domain`, `session_max_age`
(12h), `admin_cors_origins` + `get_admin_cors_origins()`, `admin_ip_allowlist` +
`get_admin_ip_allowlist()`, `traffic_tracking_enabled`, `traffic_retention_days` (90),
`usage_rollup_interval` (300s), and an `is_prod` property. `_require_secure_prod` now also fails fast in
prod if `STAFF_SESSION_SECRET` is empty/default **or equals** `SESSION_SECRET`.

### 4.4 Auth — `backend/app/auth.py`
Added `require_staff`, `require_platform_role`, `set_org_scope_for_staff`, `_PLATFORM_ROLES` ladder;
imported `StaffUser`. (Existing `require_org`/`resolve_org`/`require_user`/`require_role` unchanged.)

### 4.5 Routers (new + changed)
- **NEW** `backend/app/routers/auth_staff.py` — `POST /admin/v1/auth/login` (rate-limited, timing-equalized,
  session-rotating), `/logout`, `GET /admin/v1/auth/me`. Has its own `slowapi` `limiter`.
- **NEW** `backend/app/routers/admin_orgs.py` — `GET /admin/v1/organizations` (viewer, cross-org),
  `GET /admin/v1/organizations/{id}` (viewer; uses `set_org_scope_for_staff` for that org's ledger
  counts), `POST …/{id}/suspend` + `…/enable` (operator; each writes an `admin_actions` row).
- **NEW** `backend/app/routers/admin_staff.py` — `GET/POST /admin/v1/staff`, `POST …/{id}/disable`
  (**superadmin only**; self-disable blocked; audited).
- **NEW** `backend/app/routers/admin_stats.py` — `GET /admin/v1/stats` (platform KPIs from rollups),
  `GET /admin/v1/traffic` (recent in/out feed + by-site + error counts).
- **NEW** `backend/app/routers/leads.py` — public `POST /v1/leads` (dedup active lead by email) and
  `POST /v1/track` (marketing beacon → `site='marketing'` row). Both rate-limited (reuse `logs.limiter`).
- **CHANGED** `backend/app/routers/billing.py` — idempotent `stripe_events` + invoice upsert + lead
  conversion (see 3.5). Handlers now take a shared `db` and are unit-testable.
- **CHANGED** `backend/app/routers/policies.py` — `PUT` hardened to `require_role("admin")` (see 3.7).

### 4.6 Middleware — `backend/app/middleware/`
- `traffic.py` — `TrafficMiddleware(site=...)` + threadpool writer + `flush()` (for tests/shutdown).
- `admin_guard.py` — `AdminIPAllowlistMiddleware` (403s non-allowed IPs before auth; empty list = allow all).
- `__init__.py` — empty package marker.

### 4.7 Helpers / workers / scripts
- **NEW** `backend/app/admin_audit.py` — `record_admin_action(...)` (stages an `AdminAction`, caller
  commits in the same txn) + `client_ip(request)`.
- **NEW** `backend/app/usage.py` — `rollup_recent`, `maintain_partitions`, `run_once`, `usage_loop`.
- **CHANGED** `backend/app/worker.py` — starts a `foxy-usage` daemon thread running `usage_loop`
  (alongside the existing grading poller + `foxy-anchor` thread).
- **NEW** `backend/scripts/seed_staff.py` — bootstrap the **first** superadmin; refuses to run if staff
  already exist (anti-backdoor) unless `--allow-additional`.

### 4.8 Tests — `backend/tests/integration/`
- **NEW** `test_staff_auth.py` — the no-bypass proof: customer cookie → 401 on every `/admin/v1/*`;
  staff cookie → 401 on `/v1/*` customer routes; role ladder (viewer/operator/superadmin);
  cross-org visibility vs customer isolation; **every mutation writes exactly one `admin_actions` row**;
  hardened policy write rejects member/Bearer.
- **NEW** `test_tracking_billing.py` — leads capture+dedup, marketing beacon, traffic middleware capture
  (enables tracking, `flush()`, asserts row + hashed IP + org attribution), Stripe idempotent insert,
  checkout provisioning + lead conversion, invoice upsert idempotency, usage rollup aggregation.
- **CHANGED** `conftest.py` — `_DATA_TABLES` now truncates the 7 new tables; added `make_staff` +
  `staff_login` fixtures + `STAFF_SESSION_SECRET`/`TRAFFIC_TRACKING_ENABLED=false` env; `_clean_db` now
  also resets the `logs` + `auth_staff` rate limiters between tests (all TestClients share one IP).

---

## 5. Endpoint inventory + the enforced route-guard boundary

| Surface | Path prefix | Cookie read | Guard | Sees |
|---|---|---|---|---|
| Marketing (public) | `/v1/leads`, `/v1/track` | none | rate-limit only | n/a |
| Customer SDK/dashboard | `/v1/*`, `/dashboard` | `session` | `require_org` / `resolve_org` / `require_user` / `require_role` + RLS GUC | its **one** org |
| Internal admin | `/admin/v1/*` | `foxy_staff_session` | `require_staff` / `require_platform_role`, GUC unset | **all** orgs |

Admin endpoints: `POST /admin/v1/auth/{login,logout}`, `GET /admin/v1/auth/me`,
`GET /admin/v1/organizations[/{id}]`, `POST /admin/v1/organizations/{id}/{suspend,enable}`,
`GET/POST /admin/v1/staff`, `POST /admin/v1/staff/{id}/disable`, `GET /admin/v1/stats`,
`GET /admin/v1/traffic`.

---

## 6. How to run everything (Windows, from `backend/`)

**Postgres:** docker container `foxy_pg` (postgres:16, user/pw `foxy`) on `localhost:5432`. Native
Windows PostgreSQL also wants 5432 — if auth fails, stop it (`Stop-Service postgresql-x64-18`) and use
the container. Databases: `foxy` (compose), `foxy_test` (manual), `foxy_pytest` (the test suite).

**⚠️ Do NOT run two Claude/agent sessions against the same `foxy_pytest` DB at once** — concurrent
writes made ~42 tests fail spuriously earlier; running them alone gives **72 passed**.

**Venv python:** `backend/.venv/Scripts/python.exe`.

```bash
# Apply migrations (creates the 7 new tables + traffic partitions)
cd backend
DATABASE_URL='postgresql+psycopg://foxy:foxy@localhost:5432/foxy_pytest' \
  API_KEY_PEPPER=testpepper SESSION_SECRET=devx STAFF_SESSION_SECRET=devy \
  ./.venv/Scripts/python.exe -m alembic upgrade head

# Run the full integration suite (expect: 72 passed)
DATABASE_URL='postgresql+psycopg://foxy:foxy@localhost:5432/foxy_pytest' \
  API_KEY_PEPPER=testpepper SESSION_SECRET=devx STAFF_SESSION_SECRET=devy \
  ./.venv/Scripts/python.exe -m pytest tests/integration -q

# Run the API (customer + admin surfaces). Prod requires distinct STAFF_SESSION_SECRET.
DATABASE_URL=... API_KEY_PEPPER=... SESSION_SECRET=... STAFF_SESSION_SECRET=... \
  ./.venv/Scripts/python.exe -m uvicorn app.main:app --port 8000

# Bootstrap the first admin-site superadmin
DATABASE_URL=... API_KEY_PEPPER=... SESSION_SECRET=... STAFF_SESSION_SECRET=... \
  ./.venv/Scripts/python.exe scripts/seed_staff.py --email you@foxy.audit --password 'strongpass1'
```

Existing memory note `backend-local-run-and-tests.md` has more detail (RLS/superuser, ports).

---

## 7. Current status

- ✅ All code implemented and imports clean.
- ✅ `alembic upgrade head` clean (head **0016**); all new tables + `traffic_events_YYYY_MM` + default
  partition present.
- ✅ **`pytest tests/integration` → 72 passed, 0 failed** (in a quiet repo).
- ⚠️ Not committed to git (waiting for explicit user go-ahead). Working tree, branch `foxy-skeleton`.
- ⚠️ The earlier "42 failed" scare was a **second concurrent Claude chat writing the same test DB** —
  not a code issue.

---

## 8. What remains / recommended next steps (for the new chat)

1. **Build the admin site UI — `foxy-adminpage/index.html`** (this is the deferred main deliverable; a
   parallel session has already scaffolded the `GET /admin/` route + `_find_admin_html()` in `main.py`,
   with `FOXY_ADMIN_HTML` override — so the file just needs to be created at `foxy-adminpage/index.html`).
   Mirror `foxy-dashboard/foxy-audit-premium.html`'s structure/token-driven theming; pages: Orgs list,
   Org detail (users/keys/logs/billing/suspend), Staff management (superadmin), Platform stats, Traffic
   feed. Client route guards are **UX only** — the server already re-verifies via `require_staff`. It
   must POST login to `/admin/v1/auth/login` and rely on the `foxy_staff_session` cookie (already
   served same-origin at `/admin/`, so cookie `Path=/admin` works with no CORS).
2. **Wire the customer dashboard** to consume any newly relevant endpoints (e.g. show `usage_daily`
   quota, invoice history from `/v1/`… note: customer-facing invoice/usage read endpoints under `/v1`
   are **not yet built** — only the admin views + the tables exist. Add `GET /v1/invoices` /
   `GET /v1/usage` (resolve_org) if the customer dashboard needs them).
3. **Marketing site (site 1) signup wiring** — point `foxy_salepage/` forms at `POST /v1/leads` and the
   beacon at `POST /v1/track`; confirm the Stripe checkout → webhook → provision → lead-conversion path.
4. **Ops/deploy housekeeping:** add the new env vars to `backend/.env.example`
   (`STAFF_SESSION_SECRET`, `STAFF_COOKIE_DOMAIN`, `ADMIN_CORS_ORIGINS`, `ADMIN_IP_ALLOWLIST`,
   `TRAFFIC_TRACKING_ENABLED`, `TRAFFIC_RETENTION_DAYS`, `USAGE_ROLLUP_INTERVAL`); update
   `docker-compose.yml` to pass them; CI already runs `pytest tests/integration` so the new tests ride
   along.
5. **Deployment split:** three subdomains (`foxyaudit.com` / `app.foxyaudit.com` / `admin.foxyaudit.com`),
   set `STAFF_COOKIE_DOMAIN=admin.foxyaudit.com`, put admin behind `ADMIN_IP_ALLOWLIST` and/or SSO,
   `FOXY_ENV=prod` (forces TLS-only cookies + secret fail-fast).
6. **Adversarial review** of the new authz surface (a good use of a review workflow): confirm no
   `/admin/v1/*` route is reachable without a valid staff session, no privilege escalation, no
   cross-tenant leak, and every mutation is audited.

---

## 9. Security constraints that MUST be preserved (do not regress)

- **Never merge the two session channels.** Customer = cookie `session` + `SESSION_SECRET`; staff =
  cookie `foxy_staff_session` + `STAFF_SESSION_SECRET`. They must stay on **separate sibling sub-apps**.
  Do not add a global `SessionMiddleware`/`CORSMiddleware` to the root `app`.
- **`require_staff` must never read the customer session key or set `app.current_org`** to a single org
  implicitly; cross-org reads are intentional and go through hand-audited endpoints only.
- **Every state-changing staff action writes an `admin_actions` row in the same transaction.** Staff
  must never be able to DELETE from `admin_actions`.
- **Traffic capture must never raise into a request** and must never store raw IP/UA/bodies/secrets.
- **Never hard-delete an org** (soft delete via `deleted_at`); its audit chain must stay provable.
- **`PUT /v1/policies` stays admin-only.** Don't revert it to `resolve_org`.
- **Commit/push only when the user explicitly asks.** Keep real secrets out of git (`backend/.env` is
  gitignored and holds a live Sepolia key — never commit it).

---

## 10. Gotchas / load-bearing assumptions

- **Superuser RLS bypass is load-bearing** for staff cross-org reads. If the DB is ever moved to a
  non-superuser role, staff endpoints need `BYPASSRLS` (or a dedicated connection), and any customer
  query missing its `WHERE org_id` would then start leaking — audit for that before hardening the role.
- **`traffic_events` partition dependency:** if next month's partition is missing, inserts land in the
  DEFAULT partition (never lost); the worker's `maintain_partitions` pre-creates it. Adding a monthly
  partition fails if the DEFAULT already holds rows for that month — the worker creates next month
  *before* rows exist, which is why it must keep running.
- **Starlette `SessionMiddleware` supports `path=`/`domain=`** (confirmed v1.3.1) — used to scope the
  staff cookie to `/admin`.
- **Rate limiters are process-global and keyed by IP**; all `TestClient`s share IP `testclient`, so
  `conftest._clean_db` resets `logs.limiter` and `auth_staff.limiter` between tests.
- **`get_settings()` is `lru_cache`d;** the traffic test mutates `get_settings().traffic_tracking_enabled`
  in place and restores it in a `finally`.
- The dashboard HTML was moved to `foxy-dashboard/foxy-audit-premium.html` (main.py path resolution
  already updated for this).

---

## 11. Key file paths (quick reference)

```
foxy-adminpage/index.html                # site-3 admin UI (in progress); served at GET /admin/
backend/app/main.py                      # sibling sub-apps: customer_api + admin_api under bare root; GET /admin/ serves the admin UI
backend/app/auth.py                      # require_staff / require_platform_role / set_org_scope_for_staff
backend/app/config.py                    # staff/admin/traffic/usage settings + prod fail-fast
backend/app/models.py                    # +Organization cols, +7 new models
backend/app/admin_audit.py               # record_admin_action(), client_ip()
backend/app/usage.py                     # rollup_recent / maintain_partitions / usage_loop
backend/app/worker.py                    # starts foxy-usage thread
backend/app/middleware/traffic.py        # TrafficMiddleware (off-thread writer) + flush()
backend/app/middleware/admin_guard.py    # AdminIPAllowlistMiddleware
backend/app/routers/auth_staff.py        # /admin/v1/auth/*
backend/app/routers/admin_orgs.py        # /admin/v1/organizations*  (+ suspend/enable)
backend/app/routers/admin_staff.py       # /admin/v1/staff*  (superadmin)
backend/app/routers/admin_stats.py       # /admin/v1/stats, /admin/v1/traffic
backend/app/routers/leads.py             # public /v1/leads, /v1/track
backend/app/routers/billing.py           # idempotent stripe_events + invoices + lead conversion
backend/app/routers/policies.py          # PUT hardened to admin-only
backend/migrations/versions/0010_*.py … 0016_*.py
backend/scripts/seed_staff.py            # bootstrap first superadmin
backend/tests/integration/test_staff_auth.py
backend/tests/integration/test_tracking_billing.py
backend/tests/integration/conftest.py    # +make_staff/staff_login, +new-table truncation, +limiter reset
```

**Approved plan file:** `C:\Users\Dell\.claude\plans\tell-me-this-plan-cheerful-sunset.md`
**Run/DB notes memory:** `…\memory\backend-local-run-and-tests.md`

---

*End of handoff. The backend for site 3 is built and green; the next chat's headline job is the
`foxy-audit-staff.html` admin UI on top of the `/admin/v1/*` API described above.*
