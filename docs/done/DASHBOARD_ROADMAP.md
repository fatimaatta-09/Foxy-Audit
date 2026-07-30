# Foxy Audit — Customer Dashboard feature roadmap (build spec)

## How to use this doc
A standalone build spec for the **customer dashboard** (the paying customer's web app at `app.foxyaudit.tech`).
Assumes no prior context. Every feature is grounded in data that **already exists** in the Postgres schema and
the customer `/v1/*` API, names the tables / reuse points / file paths, and is tagged with effort + priority.
Build it **phase by phase on a feature branch**; each phase is independent. Local setup is below; the merge
workflow is at the end. (A sibling spec for the staff/admin console is at `docs/ADMIN_CONSOLE_ROADMAP.md` —
that is a different surface; this one is the customer app.)

## System primer (what Foxy Audit is)
A three-tier product: a **PyQt6 desktop pet** (`desktop/`), a Python **SDK** (`@foxy.audit` decorator, `sdk/`),
and a **FastAPI + PostgreSQL backend** (`backend/`). The SDK hashes each AI prompt/response locally (SHA-256 or
an HMAC commitment keyed by the API key) and sends only hashes + metadata; the backend chains them
(`chain_hash = SHA256(row + prev_hash)`) so tampering is mathematically detectable; an AI "judge" (Gemini)
grades each call; chain heads are optionally anchored to a public chain (Sepolia). Three web surfaces share one
backend:
- **Marketing** — `foxyaudit.tech` (static `foxy-sale-page/`).
- **Customer dashboard** — `app.foxyaudit.tech` — **this is what this roadmap is about.**
- **Staff ops console** — `admin.foxyaudit.tech/admin/` (separate spec).

The dashboard is a single-file SPA `foxy-dashboard/foxy-audit-premium.html` (served by the backend at
`/dashboard`) talking to the **customer API** — the `customer_api` FastAPI app built in `backend/app/main.py`
(routes `/v1/*`). Customer routers in `backend/app/routers/`: `auth_human`, `auth_google`, `logs`, `verify`,
`passport`, `keys`, `policies`, `account`, `billing`, `anchors`, `analytics`, `consent`, `badge`, `health`.
The desktop pet auto-logs-in via a handoff token (`POST /v1/auth/handoff` → `?handoff=` redeem).

## Customer auth + roles (already implemented — `backend/app/auth.py`, `auth_human.py`)
- **`require_org`** — machine auth, `Authorization: Bearer <key>` (the SDK). Sets RLS scope for the org.
- **`require_user`** — human dashboard session (signed `session` cookie); also enforces the org IP allow-list.
- **`require_role("admin"|"member")`** — gates on `users.role`.
- **`resolve_org`** — read endpoints: accepts EITHER a Bearer key OR a dashboard session. The workhorse.
Two org roles: **admin** (manage keys, policies, team, billing, account) and **member** (read). MFA is an
emailed OTP at login; Google OIDC SSO exists. Every RLS-scoped query is confined to the caller's org.

## Current dashboard surface (already exists — don't rebuild)
9 sections (dock nav in the SPA), claymorphism, light/dark:
- **Dashboard** (home) — hero KPI tiles + 7-day activity sparkline (`/v1/stats`).
- **Analytics** — threat analytics (`/v1/analytics/threats`: top policies, avg risk, recent high-risk).
- **Ledger** — paginated interactions feed (`/v1/logs`), per-row quick-verify (`/v1/verify/hash/{hash}`).
- **Verify** — verify a record + full-chain recompute (`/v1/verify`) + **"anchor now"** (`POST /v1/anchors`).
- **Policy** — the 7 policy toggles (`GET/PUT /v1/policies`: pii / injection / regulated / token-threshold /
  enforcement / confidence / notify_on_breach).
- **Export** — Compliance Passport PDF (`POST /v1/passport`).
- **Keys** — list / create / revoke keys + 2FA-gated regenerate (`/v1/keys*`).
- **Billing** — invoices (`/v1/invoices`), usage vs quota (`/v1/usage`), IP allow-list, trust badge
  (mint/revoke `/v1/account/badge`), workspace data export (`/v1/logs/export`), delete workspace, add user.
- **Settings** — profile/identity, change password, team users (add/disable), login history
  (`/v1/auth/login-history`).

## Run + test locally
- **Backend + DB:** `cd backend && docker compose up --build -d` (Postgres + API + worker + migrate). Or run
  Postgres natively, then `alembic upgrade head` + `uvicorn app.main:app`. Tests: `pytest tests/integration -q`
  (conftest sets its own env via `setdefault`, so do NOT export `SESSION_SECRET`/`STAFF_SESSION_SECRET` — let
  it default; it runs `alembic upgrade head` itself). Baseline green (~258 passed).
- **Dashboard:** open `http://localhost:8000/dashboard`. Create an org via `POST /v1/signup` (returns an SDK
  key + invites an admin) or seed an `organizations` row + a `users` row (bcrypt `password_hash`, `role='admin'`).
- **Schema:** `backend/app/models.py` is the source of truth for every table named below.

## Reuse foundations (lean on these; don't rebuild)
- Guards: `require_user`, `require_role("admin")`, `resolve_org` — `backend/app/auth.py`.
- Ledger/proof: `verify.py` (chain + per-hash verify), `logs.py` (list/breaches/export/stats), `passport.py`
  (signed PDF), `analytics.py` (threats). Anchoring: `anchors.py` + `anchor.py`. Usage/quota: `account.py`
  (`/v1/usage`) + `usage.py` rollups. Keys: `keys.py` (create/revoke/rotate/2FA-regen). Policies: `policies.py`.
  Team/auth: `auth_human.py` (users list/create/disable, login-history, MFA-at-login, handoff). Billing:
  `billing.py` (checkout + Stripe webhook), invoices via `account.py`. Trust badge: `account.py` + `badge.py`
  (public `/v1/badge/{token}.svg`).
- MFA OTP helper: `mfa.py` (new_otp / hash / verify / TTL) — reuse for MFA self-enrollment. Emails: `email.py`
  `send_email(...)` (Brevo). Standalone independent verifier: `verifier/foxy_verify.py`.
- FE shell in the SPA: the CSRF-aware `api()` / `fetch` wrapper, `go(page)` router, `.clay` cards, themed
  dropdowns, toast, `revealSecret(...)` (shown-once secrets), light/dark theme.

## Non-negotiable conventions
- **Tenant isolation:** every new read/write must be RLS-scoped to the caller's org (`require_user` / `resolve_org`
  set the scope) — a customer must never see another org's data.
- **Role-gate mutations** at `require_role("admin")`; reads at `resolve_org`.
- **Secrets shown once, never re-readable** — reuse `revealSecret` / the create-key-returns-plaintext-once
  pattern; never echo `key_hash` / `password_hash`.
- **Reuse SQL-aggregated data** (`usage_daily`, `/v1/stats`, `/v1/analytics/threats`), not raw ledger scans;
  respect the 50k-row verify guard.
- **No fake/placeholder data;** honest empty states. Stats are content-blind and truthful — `clean_rate` can be
  `null` (no determinate grades yet) and "unknown" verdicts are never counted as clean; the FE must render those
  honestly (see §J P0 quick-fix).
- **Keep the claymorphism look** + light/dark; charts are inline SVG/Canvas (the SPA CSP blocks CDNs).

## The raw material (data / capability under-surfaced today)
- **MFA self-enrollment:** `users.mfa_enabled` is enforced at login but there is **no `/v1` endpoint** for a
  user to turn on/off their own 2FA.
- **Team management is partial:** list / invite / disable exist; **no** change-role, re-enable, remove, or
  resend-invite.
- **Billing self-serve is thin:** only `checkout-session` + read-only `/v1/invoices`; **no** Stripe billing
  portal, cancel/upgrade/downgrade, payment-method update, or a current-plan/subscription read endpoint.
- **Notifications:** `policies.notify_on_breach` is stored but there is **no destination config** (email / webhook
  / digest) and no wired notifier; the desktop must poll `/v1/logs/breaches`.
- **Anchoring UX:** `anchor now` + `/v1/anchors` + `/v1/anchors/sla` exist, but receipts aren't shown with
  on-chain (Etherscan) links or an SLA countdown, and anchor history is minimal. (Backend now refuses to anchor
  a root unless the stored chain recomputes cleanly — surface that trust signal.)
- **Sharing/proof:** a public trust-badge token exists (`/v1/badge/{token}.svg`), but there is no public
  **verify/status page** a customer's buyer can open to re-verify the chain themselves. The standalone verifier
  also supports a customer-owned HMAC sidecar (`--commitment-key`/`--events`) to check known content offline.
- **Ledger UX:** `/v1/logs` is paginated only — no server-side search / filter / sort (policy / verdict / agent /
  date / risk), no breach-detail drill. Rows now carry richer capture metadata (`event_id`, `client_id`,
  `client_seq`, `event_type`, `chain_version`, `event_metadata`) that isn't surfaced.
- **Analytics:** only `/v1/stats` + `/v1/analytics/threats` — thin; no time-series charts, per-agent/per-policy
  breakdowns, or date ranges.
- **Account audit:** only login history; no trail of the customer's own account actions (key / policy / member /
  settings changes).
- **Settings:** no endpoint to edit org name / contact email / timezone; no per-user profile beyond
  `/v1/auth/me`; no full-account/GDPR export or self-serve erasure (only soft workspace delete).
- **Integrations:** no customer outbound webhooks / event subscriptions; API keys are org-wide with no scopes /
  expiry; no SAML / enterprise SSO (only Google OIDC).

---

## Feature catalog (by area)
Legend: **[FE]** frontend-only (endpoint exists) · **[BE+FE]** needs a new endpoint · effort S/M/L · P0–P3.

### A. Proof & trust (the core value — make "prove it" shine)
- **Anchor transparency** [BE+FE · S · P0] — show each anchor receipt with status, `tx_hash` linked to
  Etherscan, block number, confirmed-at, plus an SLA countdown to the next auto-anchor (data: `chain_anchors`,
  `/v1/anchors`, `/v1/anchors/sla`; "anchor now" already wired). Today receipts aren't surfaced richly.
- **Public verify / status page** [BE+FE · M · P0] — a shareable, no-login page where a customer's buyer
  re-verifies the chain (or sees aggregate verified status) themselves. Extend the public badge token
  (`badge.py`, `organizations.public_badge_token`) into a `/verify/{token}` view backed by a token-scoped verify.
- **Verify UX polish** [FE · S · P0] — visualize the chain, highlight a tampered `seq`, one-click "download the
  independent verifier + my ledger export" (reuse `/v1/logs/export` + the repo's `verifier/`). Verify now also
  reports sequence gaps / deleted rows — show that distinctly from a content mismatch.
- **Passport upgrades** [BE+FE · M · P1] — shareable passport link, scheduled/auto-generated passports, more
  date presets (reuse `passport.py`).

### B. Usage, quota & insight (real charts)
- **Usage & quota dashboard** [BE+FE · M · P0] — interactions / tokens / breaches over time with a quota-headroom
  meter + over-quota warning + upgrade CTA (data: `usage_daily`, `/v1/usage`). Today `/v1/usage` is a thin read.
- **Real analytics charts** [FE · M · P0] — replace thin widgets with time-series: interactions, breach rate,
  token usage, avg seconds-to-verdict, risk-score distribution, top policies (data: `/v1/stats`,
  `/v1/analytics/threats`, `usage_daily`). Inline SVG/Canvas.
- **Per-agent / per-policy breakdown** [BE+FE · S · P1] — group by `agent` / `policy_tag` (columns exist on
  `audit_logs`; add an aggregation endpoint).
- **Date-range filters + export** [BE+FE · S · P1].

### C. Ledger UX
- **Ledger search / filter / sort** [BE+FE · M · P1] — server-side by policy / verdict / agent / date / risk
  (extend `logs.py list_logs`). Today it's pagination only.
- **Breach detail drill** [FE · S · P1] — reason, risk_score, pii_signals, verify status per record
  (`audit_logs.gemini_verdict`, `/v1/verify/hash`).

### D. Team & access management
- **Full member management** [BE+FE · M · P1] — add change-role, re-enable, remove, and resend-invite to the
  existing list/invite/disable (extend `auth_human.py`; audit each). Today only list/invite/disable.
- **MFA self-enrollment** [BE+FE · M · P1] — endpoints for a user to enable/disable their own 2FA (reuse
  `mfa.py`); step-up on sensitive actions. Today `mfa_enabled` is only set outside `/v1`.
- **Per-user profile + workspace settings** [BE+FE · S · P1] — edit display name, org name, contact email,
  timezone (no such endpoints today).
- **Account audit log** [BE+FE · M · P2] — a customer-visible trail of their OWN account actions (key / policy /
  member / IP-allowlist / settings changes). New per-org audit rows (mirror the admin `record_admin_action`
  pattern, org-scoped) + a viewer. Today only login history.

### E. Billing & plan self-serve
- **Billing portal** [BE+FE · M · P1] — a Stripe billing-portal session (cancel / upgrade / downgrade / update
  payment method) — new `POST /v1/billing/portal` (reuse `billing.py` + Stripe). Today only checkout + read
  invoices.
- **Current plan / subscription view** [BE+FE · S · P1] — plan_tier + subscription_status + renewal + quota in
  one place (data present but scattered across `/v1/usage` + `/v1/anchors/sla`).
- **Invoice PDF download** [BE+FE · S · P2] — per-invoice PDF (Stripe hosted URL or generated).

### F. Alerts & notifications
- **Notification config** [BE+FE · M · P2] — wire `notify_on_breach` to a real destination: recipient email(s),
  digest schedule, and an outbound webhook URL (new endpoints + a notifier in the worker; reuse `email.py`).
  Today the pref is stored but nothing sends.
- **In-app breach feed / notification center** [BE+FE · S · P2] — surface recent breaches with unread state
  (`/v1/logs/breaches`).
- **Outbound webhooks / event subscriptions** [BE+FE · L · P3] — let customers subscribe to graded/breach events
  instead of polling (new table + worker delivery + HMAC signing).

### G. Keys & integration
- **Per-key scopes / expiry / last-used** [BE+FE · M · P2] — show `last_used_at`, add optional expiry + scopes
  (columns/extension on `api_keys`; today keys are org-wide, no scopes, rotate collapses to one).
- **SDK setup helper** [FE · S · P2] — copy-paste install + decorator snippets, language examples, and a
  connection / "foxy doctor" status check in the dashboard.

### H. Settings & compliance
- **Full data export + GDPR** [BE+FE · M · P2] — a self-serve full-account export (users, policies, invoices,
  anchors, ledger) beyond the current logs/passport export, and an erasure-request flow (today only soft
  workspace delete via `/v1/account/delete`).
- **Consent / data-handling transparency** [FE · S · P3] — a panel showing what's stored (hashes only, no raw
  payloads) as a compliance reassurance.
- **Enterprise SSO / SAML** [BE+FE · L · P3] — beyond Google OIDC (SAML / SCIM / domain-capture) for larger buyers.

### I. Onboarding & activation
- **First-run checklist** [BE+FE · S · P0] — guided empty state: connect the SDK → run your first call → see it
  verified → invite your team → export a passport (data: `/v1/stats` count, keys, users). Drives activation.
- **Desktop app download** [FE · S · P1] — surface the installer links (`/download/…` on the marketing host) +
  the handoff so customers get the live fox.

### J. Dashboard UX / quality (cross-cutting)
- **`clean_rate` / null-stat guard** [FE · S · P0 · quick-fix] — the backend now truthfully returns
  `clean_rate: null` (and unknown verdicts aren't counted clean); the FE currently renders `"null%"` on a fresh
  workspace. Guard every stat render (`foxy-audit-premium.html` ~L1183/1184/1202 and the analytics tile) so
  `null` shows `—` / "No graded activity yet", not `null%`. **Do this first — it's a live cosmetic bug.**
- **Real charts** [FE · M · P0] — (see §B) the single biggest polish lever.
- **Styled modals + skeleton / empty states** [FE · S · P1] — replace native dialogs; add loading skeletons.
- **Server-side pagination everywhere** [BE+FE · S · P2].
- **Global search / command palette** [FE · M · P3] — jump to a record/section by seq or hash.

---

## Recommended build order
- **P0 — Value & activation (§A, §B, §I, §J charts + null-stat quick-fix):** the `clean_rate` null guard first
  (tiny live bug); then anchor transparency + public verify/status page + verify UX polish; usage/quota dashboard
  + real charts; first-run checklist. *First because it makes the product's core "prove it" promise visible and
  drives activation — mostly assembly of existing data.*
- **P1 — Account maturity, paid table stakes (§D, §C, §E, §A passport, §I download):** full member management +
  MFA self-enrollment + profile/workspace settings; ledger search/filter + breach drill; billing portal +
  current-plan view; passport share; desktop download.
- **P2 — Alerts, keys, compliance (§F, §G, §D audit, §H export, §E invoice PDF, §J modals/pagination):**
  notification config + breach feed; per-key scopes/last-used + SDK helper; account audit log; full data
  export/GDPR; invoice PDF; styled modals + pagination.
- **P3 — Integrations & enterprise (§F webhooks, §H SSO, §J palette, §H consent):** outbound webhooks /
  event subscriptions; enterprise SSO/SAML; command palette; consent transparency.

Each phase is a self-contained set of new/extended `/v1` endpoints + dashboard sections, deployed via push→CD,
tested per the pattern below. Phases are independent — reorder or cherry-pick.

---

## Where to work (critical files)
- **Backend:** extend the existing customer routers in `backend/app/routers/` — `anchors.py`, `verify.py`,
  `passport.py`, `logs.py`, `analytics.py`, `account.py`, `auth_human.py`, `keys.py`, `policies.py`, `billing.py`,
  `badge.py` — and add small new ones where a feature is distinct (e.g. `notifications.py`, `webhooks.py`,
  `org_audit.py`), registering any new router in the customer-router loop in `main.py`. Reuse the guards
  (`require_user` / `require_role` / `resolve_org`), `mfa.py`, `email.py`, and the `usage_daily` rollups.
- **Frontend:** extend the single file `foxy-dashboard/foxy-audit-premium.html` — new dock sections + panels
  using the existing `api()` / `go()` / `.clay` / theme / `revealSecret` patterns; charts as inline SVG/Canvas.
- **DB / migrations:** mostly reads of existing tables (`backend/app/models.py`). New columns/tables only where a
  phase needs them — e.g. a per-org `account_actions` audit table, `api_keys` expiry/scope columns, a
  `webhook_subscriptions` table, notification-destination columns on `org_policies`. One Alembic migration per
  such phase, numbered after the latest (the current head is **0035** — number new ones 0036+).
- **Do NOT touch:** the staff `/admin/v1/*` app (`admin_*` routers, `foxy-adminpage/`) or the marketing site
  (`foxy-sale-page/`). Keep the diff inside the customer dashboard + its `/v1` API. (The admin console is a
  separate spec/branch.)

## Testing (per feature / phase)
Backend TDD for each new/changed `/v1` endpoint: (1) **tenant isolation** — a scoped read/action only ever
touches the caller's org (org-A can't see org-B); (2) **role-gating** — admin-only mutations 403 for members /
bare keys; (3) the **effect** is correct; (4) **secrets** are never returned in cleartext except the shown-once
create/regen path. Keep the full suite green (`pytest backend/tests/integration -q`, baseline ~258). Mirror
existing tests: `tests/integration/test_isolation.py`, `test_rls.py`, `test_keys_regenerate.py`,
`test_onboarding.py`, `test_evidence_capture.py`. Manual smoke against the local stack — e.g. P0: ingest a few
interactions via a Bearer key → the usage/quota chart moves; "anchor now" → the receipt shows a real `tx_hash`
linking to Etherscan; open the public verify link in a private window and confirm it verifies with no login.

## Branch & merge workflow
- Build on a dedicated feature branch, **one phase per branch/PR** (e.g. `feat/dashboard-p0-proof-and-usage`),
  so each phase is reviewable and independently mergeable.
- Keep the diff confined to the customer dashboard + `/v1` API (see "Where to work"). Add tests with every
  endpoint and keep the full backend suite green before opening the PR.
- Open the PR against `main`. On merge, CI + CD redeploy the stack; hard-refresh `app.foxyaudit.tech/dashboard`.
- Each phase mostly adds new sections/endpoints (or extends one router) rather than rewriting existing flows,
  so phases can be built and merged in any order, by different people, with minimal conflicts.
