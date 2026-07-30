# Foxy Audit — Codebase Deep-Dive

> Read from source on branch `main` (`5e428f6`, 2026-07-04). Every claim below was
> read from code, not documentation. Where the checked-in docs disagree (branch
> topology in `CLAUDE.md`, "all 14 themes apply", `IMPLEMENTATION_STATUS.md`
> calling anchoring "deferred"), **the code wins**.

**One-line truth:** Foxy Audit is a **tamper-evident AI-governance platform** wearing a
pixel-fox. Customers wrap their LLM calls with an SDK that hashes each
prompt/response — raw text never leaves the process — and streams the hashes to a
backend that (1) links them into a per-org cryptographic **hash chain**, (2) grades
each interaction with **Gemini** against the org's policy, and (3) can **anchor the
chain head on a public blockchain** so anyone can prove the log wasn't altered.
Everything else — the desktop fox, three web properties, the admin console — is a
surface over that spine.

**One brand, two products:** the sales page markets a consumer *desktop pet*; the
dashboard + admin console are a B2B *compliance SaaS*. Internally the desktop code
still uses the legacy `omni_fox` / `OmniAwareFox` naming; the backend is clean.

**Status legend:** ✅ real / works end-to-end · 🟡 implemented but off / partial /
mock · 🔴 broken, unwired, or missing · ℹ️ caveat / drift.

---

## Table of contents

1. [What it is](#1-what-it-is)
2. [Development phases](#2-development-phases)
3. [The data spine](#3-the-data-spine)
4. [Reality-check matrix](#4-reality-check-matrix)
5. [Desktop client](#5-desktop-client)
6. [SDK](#6-sdk)
7. [Backend](#7-backend)
8. [Data model](#8-data-model)
9. [Web properties](#9-web-properties)
10. [Deploy topology](#10-deploy-topology)
11. [Tests & CI](#11-tests--ci)
12. [Bug / gap / drift register](#12-bug--gap--drift-register)

---

## 1. What it is

Three tiers over one backend:

| Tier | Component | Role |
|---|---|---|
| Desktop | `omni_fox.py` + friends | PyQt6 mascot: telemetry-reactive state machine, AI chat copilot, native "Auditor Console" dashboard. |
| SDK | `sdk/` (`foxy-audit` pip pkg) | A single `@audit` decorator. Hashes prompt+response locally, UDP-pings the fox, batches metadata to the backend. Never raises into the host app. |
| Backend | `backend/` (FastAPI + Postgres) | Two sibling ASGI apps — customer API at `/`, staff admin API at `/admin` — hash chain, Gemini judge, durable worker, blockchain anchoring. |

Plus three static single-file web apps (marketing, customer dashboard, admin
console), a Solidity anchoring contract (`contracts/AnchorRegistry.sol`), and a
production deploy (one backend image, three hostnames).

---

## 2. Development phases

Four completed phases, reconstructed from git history and in-code `Phase N` markers.
The current `main` == `phase3-pilot-ready` branch is **Phase 4 complete, pilot-ready**.

| Phase | When | Branch(es) | What shipped |
|---|---|---|---|
| **1 · Desktop companion** | Jun 24–25 | `foxy-f`, `foxy-a` | The PyQt6 fox: state machine, spritesheet, chat copilot, overlays, settings/themes, system tray, SDK bridge, security overlays, org key, and the "Compliance Command Center" → "Auditor Console" dashboard. |
| **2 · Walking skeleton** | Jun 26 – Jul 2 | `feat/phase2-backend-and-dashboard`, `foxy-skeleton` | The three-tier skeleton: `foxy-audit` SDK + FastAPI backend + the hash chain, background Gemini grading worker, Stripe billing webhook, Compliance Passport (HTML), durable grading queue, dashboard read API, human auth + RBAC, and the live web dashboard. |
| **3 · Pilot hardening** | Jul 3 | `phase3-pilot-ready` | The "trust upgrade": public-chain **anchoring** (A1), **HMAC multi-key API keys** + user provisioning (A2+B), **Docker deploy** + secure-defaults fail-fast + readiness probe (Track C), and a **Postgres-backed integration suite + CI gate** (Track D). |
| **4 · Platform / go-to-market** | Jul 3–4 | `main` | The **admin site** ("site 3") + staff accounts + admin audit trail, **traffic tracking** (partitioned) + org platform metadata (#1), **customer billing + usage reads** (#2), the three-site split, and CI/CD security scanners + a VM deployment pipeline for `foxyaudit.tech`. |
| **5 · Production hardening** | *in progress* | — | 🚧 The road from pilot-ready to production — correctness fixes, security/tenancy hardening, real integrations, missing SaaS features, ops/observability, dependency pinning, cleanup, test gaps, AI/PII detection depth, dashboard access controls, and desktop-UX polish. Detailed below. |
| **6 · Differentiators & moat** | *vision* | — | 🔭 Forward product bets from competitor analysis — agent-level attribution, open-source verifier, embeddable re-runnable trust badge, self-serve onboarding, priced anchor-SLA, ZKP report. Not scheduled; captured below. |

### Phase 1 — Desktop companion
The animated fox as a standalone desktop pet. `OmniAwareFox` state machine +
spritesheet animation, the chat copilot (`clay_chat_popup.py`, `ai_providers.py`),
cursor-following eyes and security-glow overlays, the 14-theme token system
(`fox_settings.py`), settings dialog, system tray, the local **SDK UDP bridge**
(`sdk_bridge.py`, port `9999`), and the compliance dashboard (`dashboard.py`, later
redesigned into the "Auditor Console"). This is the surface the customer sees; it
has been carried forward largely unchanged through every later phase.

### Phase 2 — Walking skeleton (backend + SDK)
The "walking skeleton" commit adds the `foxy-audit` SDK, the FastAPI backend, and a
demo. This phase establishes the **spine**: the SHA-256 hash chain (`chain.py`), the
ingest route (`/v1/logs/batch`), the **background Gemini grading worker**, the
**Stripe billing webhook**, the **Compliance Passport** generator, plus the durable
grading queue, the dashboard read endpoints (`/v1/logs`, `/v1/stats`, `/v1/verify`),
**human auth + RBAC** (cookie sessions, admin/member roles), and the live web
dashboard (`foxy-audit-premium.html`). Reconciled onto `foxy-skeleton` as the
"Phase 1+2" merge.

### Phase 3 — Pilot hardening
Four tracks that turn the skeleton into something an auditor can trust:
- **A1 — Public-chain anchoring.** `anchor.py` + `AnchorRegistry.sol` publish the
  org's chain head to a public chain (stub / EVM-Sepolia / OpenTimestamps providers).
  `verify_anchor.py` proves the anchored root matches a fresh recompute *and* the
  on-chain calldata. Guarded by offline EVM encoding tests.
- **A2 + B — HMAC multi-key API keys + provisioning.** `api_keys` table with
  `HMAC-SHA256(pepper, key)` hashes (legacy plain-SHA256 kept as a fallback), key
  create/rotate/revoke, and dashboard wiring.
- **Track C — Docker deploy + secure defaults.** `docker-compose.yml`, the
  `FOXY_ENV=prod` fail-fast (refuses weak/missing/equal secrets), and the
  `/health/ready` readiness probe.
- **Track D — Test suite + CI gate.** The Postgres-backed integration suite and the
  GitHub Actions gate.

> ℹ️ **Drift caught here:** `IMPLEMENTATION_STATUS.md` still lists anchoring and
> HMAC keys as "Open / Deferred (Phase 4)". Git shows both **shipped in Phase 3**.
> That stale doc is exactly why the source is the source of truth.

### Phase 4 — Platform / go-to-market
Commercialization and multi-tenant operations:
- **#1 — Admin site ("site 3").** `staff_users` + `admin_actions` (append-only audit
  trail), the platform-role ladder (viewer < operator < superadmin), org platform
  metadata (soft-delete, suspend, quota), partitioned `traffic_events` capture, and
  the three-site split (`foxy-sale-page` / `foxy-dashboard` / `foxy-adminpage`).
- **#2 — Customer billing + usage.** `invoices` + `usage_daily` rollups behind
  `/v1/invoices` and `/v1/usage`.
- **Deploy pipeline.** CI security scanners (GitLeaks, Trivy/SBOM), the VM deployment
  for `foxyaudit.tech`, brand favicons, and the shared-VM host-nginx variant.

**Explicitly deferred beyond Phase 4** (per the code's own comments): AI-native
temporal reasoning in the judge, broader PII detection (beyond email/SSN), and
SSO/SAML — scoped out as not-blockers for a design-partner pilot.

### Phase 5 — Production hardening (planned · not started)

Where `main` is today is **pilot-ready, not production-ready**. Phase 5 is the remaining
work to close that gap, grouped into tracks and tagged by severity (🔴 blocker · 🟡 high ·
⚪ low). Every item traces to a concrete location in the code — this is the §12 register,
the operational/security/hygiene sweep, and issues raised by the team, reframed as a
forward roadmap. Two standing decisions: **zero-knowledge-proof work is out of scope for
now**, and **rotating the exposed secrets is deferred to the very end of the project** (a
team call — it's still required before a real launch, just not yet).

**5A · Correctness & core-flow fixes** — 🔴 breaks a promise
- **Wire `policy_breach` end-to-end.** The fox's breach reaction (`omni_fox.py:603`) and
  the dashboard's `on_policy_breach` listen for a UDP event **nothing emits** — the SDK
  only sends `hash_ok` (`client.py:129`) and grading is async/remote. Have the worker (or
  a local relay) emit the UDP `policy_breach` when a graded row comes back a breach.
- **Fix Stripe signup provisioning.** `_handle_checkout` (`routers/billing.py:131`)
  provisions an org but creates no peppered `ApiKey` row and no admin `User`, and returns
  the plaintext key only to Stripe — a new customer can neither authenticate an SDK nor log
  in. Create both and deliver the key.
- **`/v1/analytics/threats` (`routers/analytics.py`).** Aggregate in SQL + paginate
  (currently loads the whole org ledger into memory); fix the malformed timestamp
  `isoformat() + "Z"` (line 44); switch to `resolve_org` so the cookie web dashboard can
  call it (Bearer-only today).
- **Drop the duplicate `stripe_customer_id` unique constraint** (inline in `0001` + named
  `uq_org_stripe_customer` in `0002`).
- **`traffic_events.direction`** is only ever written `'in'` — implement outbound capture
  or drop the column + the "in/out" framing.

**5B · Security & tenancy hardening** — 🔴 critical
- ⏸ **Rotate the exposed secrets** (EVM private key, session secrets, API-key pepper,
  Stripe + Gemini keys in `backend/.env` / `deploy/.env`). **Deferred to the end of the
  project by team decision (2026-07-04)** — still required before a real launch; parked
  here so it isn't forgotten. (They were exposed in chat; the risk stands until rotated.)
- **Enforce RLS with a confined DB role.** Prod and the test suite both connect as the
  `foxy` **superuser**, which bypasses `FORCE ROW LEVEL SECURITY` — so the four RLS
  policies are decorative and tenant isolation rests entirely on app-code `WHERE org_id`.
  ⚠️ **Load-bearing caveat:** staff cross-org visibility (the admin site) *depends* on this
  superuser bypass. A confined role would **silently break** staff reads unless the staff
  path gets an explicit cross-org mechanism — so hardening the role is a coordinated
  re-audit of every staff query, not a one-line change.
- **Rate-limit customer login** (`auth_human.py`) — only staff login is limited today.
- **Rotate the customer session on login** (`auth_human.py:92`) — staff login clears the
  session first (fixation guard); customer login doesn't.
- **Admin MFA/TOTP** for staff login (`auth_staff.py`); make the IP allowlist enforced in
  prod + add a trusted-proxy list (spoofable `X-Forwarded-For` today, `admin_guard.py`).
- **Security headers** — no HSTS / CSP / X-Frame-Options (`main.py`). **Request-size cap**
  — `/v1/logs/batch` accepts an unbounded batch. **Log/payload hygiene** — only two anchor
  secrets are redacted (`anchor.py:60`); raw Stripe payloads are stored in
  `stripe_events.payload`.

**5C · Turn on the real integrations** — 🟡 the value prop is off
- **Enable anchoring** — deploy `AnchorRegistry.sol`, set the EVM vars, flip
  `ANCHOR_ENABLED=true` (real web3 code, off by default) and monitor it.
- **OpenTimestamps** provider returns `pending` forever (`anchor.py:177`) — implement the
  calendar submission or remove the provider.
- **Sales page** — wire real payment + a real download, or honestly remove the fake Stripe
  checkout and fix the placeholder links + the `support@foxychat.ai` domain.

**5D · Missing product features** — 🔴 table-stakes for multi-tenant SaaS
- **Password reset** — none; a lost password is a permanent lockout.
- **Transactional email** — temp passwords + invites are generated but never sent (an
  admin must share them manually).
- **Workspace/org deletion** — the dashboard's "delete workspace" button
  (`foxy-audit-premium.html:897`) has no backend endpoint.
- **Customer data export** — no endpoint to retrieve one's own audit logs.
- **`admin_actions` retention** — grows unbounded (only `traffic_events` has a 90-day
  partition drop).

**5E · Reliability & ops** — 🟡 high
- **Dead-letter alerting** — `grading_status='failed'` shows up in `/health/ready` but
  nothing alerts when it grows; a runaway Gemini failure fills the queue silently.
- **Worker resilience** — add a circuit-breaker / backoff on repeated Gemini failures
  (`worker.py`) and a graceful-shutdown drain so a deploy doesn't orphan `in_progress`
  rows.
- **Deploy safety** — `deploy.yml` runs SSH/compose but has no post-deploy smoke test and
  no rollback. *(Schema migrations DO run on deploy via the `foxy-migrate` service — only
  the app lifespan is advisory-only, so that part is already fine.)*
- **Observability** — no metrics (Prometheus/OTel), no error tracking (Sentry), no request
  correlation IDs.
- **Backups / DR** — no Postgres backup strategy in the repo.
- **Anchoring at scale** — real EVM anchoring means real gas cost + confirmation latency.
  Monitor anchor-tx cost, confirmation time, and failures (the worker already persists
  `failed` anchor rows — surface + alert on them), plus a wallet-balance check so anchoring
  doesn't silently stop when the key runs out of test-ETH.

**5F · Dependency & build hygiene** — 🟡 high
- All deps use `>=` with no lock files (`backend/requirements.txt`, `sdk/pyproject.toml`,
  `demo/requirements.txt`) → non-deterministic builds. Pin + lock. The desktop client has
  **no `requirements.txt`** at all (PyQt6 / psutil / pynput / requests are implicit).

**5G · Cleanup & drift** — ⚪ low
- Delete dead code: SDK `transport.py`, `window_tracker.ActiveWindowTracker`, and the SDK's
  vestigial client-side `chain_hash`/`timestamp` (dropped by the backend).
- **Theme system decision** — ship a picker or remove the 14 unreachable themes.
- **Model-label drift** — code default `gemini-1.5-pro`, prod `gemini-2.5-flash`, UI says
  "Gemini 1.5 Pro"; pick one source of truth.
- Fix the dev single-file HTML mount footgun (`backend/docker-compose.yml`) and the
  Makefile's stale `acme_test_key`.
- Retire the stale root docs (`IMPLEMENTATION_STATUS.md` et al.) that contradict the code.

**5H · Test-coverage gaps** — 🟡 medium
- No tests for: RLS enforcement (needs a confined role), rate-limit / lockout, live-chain
  anchoring, the real-Gemini path, the async worker loop, or Stripe signature
  verification. The suite runs as superuser (RLS bypassed) and resets the limiters before
  every test.

**5I · Business & legal (non-engineering)** — ⚪ paperwork, not code
- SOC 2, SSO/SAML, and a signed BAA (HIPAA). Business/legal steps we'll need for enterprise
  eventually — not engineering gaps; parked so they're tracked.

**5J · AI judge & PII detection depth** — 🟡 shallower than it looks *(team-raised)*
- **Real temporal reasoning in the judge.** Gemini today mostly runs our hardcoded if/else
  thresholds dressed as "AI judgment" (`gemini.py` builds a threshold-driven prompt and
  grades one event at a time). Upgrade it to reason over a *sequence* of an org's events
  (replay / exfil patterns over time), not a single interaction in isolation.
- **Replace the DIY PII regex with Presidio.** `_detect_pii` (`sdk/client.py:33`) only
  catches email + SSN. Adopt Microsoft **Presidio** (open-source) for phone numbers, credit
  cards, addresses, names, MRNs, etc. — stop hand-rolling regex.

**5K · Dashboard access & audit** — 🟡 tenant-facing security *(team-raised)*
- **Per-company IP allowlist for the customer dashboard** — reuse the admin
  `AdminIPAllowlistMiddleware` pattern (`middleware/admin_guard.py`), scoped per-org, so a
  tenant can lock their dashboard to their office/VPN ranges.
- **`noindex` on `/dashboard`** — the admin console ships `<meta name=robots
  content="noindex,nofollow">`; the customer dashboard (`foxy-audit-premium.html`) doesn't.
- **Login history / audit trail** so an org admin can see who logged in and when (a new
  per-user login-event table + a dashboard view; distinct from the staff `admin_actions`).

**5L · Desktop UX (the pet) — nice-to-haves** — ⚪ polish *(team-raised)*
- A real **popup notification** when something's flagged (not just the fox's colour change).
- A weekly **"here's your summary"** popup so people don't forget to check.
- A subtle **sound cue** on breach detection.

> ℹ️ **Flagged as missing but already in code (verify, don't rebuild):** a *customer*
> usage/quota endpoint already exists — `GET /v1/usage` (`routers/account.py`, Phase 4 #2)
> returns per-day usage + `monthly_log_quota` / used / remaining, and the dashboard's
> Billing view calls it. "Only admin has usage" is stale — just confirm it's surfaced.

> ℹ️ **ZKP note (team decision):** zero-knowledge-proof *engineering* stays out of scope for
> now — but the ZKP-proof-as-a-report *differentiator* is captured below in Phase 6.

### Phase 6 — Differentiators & moat (product vision)

Forward product bets the team surfaced from competitor analysis (vs Modulos, TrueFoundry,
Credo AI, OneTrust) — **not** production-readiness fixes (that's Phase 5), but the moat that
makes Foxy Audit distinct. Captured so they're tracked; **not scheduled** for the current
hardening pass. Severity here = strategic value / effort, not blocker-ness.

- **6A · Agent-level attribution in the chain** — 🟡 net-new, high-value. Today a chain row
  is just "this org had an interaction." Tag each row with `agent_id` / `step_id` (thread it
  through the SDK payload → `audit_logs` schema → **fold into the chain hash** → surface in
  the dashboard + passport) so a compliance officer can reconstruct an entire autonomous
  agent's reasoning trail — which agent, which tool call, which step produced each row, not
  just "an AI did something at 3pm." Maps to emerging agentic-AI regulation. *(Accuracy check:
  there is **no** `agent_id`/`step_id` in the current schema or SDK — this is net-new, not
  "formalize an existing field.")*
- **6B · Public open-source verifier** — ⚪ cheapest, highest-credibility. Publish just the
  verification logic (`scripts/verify_chain.py` + `verify_anchor.py` already exist) as a
  standalone open-source CLI/library anyone can run against your public anchor receipts —
  zero trust in Foxy's servers. "Don't trust us, run it yourself." Mostly packaging what's
  already written.
- **6C · Embeddable, re-runnable trust badge** — 🟡 distribution mechanic. A public
  `verified.foxyaudit.tech/badge/{org_id}` any visitor can click to **independently re-run**
  verification (not a static image). Needs a public, org-scoped, data-safe verify endpoint
  (chain-intact + block count + last-anchor tx, never ledger contents). Turns the product
  into part of the customer's own sales pitch — the competitor set keeps verification behind
  a login.
- **6D · Self-serve, no-salesperson onboarding (startup tier)** — 🟡 GTM. `pip install` → pay
  by card → integrated in an afternoon, no human in the loop, while every named competitor
  sells enterprise-first through demos. **Unlocked by 5A.2** (fix Stripe signup provisioning)
  **+ 5C** (real payment/download) — the differentiator is finishing those into one clean
  self-serve funnel.
- **6E · Anchor-frequency as a priced SLA** — ⚪ productize a knob. `anchor_interval_seconds`
  is a hidden global today (`config.py`). Make it a **per-org, plan-tiered dial** (hourly for
  premium, daily for free) — a visible feature a buyer understands and pays more for, not an
  implementation detail.
- **6F · ZKP per-org compliance proofs, sold as a report** — ⚪ future bet. Per-org
  zero-knowledge compliance proofs packaged as a deliverable *report*, not just a dashboard
  view. Captured as a differentiator; the ZKP engineering itself stays deferred per the note
  above.

---

## 3. The data spine

The load-bearing path — one audited call, end to end. Everything else observes or
renders it.

1. **Customer decorates a function** ✅ — `@foxy.audit(policy="hipaa_basic")` wraps
   any sync or async LLM-calling function (`sdk/client.py`). Runs the wrapped
   function **first**; never masks the host's exceptions.
2. **SDK captures & hashes** ✅ — `sha256(prompt)`, `sha256(response)`, a token
   estimate, local PII regex signals. Raw text goes out of scope.
3. **Two fire-and-forget transports** ✅ — (a) instant **UDP `hash_ok`** →
   `127.0.0.1:9999` for the fox; (b) **batched HTTPS** (10 items or 1s) →
   `POST /v1/logs/batch` with a Bearer key, only when a key is configured.
4. **Backend writes the chain synchronously** ✅ — `routers/logs.py` locks the org's
   tail row `SELECT … FOR UPDATE` (batches can't fork), assigns `seq`, computes the
   durable chain hash, writes rows `grading_status='pending'`, **commits, returns
   202**. That commit is the crash-safe enqueue.
5. **Worker grades asynchronously** ✅ — `worker.py` is a Postgres-outbox poller:
   claims pending rows with `FOR UPDATE SKIP LOCKED`, calls `gemini.evaluate()` with
   **only hashes + metadata, never text**, writes the verdict, marks `graded`.
   Fail-open by default; 5 retries then dead-letter `failed`.
6. **Anchoring commits the head** 🟡 — a separate worker thread (off by default)
   publishes the org's **chain head only** (a single `bytes32`, not a Merkle root).
7. **Anyone verifies** ✅ — `GET /v1/verify` and `scripts/verify_chain.py` recompute
   from genesis; `verify_anchor.py` also reads the on-chain tx back and checks
   `calldata == selector + root`. Editing any historical field breaks that row and
   every row after it (avalanche).

**The one frozen formula** (writer and verifier import the same function):

```python
# backend/app/chain.py — GENESIS_HASH = "0"*64
data_blob = f"{org_id}|{prompt_hash}|{response_hash}|{token_count}|{policy_tag}|{seq}"
chain_hash = sha256((data_blob + prev_hash).encode()).hexdigest()

# golden vector (test_chain.py:54) — org-1, "a"*64, "b"*64, 100, hipaa_basic, seq 1
872eb2c206bcb995773ab1b9a43a031c6d8488761c976ce3d341921a81aa2f79
```

> 🔴 **The breach alert is wired but never fires.** The fox's most demo-critical
> behavior — a policy breach turning it **red and auto-opening the chat** with the
> reason + risk score (`omni_fox.py:603`) — listens for a `policy_breach` UDP event
> (`sdk_bridge.py:84`). Grepping the whole repo: **nothing emits it.** The SDK only
> sends `hash_ok` (`client.py:129`); grading is async on the backend, which sends no
> UDP at all and in prod isn't even on the same host as the fox's loopback port. The
> protocol is fully specified on both ends and unwired in the middle. Today the
> breach reaction only fires from the manual "🚨 Alert" emote.

> ℹ️ **The SDK's own hash chain is vestigial.** The SDK computes a client-side
> `chain_hash` + `timestamp` and puts them in the payload — but the backend's
> `LogIngest` schema only accepts `prompt_hash / response_hash / token_count /
> policy_tag / pii_signals`. Pydantic drops the extras and the backend recomputes its
> own chain from scratch. The SDK's `self.last_hash` ledger is dead weight.

---

## 4. Reality-check matrix

The whole system graded against the source.

| Capability | Status | Ground truth |
|---|---|---|
| Hash chain + tamper verify | ✅ | One frozen formula; `/v1/verify` + scripts recompute from genesis; golden-vector & tamper-cascade tests. |
| Durable grading (outbox worker) | ✅ | `FOR UPDATE SKIP LOCKED` poller, retries → dead-letter, heartbeat → readiness probe. |
| Gemini judge | ✅* | Real API call, policy-aware prompt, hashes-only input. *Inert without `GEMINI_API_KEY`; fail-open default. |
| Multi-tenant auth (keys/session/staff) | ✅ | HMAC-peppered keys (+legacy fallback), bcrypt sessions, separate staff channel. Well tested. |
| Postgres RLS on 4 ledger tables | 🔴 decorative | Policies exist, but prod + tests both run as the `foxy` **superuser** which bypasses FORCE RLS. App-code `WHERE org_id` is the real wall. RLS never exercised. |
| Customer web dashboard | ✅ | Genuine SPA over the API, same-origin session cookie, no keys in browser. |
| Admin ops console | ✅ | Full staff CRUD over `/admin/v1/*`, role-gated, audited. |
| Stripe webhook ingest | ✅ | Signature-verified, idempotent + durable via `stripe_events`. |
| Traffic + usage rollups | ✅ | Off-hot-path capture, partitioned; `usage_daily` keeps dashboards off the raw ledger. |
| EVM / Sepolia anchoring | 🟡 off | Real web3 code + offline encoding tests, but `ANCHOR_ENABLED=false` and provider defaults to `stub` (fake tx). |
| OpenTimestamps (Bitcoin) anchor | 🔴 unimpl | Returns `pending`; the actual OTS submission is a TODO. |
| Sales-page checkout | 🔴 fake | `setTimeout` theater; "Powered by Stripe · SSL" is false; card never sent; "download" opens bare github.com. |
| Signup → usable API key | 🔴 broken | Checkout provisions an org but no `ApiKey` row and no login `User`; plaintext key returned only to Stripe. |
| Fox breach reaction (UDP `policy_breach`) | 🔴 unwired | Listener + handler exist; nothing emits the event (see §3). |
| `dashboard.py` ledger figures | 🟡 synthesized | Block height / chain root / compliance score computed *locally* from the UDP stream; only 4 explicit workers show real backend data. |
| 14 UI themes | 🟡 unreachable | Fully defined tokens, but chat/settings/dashboard hard-pin fixed skins; no shipping control calls `set_theme()`. |
| SDK `transport.py` | 🔴 dead | Imported by dispatch but never called; targets the wrong (singular) endpoint. |
| `ActiveWindowTracker` UDP broadcaster | 🔴 unwired | Defined in `window_tracker.py` (port 5005) but never instantiated. |

---

## 5. Desktop client

~6.5k lines of PyQt6. `OmniAwareFox` is a frameless, always-on-top, translucent
`Tool` window fixed to a 192×208 sprite cell — it owns the state machine, timers,
threads, tray, and every child overlay.

**State machine & animation.** One atlas (`ultimate_fox_spritesheet.png`), 12 actions
× 24 frames (8 cols, 3 rows/action). `self.state` + `current_row/frame`;
`_set_state(name,row,duration)` is the single transition point, gated by
`reaction_cooldown`. `_TIMED_STATES` auto-expire to IDLE. IDLE has breathing, dozing
after 120s, autonomous idle-break poses (~60–120s), and compliance-tip bubbles. Two
`QTimer`s: animation 100ms, roaming 150ms.

**Three background QThreads → Qt signals:**

| Thread | Feeds | Behavior |
|---|---|---|
| `GlobalSensors` | typing / scrolling / hardware | pynput keyboard+mouse + psutil CPU/RAM/battery every 3s. CPU>90 or RAM>92 → CRYING; battery<15 & unplugged → ALERTING. |
| `SDKBridgeListener` | hash_confirmed / policy_breach | UDP `127.0.0.1:9999`, drops malformed. The live wire from the SDK. |
| `StartupHealthWorker` | succeeded / failed | One-shot `GET /v1/health` with `Bearer {org_key}` (only if both set). Drives connected(green)/unreachable(amber). |

**Interaction:** click (<6px) → chat; drag (>30px) → `_user_placed` stops roaming;
patting → LOVING. Right-click menu: Dashboard / Compliance Status / PC Health / Chat
/ Emotes / tray / roaming / Settings / Quit.

> ℹ️ **Two QSettings namespaces.** `__main__` sets the Qt app org/app to
> `FoxyAudit`/`Foxy Audit` (cosmetic), but `FoxSettings` constructs
> `QSettings("OmniAwareFox","DesktopPet")` — where settings actually persist.

**Chat copilot** (`clay_chat_popup.py`). Frameless resizable *Window* with painted
vector icons. `send_message` first tries the local `open <path>` command and
short-circuits the AI; otherwise runs `_AICallWorker` off-thread. `ai_providers.call_ai()`
dispatches by provider — **anthropic** (`x-api-key`, `anthropic-version: 2023-06-01`,
`max_tokens=250`), **openai/lmstudio/custom** (`/v1/chat/completions`), **ollama**
(`/api/chat`). Every provider raises on failure; the UI catches and shows a canned
line. History capped at 20 turns, persisted to `~/.foxy_audit/chat_history.json`. The
copilot talks to Anthropic/OpenAI/Ollama **directly — not the Foxy backend**.

**Settings & themes.** `fox_settings.py` defines 14 fully-populated theme token dicts,
**but no shipping UI exposes a theme picker** — settings/chat/dashboard hard-pin fixed
skins and ignore the tokens. The dialog *does* drive AI provider/key/model/URL (with a
live "Test Connection"), behavior sliders, and the org key + backend URL.

**Auditor Console** (`dashboard.py`, 2021 lines). Five pages. Four genuine `QThread`
HTTP workers — `/v1/verify`, `/v1/logs`, `/v1/analytics/threats`, `/v1/logs/{seq}` —
all no-op without backend URL + key.

> 🟡 **The headline numbers are locally synthesized.** CPU/RAM/battery are real, but
> block height, chain root, and the compliance "score" are computed client-side from
> the live UDP stream: `_next_hash()` = `sha256(last_hash|policy|block_height|time())`,
> score decays on breach and self-heals +0.1/sec. Overview strings like
> `org="acme-health-ai"` are hardcoded. The docstring is candid about it.

**Overlays & tooling:** `eye_tracker` (cursor-following pupils), `security_overlay`
(green/red/amber glow), `window_tracker` (cross-platform active window + `open_path`;
holds the unwired `ActiveWindowTracker`), `build_16frame_atlas` (offline Pillow tool
that hardcodes another dev's path and falls back to re-expanding the atlas in place).

---

## 6. SDK

7 modules. A **decorator-only** public surface:

```python
from foxy_audit import FoxyClient
foxy = FoxyClient(api_key="foxy_sk_…")   # or $FOXY_API_KEY

@foxy.audit(policy="hipaa_basic")
def ask_model(prompt): ...               # works on sync AND async fns
```

| Module | What it does |
|---|---|
| `client.py` | Validates policy tag (`^[a-z0-9_]{1,32}$` → `default`), extracts the prompt, builds the payload, dispatches. Wrapped so telemetry **can never raise into the host**. |
| `config.py` | `resolve()`: kwarg → env → default. `enabled = bool(api_key)` — no key ⇒ HTTP off, UDP still on. Default endpoint `http://127.0.0.1:8000`. **No `org_key` concept.** |
| `dispatch.py` | One daemon thread drains an **unbounded** queue, batches 10-or-1s, POSTs the JSON list to `/v1/logs/batch`. Failures swallowed at debug; `atexit` flush ≤2s. |
| `hashing.py` | `sha256_hex` + `estimate_tokens` = `max(len//4, wordcount)` — explicit placeholder. |
| `udp.py` | Generic `send_ping`; has `reason`-trimming for oversized `policy_breach` packets the SDK never constructs. |
| `transport.py` | 🔴 **dead** — targets `/v1/logs` (singular); `dispatch.py` never calls it. |

**Policy breaches are decided backend-side, not by the SDK.** Every audited call
emits `hash_ok`; the backend grades async and returns `202 {"status":"pending"}` at
ingest. The one bit of local inspection is `_detect_pii` (email + SSN regex) — a
signal forwarded to the backend, not a local decision.

---

## 7. Backend

FastAPI over sync SQLAlchemy 2.0 / psycopg3 / Postgres. The root app is a **bare
parent that mounts two sibling sub-apps** and carries no middleware of its own (so
the two `SessionMiddleware`s never collide).

**The two-app split:**

- **`customer_api` (mounted `/`)** — SDK ingest + dashboard. Cookie `session`,
  customer CORS. Serves `/dashboard` same-origin.
- **`admin_api` (mounted `/admin`)** — internal staff console. Distinct cookie
  `foxy_staff_session` (separate secret, `SameSite=strict`, `Path=/admin`), separate
  CORS, and an **IP-allowlist guard outermost**.

**Auth & multi-tenancy:**
- `require_org` (SDK Bearer): (1) HMAC-with-pepper match against active `api_keys`,
  else (2) legacy plain-SHA256 fallback. Sets the `app.current_org` RLS GUC.
- `require_user` (session) + `require_role("admin")`; `resolve_org` accepts either.
- `require_staff` deliberately does *not* scope RLS — staff read cross-org. Ladder:
  `viewer < operator < superadmin`.
- 🔴 Two-layer isolation on paper (RLS + app-code `WHERE org_id`), but today the whole
  thing leans on the app-code filter because the `foxy` superuser bypasses RLS.

**The core engines:**

| Module | What it does |
|---|---|
| `chain.py` | The single frozen formula (§3). |
| `worker.py` | Outbox poller; anchoring + usage rollups run in their **own** threads so a slow chain RPC can't starve grading or the heartbeat. |
| `anchor.py` | Anchors the chain head (`bytes32`). Providers: `stub` (default fake), `evm` (web3 → Sepolia, EIP-1559 gas, v6/v7-safe), `opentimestamps` (unimplemented). Failures persist as visible `failed` rows; RPC/key redacted from errors. |
| `gemini.py` | Hashes-only input, anti-injection + policy-aware prompt, `temperature=0`, strict JSON. **Never raises** — fail-open default. Code default model `gemini-1.5-pro`; prod passes `gemini-2.5-flash`. |
| `billing.py` | Webhook-only. Idempotent via `stripe_events`. `stripe_secret_key` is defined but **never used** — no checkout endpoint. |
| `traffic.py` | One row/request off the hot path (4-worker pool). IP/UA HMAC-hashed. Only ever writes `direction='in'`. |
| `usage.py` | Upserts today+yesterday into `usage_daily`; pre-creates/drops traffic partitions. |

**Endpoint catalog:**

| Method · path | Auth | Does |
|---|---|---|
| `POST /v1/logs/batch` | API key | Ingest batch into the chain (202, pending). 60/min. |
| `GET /v1/logs · /{seq} · /stats` | key or session | Paginated rows / one row / dashboard tiles + 7-day activity. |
| `GET /v1/verify` | key or session | Recompute chain, `first_broken_seq`, anchor cross-check (skips full recompute >50k). |
| `POST /v1/passport` | key or session | Recompute + aggregate by policy → PDF (weasyprint) else HTML. |
| `GET/POST/DELETE /v1/keys` | human admin | List / create (plaintext once) / revoke named keys. |
| `POST /v1/keys/rotate` | API key | SDK self-rotate: mint peppered, revoke all active, kill legacy hash. |
| `GET/PUT /v1/policies` | get: either · put: **admin** | Policy config (auto-created). PUT hardened → admin-only. |
| `GET/POST /v1/anchors` | get: either · post: admin | List receipts / anchor now (409 if head unchanged). |
| `GET /v1/analytics/threats` | API key | 🔴 O(all rows) — loads the whole org ledger into memory; Bearer-only so the web dashboard can't call it. |
| `GET /v1/invoices · /usage` | key or session | Billing history / usage rollup + quota headroom. |
| `POST /v1/auth/{login,logout,me,users,change-password}` | public / session / admin | Human dashboard session (no rate limit on login). |
| `POST /v1/leads · /v1/track` | public | Marketing lead capture (20/min) / pageview beacon (120/min). |
| `POST /v1/webhooks/stripe` | Stripe sig | Provision org / subscription status / upsert invoices. |
| `GET /v1/health · /health/ready` | key · **public** | Desktop probe / orchestrator readiness (503 if worker stale). |
| `/admin/v1/{auth,organizations,staff,stats,traffic}` | staff (role-gated) | Cross-org console; every mutation writes an `admin_actions` row. |

---

## 8. Data model

16 linear Alembic migrations → 14 tables (pgcrypto for `gen_random_uuid()`). No ORM
`relationship()` anywhere — associations are raw FK columns.

| Table | RLS | Purpose |
|---|---|---|
| `organizations` | — | Tenant root. Legacy key hash, Stripe fields, Phase-4 metadata (soft-delete, suspend, quota). |
| `audit_logs` | ✅ FORCE | The append-only hash-chain ledger + grading-outbox columns. |
| `chain_anchors` | ✅ FORCE | Public-chain anchor receipts. |
| `invoices` | ✅ FORCE | Per-org Stripe billing history. |
| `usage_daily` | ✅ FORCE | Per-org/day rollup so dashboards never scan the ledger. |
| `org_policies` | — | 1:1 compliance toggles the judge reads. |
| `users` | — | Human dashboard accounts (bcrypt, role, disabled). |
| `api_keys` | — | Named multi-key SDK creds, HMAC-peppered hash. |
| `staff_users` | — | Platform employees (not tenant-scoped). |
| `admin_actions` | — | Append-only staff audit trail, written in-tx with the mutation. |
| `traffic_events` | — | **RANGE-partitioned by created_at**, PK `(id, created_at)`, default + monthly partitions. HMAC ip/ua. |
| `marketing_leads` | — | Pre-sales funnel; partial-unique on `lower(email)` where not churned. |
| `stripe_events` | — | Idempotent webhook log (unique event id). |
| `worker_heartbeat` | — | ℹ️ **no ORM model** — single row, raw-SQL only, drives readiness. |

RLS is applied to exactly the four tables a customer reads directly, with a
byte-identical predicate: `org_id = current_setting('app.current_org', true)::uuid`.
Everything invisible to the ORM lives only in migrations: the RLS policies, the
partitioning, CHECK constraints, and partial/expression indexes.

> 🟡 **Double-unique on `stripe_customer_id`.** 0001 adds an inline anonymous UNIQUE;
> 0002 adds a named `uq_org_stripe_customer` guarded only by `duplicate_table`. On a
> fresh DB the table ends up with two unique constraints on the same column.

---

## 9. Web properties

Three self-contained single-file apps, no framework. Every `fetch()` target maps to a
real backend route — except the sales page.

**Sales page** — consumer pet, 🔴 **fake checkout.** A 4-chapter video story, three
tiers (Wanderer $0, Companion $4.99/mo, Guardian $39 once). The payment modal runs a
`setTimeout` "Payment Successful" then opens **bare github.com** — no Stripe, no card
transmission, despite a "🔒 SSL · Powered by Stripe" note. The contact form silently
**drops the subject and message** (sends only name+email). Only `POST /v1/leads` and
`POST /v1/track` are real. Placeholders throughout: bare github, `href="#"` Discord,
`yourusername` clone URL, `support@foxychat.ai` (a different domain).

**Customer dashboard** — real SPA ✅. Nine views, **same-origin session cookie** (no
keys in the browser). Login prefilled with demo creds `admin@demo.test /
adminpass123`. Static HTML ships hardcoded placeholders ("Yo Fatima", 48,219 events)
that three script blocks overwrite with real API data on load — a fake-then-real
flash. The Verify sandbox is deliberately client-side (`crypto.subtle` + a
`mockLedger`); only the explicit "verify live ledger chain" button hits `/v1/verify`.
Stale: a "Gemini 1.5 Pro" label, a "42ms judge latency" stat that's never updated.

**Admin console** — staff ops ✅. `noindex`, light "daylight voxel" theme with a
pre-paint dark-mode bootstrap. All calls under `/admin/v1/*` with the staff cookie.
Client role model `viewer < operator < superadmin` gates UI while the server enforces
the real thing. Views: Overview (KPIs + funnels), Orgs (drill-down, suspend/enable),
Traffic (client-filtered), Staff (create with one-time temp-password reveal),
Settings. Minor: suspend accepts an empty audit reason.

---

## 10. Deploy topology

One backend image, three hostnames, path-partitioned. `FOXY_ENV=prod` is hardcoded in
compose and the four secrets use `${VAR:?}`, so the stack refuses to start on
weak/missing/equal secrets.

| Service | Role |
|---|---|
| `db` | postgres:16, internal-only volume. |
| `foxy-migrate` | One-shot `alembic upgrade head`, then exits; everything waits on it. |
| `foxy-backend` | uvicorn, published **loopback-only** `127.0.0.1:8085:8000`, `--proxy-headers --forwarded-allow-ips=*`. Dir-mounts the HTML folders read-only. |
| `foxy-worker` | `python -m app.worker_main`. Heartbeat healthcheck (unhealthy if no beat <30s). Anchoring off by default. |
| `caddy` | Profile `edge`, **not started by default**. Only service that would own 80/443. |

**Two mutually-exclusive fronting strategies:**
- **Shared VM (default):** host nginx owns 80/443 → proxies `app.`/`admin.` to the
  backend's loopback `127.0.0.1:8085`; marketing served straight from disk. TLS via
  certbot.
- **Dedicated box:** `docker compose --profile edge up` → Caddy takes 80/443,
  auto-provisions Let's Encrypt, reverse-proxies to `foxy-backend:8000` over the
  compose network.

Routing is symmetric: `app.foxyaudit.tech` serves everything *except* `/admin` (which
404s); `admin.foxyaudit.tech` serves *only* `/admin`. Both proxies overwrite/ignore
client `X-Forwarded-For` so a client can't spoof its IP. `--forwarded-allow-ips=*` is
safe only because the backend is unreachable except via the proxy.

> 🟡 **Footgun fixed in prod, still live in dev.** Prod dir-mounts the HTML folders (a
> `git reset` swaps inodes; a single-file bind mount would serve the stale copy). The
> dev `backend/docker-compose.yml` still bind-mounts individual HTML *files*. The dev
> stack also seeds `Demo Corp` / `admin@demo.test`, runs on public 8000/5432 with weak
> demo secrets (no `FOXY_ENV`, so the fail-fast is off), and the Makefile prints a
> stale `acme_test_key` the seed never produces.

---

## 11. Tests & CI

64 test functions across 13 modules. CI runs: `sdk-and-chain` (no Postgres — the chain
test loads `chain.py` by path), `backend-integration` (postgres:16, migrations
validated by running them), plus GitLeaks, Trivy/SBOM, and a `py_compile` pass over
the desktop modules.

**What's genuinely proven:** the chain golden vector + tamper-cascade; multi-tenant
isolation for logs/stats/verify/keys/anchors/invoices/usage (incl. a zero-leak proof);
auth precedence (peppered vs legacy, wrong-pepper differs; cross-tenant login by
*password*, not row order); 3-surface channel separation in both directions; the staff
role ladder + "one admin_action per mutation, none on reads"; Stripe idempotency; the
EVM calldata round-trip.

> 🔴 **Critical caveat: RLS is bypassed AND untested.** The suite runs as the `foxy`
> superuser, which bypasses FORCE RLS — so what's verified is **application-layer
> `org_id` scoping**, not the Postgres policies. Combined with prod also running as
> that superuser, RLS is decorative today; the app-code `WHERE org_id` filters are the
> entire tenant wall. Other untested paths: rate limiters (reset before every test),
> live-chain anchoring, the real Gemini judge (monkeypatched), the async worker loop,
> and Stripe signature verification.

---

## 12. Bug / gap / drift register

Ranked by product/demo impact, not code aesthetics.

### High — breaks a headline promise

| # | Issue | Where |
|---|---|---|
| 1 | **Breach reaction never fires.** Nothing emits the `policy_breach` UDP event the fox + dashboard listen for; grading is async/remote. | `sdk/client.py` · `sdk_bridge.py` · `omni_fox.py` |
| 2 | **Signup → usable key is broken.** Checkout provisions an org but creates no `ApiKey` row and no login `User`; the plaintext key is returned only to Stripe and stored as legacy sha256. | `routers/billing.py` |
| 3 | **RLS is bypassed in prod & never tested** (superuser role). Tenant isolation rests entirely on app-code `WHERE org_id`. | db role · all routers · tests |

### Medium — correctness / scale / security

| # | Issue | Where |
|---|---|---|
| 4 | `/v1/analytics/threats` loads the whole org ledger into memory (no SQL aggregation/limit); also Bearer-only so the web dashboard can't use it. | `routers/analytics.py` |
| 5 | Malformed timestamp: `created_at.isoformat() + "Z"` double-appends an offset (`…+00:00Z`). | `routers/analytics.py:44` |
| 6 | Double unique constraint on `stripe_customer_id` (0001 inline + 0002 named). | `migrations 0001/0002` |
| 7 | Customer login has **no rate limit** (only staff login does). | `routers/auth_human.py` |
| 8 | `traffic_events` only ever writes `direction='in'` despite the in/out schema. | `middleware/traffic.py` |
| 9 | Dev compose single-file HTML mount serves stale content after a checkout/reset. | `backend/docker-compose.yml` |

### Low — dead code, cosmetic, drift

- **Dead/vestigial:** SDK `transport.py` never called; the SDK's client-side chain
  hash + timestamp are dropped by the backend; `ActiveWindowTracker` defined but never
  started.
- **Unreachable:** all 14 themes (UI hard-pins fixed skins); `estimate_tokens` is a
  placeholder heuristic.
- **Model drift:** code default Gemini `gemini-1.5-pro`, prod `gemini-2.5-flash`, UI
  labels say "Gemini 1.5 Pro".
- **No-ops / advisory:** admin IP allowlist allows all when empty; startup only runs
  advisory `alembic current`, never migrates.
- **Sales page:** fake Stripe claim; bare/placeholder links; `support@foxychat.ai`
  wrong domain; contact form drops the message; manual snippets don't match the code.
- **Hardcoded leftovers:** dashboard "Yo Fatima"; `build_16frame_atlas` points at
  another dev's absolute path; Makefile's stale `acme_test_key`; suspend accepts an
  empty audit reason.

---

*Generated by reading the source. When a checked-in doc and the code disagree, trust
the code.*
