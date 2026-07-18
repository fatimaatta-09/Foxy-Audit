# Foxy Audit — Admin Console feature roadmap (build spec)

## How to use this doc
A standalone build spec for the **staff/admin ops console**. It assumes no prior context. Every feature is
grounded in data that **already exists** in the Postgres schema, names the tables / reuse points / file
paths, and is tagged with effort + priority. Build it **phase by phase on a feature branch**; each phase is
independent. Local setup is below; the merge workflow is at the end.

## System primer (what Foxy Audit is)
A three-tier product: a **PyQt6 desktop pet** (`desktop/`), a Python **SDK** (`@foxy.audit` decorator,
`sdk/`), and a **FastAPI + PostgreSQL backend** (`backend/`). The SDK hashes each AI prompt/response locally
(customer-keyed commitments) and sends only commitments + bounded metadata; the backend chains a versioned
canonical event (`chain_hash = SHA256(event + prev_hash)`)
so tampering is mathematically detectable; deterministic rules and an optional AI judge grade supported metadata;
chain heads are optionally
anchored to a public chain (Sepolia). Three web surfaces share one backend:
- **Marketing** — `foxyaudit.tech` (static `foxy-sale-page/`).
- **Customer dashboard** — `app.foxyaudit.tech` (backend serves `foxy-dashboard/*.html`; customer API `/v1/*`).
- **Staff ops console** — `admin.foxyaudit.tech/admin/` — **this is what the roadmap is about.**

The console is a single-file SPA `foxy-adminpage/index.html` talking to the **admin API** — a separate
FastAPI sub-app mounted at `/admin` (so routes are `/admin/v1/*`) in `backend/app/main.py`. It has its own
staff session cookie (`foxy_staff_session`, secret `STAFF_SESSION_SECRET`, `Path=/admin`), CSRF double-submit,
and an IP-allow-list guard middleware (fail-open by design). Admin routers live in `backend/app/routers/`:
`auth_staff, admin_orgs, admin_staff, admin_stats, admin_data, admin_inbox`, registered in a loop in
`main.py` (~L159).

## Staff roles (already implemented — `backend/app/auth.py`)
`platform_role` column on `staff_users`: **viewer** (0) < **operator** (1) < **superadmin** (2). Guards:
`require_staff` (any logged-in staff; reads cross-org — staff intentionally bypass RLS), `require_platform_role(min)`
(role gate), and `set_org_scope_for_staff(db, org_id)` (deliberate single-org RLS drill-down for scoped reads).
Gate every new **read** at viewer, **mutation** at operator, **staff/role management** at superadmin.

## Current admin surface (already exists — don't rebuild)
- **FE sections** (dock nav in `index.html`): Overview (KPIs + CSS-bar funnels), Orgs (list + shallow drawer +
  suspend/enable), Traffic (client-side-filtered 200-row feed), Staff (list/create/disable, superadmin), Data
  (generic table browser/editor), Inbox (support leads: claim/reply/lock/priority), Settings (dark-mode +
  identity). Claymorphism, light/dark, role-gated.
- **Admin endpoints:** `auth_staff` (login, emailed-OTP MFA, forgot/reset, logout, me); `admin_orgs` (list,
  detail-with-counts, suspend/enable); `admin_staff` (list, create/invite, disable — superadmin); `admin_stats`
  (`/stats` SQL-aggregated, `/traffic` feed); `admin_data` (generic CRUD over ~14 allowlisted tables, secrets
  stripped, some hard-locked); `admin_inbox` (list/unread/read/claim/release/reply).
- **Audit table** `admin_actions` exists and is written via `record_admin_action(...)`
  (`backend/app/routers/admin_audit.py`) by org/staff/data mutations — but NOT by auth or inbox actions, and
  there is no purpose-built viewer.

## Run + test locally
- **Backend + DB:** `cd backend && docker compose up --build -d` (Postgres + API + worker + migrate). Or run
  Postgres natively, then `alembic upgrade head` + `uvicorn app.main:app`. Tests: `pytest tests/integration -q`
  against a Postgres test DB (conftest runs `alembic upgrade head`; set `DATABASE_URL`, `API_KEY_PEPPER`,
  `SESSION_SECRET`). Baseline suite is green (~246 passed).
- **Admin console:** open `http://localhost:8000/admin/` (served by the backend). Log in with a superadmin
  `staff_users` row (bcrypt `password_hash`, `platform_role='superadmin'`) — see `backend/scripts/` for seeding.
- **Schema:** `backend/app/models.py` is the single source of truth for every table named below.

## Reuse foundations (lean on these; don't rebuild)
- Guards + scoping: `require_staff`, `require_platform_role(min)`, `set_org_scope_for_staff` — `backend/app/auth.py`.
- Audit: `record_admin_action(...)` → `admin_actions` (`backend/app/routers/admin_audit.py`) — stage it in the
  SAME transaction as the mutation.
- Data-browser allowlist + secret-strip patterns — `admin_data.py` (`TABLE_REGISTRY`, `_NEVER_EXPOSE`,
  `_HARD_LOCKED`).
- Customer logic to reuse **scoped to one org**: verify/export (`verify.py`, `logs.py`), anchoring (`anchor.py`,
  `anchors.py`), billing (`billing.py`), usage/quota (`usage.py`, `account.py`), keys (`keys.py`), login history
  (`login_history.py`).
- FE shell in `index.html`: the `api()` CSRF fetch wrapper, `go(page)` router, `.clay` cards, `fauxselect`
  dropdowns, toast, the `can(minRole)` role gate, and light/dark theme.

## Non-negotiable conventions
- **Reuse SQL-aggregated data** (e.g. the `usage_daily` rollups), never raw ledger scans.
- **Every staff mutation writes `admin_actions`** via `record_admin_action`.
- **Role-gate every action** (viewer read / operator act / superadmin manage) with `require_platform_role`.
- **Never expose secrets** — keep the `password_hash` / `key_hash` stripping.
- **No fake/placeholder data.** Empty states say so honestly.
- **Keep the claymorphism shell** + light/dark; charts are inline SVG/Canvas (the console CSP blocks font/JS CDNs).

## The raw material (data sitting in Postgres with no ops surface today)
Zero surface: `login_events` (brute-force signal), `consent_events` (GDPR), `auth_handoff_tokens`.
View-only / no-action: `audit_logs` (no staff verify/export/breach view), `chain_anchors` (no monitor / re-anchor
/ wallet), `stripe_events` (no failed-event replay). No ops for plan/quota change, refunds, comps, per-tenant IP
allow-list, requeue failed grading, per-org anchor/verify, worker/anchor/breach health, or MRR from
`invoices.amount_cents`. Email-only alerts (grading dead-letters `usage.py:135`, failed/stale anchors
`anchor.py:271`) never reach the console. No charts, exports, search/pagination on most lists, no audit-log
viewer, native `prompt()`/`confirm()`, thin settings.

---

## Feature catalog (by area)
Legend: **[FE]** frontend-only (data/endpoint exists) · **[BE+FE]** needs a new endpoint · effort S/M/L · P0–P3.

### A. Ops safety net — reliability command center
- **System Health panel** [BE+FE · M · P0] — worker heartbeat age (`worker_heartbeat.beat_at`), grading
  backlog (pending/failed), anchor freshness (newest `chain_anchors.confirmed_at`), DB status, circuit-breaker
  state. Assembles what only unauth `/health/ready` + email alerts show today. New `GET /admin/v1/health`.
- **Grading dead-letter queue** [BE+FE · M · P0] — list `audit_logs` rows in `grading_status='failed'` (global
  + per-org) with reason/attempts; **requeue/retry** (reset to `pending`); show circuit-breaker state.
  `audit_logs` is hard-locked today, so requeue is impossible. New `GET/POST /admin/v1/grading/deadletter`.
- **Anchor monitor** [BE+FE · M · P0] — status distribution, failed + stale anchors, per-org last-confirmed,
  wallet balance vs floor; **"anchor now"** + **"re-anchor failed"** per org (reuse `anchor.anchor_org`).
  Staff see none of this today (email-only). New `GET /admin/v1/anchors`, `POST …/{org}/anchor`.
- **Alerts center** [BE+FE · S · P0] — surface the email-only alerts on the console with ack.

### B. Tenant 360 — deep per-org drill-down
- **Org 360 dashboard** [BE+FE · L · P1] — one screen per org: profile (plan/subscription/quota/contact/trial/
  created), **usage timeseries** (`usage_daily`), ledger height + verify status, breaches, anchors, keys,
  users, invoices, policies, IP allow-list. Reuse `set_org_scope_for_staff`. Replaces the shallow 5-count drawer.
- **Per-org ledger + verify + export** [BE+FE · M · P1] — staff-scoped `…/{org}/logs`, `…/verify` (reuse
  `verify.py`), `…/export` JSON/CSV (reuse `logs.py`). The product's core proof, on the staff side.
- **Per-org breach view** [BE+FE · M · P1] — `policy_breach` verdicts + reason + risk_score from
  `audit_logs.gemini_verdict`.
- **Per-org API-key ops** [BE+FE · M · P1] — list / rotate / revoke (reuse `keys.py`), staff-scoped.
- **Per-org policy editor** [BE+FE · S · P1] — proper screen for `org_policies` (pii/injection/regulated/
  threshold/enforcement/confidence/notify_on_breach) vs raw field edits.
- **Org lifecycle** [BE+FE · S · P1] — keep suspend/enable; add plan change, quota set, comp/trial extension
  (`trial_ends_at`), offboard (soft-delete).
- **Per-org IP allow-list** [BE+FE · S · P2] — view/set `organizations.ip_allowlist` (customer-only today).

### C. Billing & revenue
- **Revenue dashboard** [BE+FE · M · P1] — MRR, revenue by plan, invoice totals, active/trial/churned from
  `invoices.amount_cents` + `subscription_status` (present, unsurfaced).
- **Invoices + billing ops** [BE+FE · M · P1] — per-org invoice history; plan change, quota override, comp/
  trial grant, **refund** (Stripe passthrough via `billing.py`).
- **Failed-webhook monitor + replay** [BE+FE · M · P1] — `stripe_events status='failed'` with error + a
  **reprocess** action (hard-locked today → no replay).

### D. Security & abuse
- **Login / brute-force monitor** [BE+FE · M · P2] — `login_events` (success+fail, ip, ua) — **zero surface
  today**; per-IP/per-email failed-login watchlist, per-org login history.
- **Account lockout** [BE · S · P2] — lock after N failed logins (staff + customer) on top of the existing
  per-IP rate limit (no lockout today).
- **Staff auth auditing** [BE · S · P2] — log staff login/MFA/logout/password-reset into `admin_actions`.
- **Traffic / abuse tooling** [BE+FE · M · P2] — server-side filter of `traffic_events` by org/status/endpoint,
  per-IP/per-org aggregation, error-rate by endpoint (columns exist; feed is client-side 200-row today).
- **Consent / GDPR audit** [BE+FE · S · P2] — `consent_events` view (region/regime/policy_version) — zero
  surface today; compliance proof.

### E. Audit & compliance
- **Audit-log viewer** [BE+FE · M · P2] — purpose-built, filterable `admin_actions` (actor/target/action/date),
  per-org history, export. Today only raw browser rows.
- **Expand audit coverage** [BE · S · P2] — log inbox actions + auth events (unlogged today).
- **Data-access logging** [BE · S · P3] — record staff reads of sensitive tables.

### F. Staff & access management
- **Role change + re-enable + MFA reset** [BE+FE · M · P2] — promote/demote `platform_role`, re-enable
  disabled staff, reset/enforce staff MFA (create/disable only today; role change needs direct DB).
- **Staff activity** [BE+FE · S · P3] — last login + action count per staff (`login_events` + `admin_actions`).
- **Session management** [BE+FE · M · P3] — active sessions, force-logout.

### G. Growth & CRM (leads)
- **Leads pipeline board** [BE+FE · M · P3] — new/trial/converted/churned kanban with claim/convert workflow,
  UTM attribution, lead→`converted_org_id` linkage (leads are only an Overview funnel + raw rows today).
- **Inbox upgrades** [BE+FE · S · P3] — audit-log inbox actions, canned replies, assignment.

### H. Analytics & reporting
- **Real charts** [FE · M · P3] — replace the CSS bars with real time-series (traffic, signups, breaches,
  grading throughput, anchor success, MRR) from `usage_daily`/`traffic_events`/`invoices`. Inline SVG/Canvas
  (CSP-safe, no CDN).
- **Date-range filters** [BE+FE · S · P3] — on `/stats` + dashboards.
- **Exports** [BE+FE · S · P3] — CSV/JSON for orgs, logs, audit, revenue.
- **Digest email** [BE · S · P3] — scheduled ops summary.

### I. Platform config & feature flags
- **Config panel** [BE+FE · M · P3] — edit anchor cadences, default quotas, alert thresholds, enforcement
  defaults without redeploy (env-only today; needs a small `platform_config`/flags table).
- **Broadcast / announcements** [BE+FE · M · P3] — banner or bulk email to orgs/users (1:1 inbox only today).
- **Maintenance mode** [BE+FE · S · P3].

### J. Console UX / quality (cross-cutting)
- **Server-side search / sort / pagination** [BE+FE · M · P2] — Orgs + Traffic (client-side/absent today).
- **Styled modals** [FE · S · P2] — replace native `prompt()`/`confirm()`.
- **Global search / command palette** [FE · M · P3] — jump to org/user/lead by email or id.
- **Skeleton loading + empty states** [FE · S · P3].
- **DB-introspection endpoint** [BE+FE · S · P3] — stop hardcoding the data-browser schema.

---

## Recommended build order
- **P0 — Ops safety net (§A):** System Health · grading dead-letter + requeue · anchor monitor + re-anchor ·
  alerts center. *First because it's reliability-critical, every signal already exists, and it stops silent
  failures.*
- **P1 — Tenant 360 + billing (§B, §C):** Org 360 dashboard · per-org ledger/verify/export/breaches/keys/
  policies · org-lifecycle actions · revenue dashboard · invoices/billing ops · failed-webhook replay.
- **P2 — Security & audit (§D, §E, §F + §J search/modals):** login/brute-force + lockout · staff auth
  auditing · audit-log viewer · consent view · staff role/MFA management · server-side org/traffic query ·
  styled modals.
- **P3 — Growth, analytics, config, polish (§G, §H, §I, rest of §J):** leads pipeline · real charts · exports ·
  feature flags/broadcast · command palette · skeletons · DB introspection.

Each phase ships as a self-contained set of new `admin_*` endpoints + console sections, deployed via
push→CD, tested per the pattern below. Phases are independent — we can reorder or cherry-pick.

---

## Where to work (critical files)
- **Backend:** new routers under `backend/app/routers/` — e.g. `admin_health.py`, `admin_grading.py`,
  `admin_anchors.py`, `admin_billing.py`, `admin_security.py`, `admin_audit_view.py` — each registered in the
  admin-router loop in `main.py` (~L159). Reuse `auth.set_org_scope_for_staff`, `admin_audit.record_admin_action`,
  and org-scoped copies of `verify.py` / `logs.py` / `anchor.py` / `keys.py` / `billing.py` logic.
- **Frontend:** extend the single file `foxy-adminpage/index.html` — new dock sections + panels using the
  existing `api()` / `go()` / `.clay` / `fauxselect` / `can()` patterns; charts as inline SVG/Canvas.
- **DB / migrations:** mostly reads of existing tables (`backend/app/models.py`). New columns/tables only where
  a phase needs them — a failed-login counter (lockout) and a small `platform_config` / feature-flags table
  (config panel). Add one Alembic migration for those, numbered after the latest (0034+).
- **Do NOT touch:** the customer `/v1/*` routers, the marketing site (`foxy-sale-page/`), or the customer
  dashboard (`foxy-dashboard/`). Keep the diff inside the admin surface.

## Testing (per feature / phase)
Backend TDD for each new `admin_*` endpoint: (1) **role-gating** — viewer/operator/superadmin get the right
200/403; (2) **per-org scoping** — a scoped read/action can't leak or hit the wrong tenant; (3) an
**`admin_actions` row is written** on each mutation; (4) the **action's effect** is correct. Keep the full
suite green (`pytest backend/tests/integration -q`, baseline ~246). Mirror the existing admin tests:
`tests/integration/test_admin_inbox.py`, `test_admin_guard.py`, `test_rls.py`, `test_isolation.py`. Manual
smoke against the local stack — e.g. P0: kill the worker → Health panel shows a stale heartbeat + backlog;
force a grading row to `failed` → it appears in the dead-letter list → requeue clears it; a failed/stale
anchor shows in the monitor → "re-anchor" fixes it.

## Branch & merge workflow
- Build on a dedicated feature branch, **one phase per branch/PR** (e.g. `feat/admin-p0-ops-safety-net`), so
  each phase is reviewable and independently mergeable.
- Keep the diff confined to the admin surface (see "Where to work"). Add tests with every endpoint and keep the
  full backend suite green before opening the PR.
- Open the PR against `main`. On merge, CI + CD redeploy the stack and serve the updated console; hard-refresh
  `admin.foxyaudit.tech/admin/` to see the changes.
- Each phase is self-contained (it adds new routers + new console sections rather than rewriting existing
  ones), so phases can be built and merged in any order, by different people, with minimal conflicts.
