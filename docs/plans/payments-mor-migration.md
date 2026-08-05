# Payments — leaving Stripe for a Merchant of Record

MAIN chat, 2026-08-05 · **verified against `main` @ `44f3747`**

> **On the anchor.** Every measurement below was first taken at `8a00b95` and
> **re-taken at `44f3747`** after fast-forwarding — six commits (A5, A6, A7, R2 and
> `admin_leads.py`) had landed while this was being written. All eleven verdicts
> survived unchanged: none of those commits touch `billing.py`, `models.py`,
> `config.py`, `billing_state.py`, `auth.py`, `admin_orgs.py`, the dashboard, the
> desktop or the sale page.
>
> **Cites in this plan name symbols, not line numbers.** A line number in a file
> under active multi-phase edit is stale by construction. Re-locate by content;
> if a cite here disagrees with the file, the file wins — and say so in the report.

---

## 1 · Context

Foxy Audit's entire billing layer is written against Stripe, and the owner cannot
open a Stripe account from Pakistan. Owner decision 2026-07-29: move to a
Merchant of Record. The blocker — the Payoneer account — **opened today**.

Payoneer is the **payout rail**, not the checkout. Verified today against
Payoneer's own pages: **Payoneer Checkout requires a Hong Kong legal entity and
webstore volume above USD $20,000/month.** Foxy has neither, so Payoneer Checkout
is out and the two-layer shape from the vault note still holds — an MoR takes the
card, Payoneer receives the payout.

The deadline shapes the plan. XPRIZE is **2026-08-17 — 12 days** — and it needs
real revenue. MoR onboarding is a KYC process measured in days-to-weeks, so the
plan opens with a path that can take money **this week**, independent of it.

**Owner decisions, 2026-08-05:**

| Decision | Choice |
|---|---|
| MoR | **Apply to Paddle and Polar now; build against Paddle.** Paddle pays out to Payoneer directly; Polar pays out via Stripe Connect Express, which would leave the new Payoneer account unused. |
| Swap shape | **Adapter seam, Stripe left dormant.** Nothing deleted, every existing test keeps passing, KYC failure is not a dead end. |
| Interim revenue | **Yes — Payoneer payment request + staff activation.** Verified available to Pakistani businesses today; cards accepted at up to 3.99%. |

---

## 2 · What the premises actually are

Re-measured against `main` @ `44f3747`, not against the note.

| # | Premise | Verdict | What the code says |
|---|---|---|---|
| 1 | "84 Stripe references in `billing.py`" (2026-07-29) | **stale — understated** | **115 occurrences across 101 lines** in a 1,140-line file. E1's `upgrade_session` + `_upgrade_existing_org` added most of the growth. |
| 2 | "11 in `models.py`, 8 in `config.py`, 5 in the dashboard HTML" | **two drifted** | `models.py` **12**, `config.py` **10**, `foxy-audit-premium.html` **12** (not 5 — E3 wired three new surfaces). |
| 3 | The coupling is 4 files | **FALSE — the surface is ~2.5× wider** | **11 live source files**, plus **11 test files / ~179 tests**. Absent from the note: `admin_billing.py` (22), `account.py` (13 — including a live `stripe.Invoice.retrieve`), `billing_state.py` (9), `admin_data.py` (8), **`foxy-adminpage/index.html` (41)**, **`desktop/billing_data.py` (14)** + `billing_page.py` (4), `foxy-sale-page/index.html` (the only anonymous buy button). |
| 4 | "Billing is doubly off, nothing is on fire" | **true of the endpoints** | `backend/.env` holds no `STRIPE_*` key at all, so every Stripe-calling route returns 503. Confirmed. |
| 5 | "The payment gate is off" | **only ⅓ true** | `billing_state.dashboard_lock()` is **three** independent locks, tried in order: `evaluation_lock` (**no feature flag — always live**) → `subscription_lock` (`subscription_lock_enabled` defaults **True** in `config.py`) → `card_lock` (off). The note describes only the third. |
| 6 | "`REQUIRE_CARD_ON_FILE` stays off; we cannot lock anyone out" | **holds** | `require_card_on_file` is False in `config.py`, **and** `card_gate_grandfather_before=""` makes `grandfathered()` exempt every org. Two independent safeties. |
| 7 | "Two columns on `organizations` + a `stripe_events` table" | **understated** | Also `subscription_status`, `past_due_since` (migration **0060**, written after the note), and the four `card_*` columns (0055). Plus `invoices.stripe_invoice_id` UNIQUE. |
| 8 | "The grandfather clause is processor-agnostic bar one attribute" | **holds** | `billing_state.grandfathered()` — one `getattr(org, "stripe_subscription_id")`. |
| 9 | "Cancel UI shell + pre-change notice email survive" | **holds** | Cancel UI renders the server's `access_until` with no local date maths; `user_notifications.send_trial_ending_notices` is processor-free. |
| 10 | "E1/E2/E3 did not change any of this" | **FALSE — E1 and E3 changed it materially** | E1 added `POST /v1/billing/upgrade-session` + `_upgrade_existing_org`, moved the evaluation reasons into `billing_state`, and added `past_due_since`. E3 wired **three dashboard surfaces and the desktop** to it. E2 (export bundle) is billing-free. The `metadata.foxy_org_id` contract E1 introduced is now the **most portable idea in the design** — every MoR has the same field. |
| 11 | "Payoneer is a payout rail, not the checkout" | **holds — and is now load-bearing** | Payoneer Checkout: Hong Kong entity + >$20k/mo. Payoneer *Payment Request*: available in Pakistan, cards up to 3.99%, but invoice-based — no subscriptions, no webhooks, no self-serve. |

### ⚠ The blocker nobody mentioned

**The staff activation path the owner just chose for interim revenue cannot
rescue the customer most likely to pay.**

`POST /admin/v1/organizations/{org_id}/plan` — `set_organization_plan` in
`backend/app/routers/admin_orgs.py` — already exists, is step-up gated, and sets
`plan_tier`, `monthly_log_quota`, `trial_ends_at`, `subscription_status="active"`
and clears `past_due_since`. The admin console already calls it. **The interim
path is, to a first approximation, already built** — which is the good news.

What it does **not** do is clear the four evaluation columns. So for an org whose
evaluation window has expired:

- `dashboard_lock()` tries `evaluation_lock()` **first**, which fires on
  `evaluation_offer_id` being set and `evaluation_ends_at` being in the past —
  neither of which staff activation touches. The dashboard stays locked.
- `capture_block()` returns `evaluation_expired` for the same reason. Capture
  stays refused.

**The customer pays by Payoneer, staff sets them to `pro`, and nothing changes.**
This is exactly register #36, which E1 fixed for the Stripe path only —
`_upgrade_existing_org` in `backend/app/routers/billing.py` clears those four
fields and its own docstring says "before this, no code path in the product ever unset
`evaluation_offer_id`." That is still true of the staff path. And an expired
evaluator is the single most likely first paying customer.

This is why M0 exists and why it ships before anything else.

---

## 3 · Phases

| Phase | Branch | Scope | Ships alone? |
|---|---|---|---|
| **M0** | `fix/staff-plan-activation` | Make the staff activation path actually unlock a paid customer, and record what they paid. Backend + admin console. | **Yes — this is the deadline item** |
| **M1** | `feat/payments-seam` | A provider seam under `backend/app/payments/`; today's Stripe code moved behind it verbatim. **Zero behaviour change.** | Yes |
| **M2** | `feat/payments-paddle` | The Paddle adapter behind the seam. Dark unless `PADDLE_*` is configured. | Yes |
| **M3** | `feat/payments-surfaces` | Provider-aware copy across dashboard / sale page / desktop / admin; a `payment_processor` column. | Yes |
| **M4** | — owner, not code | Paddle + Polar KYC; the consent copy's legal read. | — |

**Deliberately not in scope:** renaming `stripe_customer_id` /
`stripe_subscription_id` / `stripe_events` / `stripe_invoice_id`. That is a
migration plus ~179 test rewrites plus the admin console's whole Stripe-events
page, for **zero behavioural gain**, before any processor is confirmed working.
M3 adds one honest `payment_processor` column instead; the rename is a later
decision made with a live processor in hand.

---

## 4 · M0 — the interim revenue path

**Branch:** `fix/staff-plan-activation` off fresh `origin/main`.

### 4.1 Clear the evaluation regime on staff activation

`backend/app/routers/admin_orgs.py` → `set_organization_plan`.

The four-field clear currently lives inline in `billing.py::_upgrade_existing_org`.
Extract it to **one** helper both call — put it in `backend/app/billing_state.py`
(both modules already import it; it is the module that owns what these fields
*mean*). Reuse, do not re-derive: copy the exact field set and the exact
reasoning from `_upgrade_existing_org`'s docstring.

**Do NOT delete the `EvaluationRedemption` row.** `models.py` puts a UNIQUE on
`org_id` alone — one redemption per org, ever — and that row is the record that
this workspace already had its offer. Deleting it silently re-arms a second
redemption. `_upgrade_existing_org`'s docstring says this; keep it true.

### 4.2 Record what was actually paid

Add an optional `payment_reference: str | None` (max ~128 chars) to `PlanRequest`
and pass it into the existing `record_admin_action(..., detail={...})` call
alongside `plan` and `monthly_log_quota`.

**Do not** write an `Invoice` row for this. `invoices.stripe_invoice_id` is
UNIQUE NOT NULL and means a Stripe invoice; stuffing `"payoneer:12345"` into it
is a lie about the column and a migration we do not need yet. The admin action
**is** the record, it is already immutable and already in the audit trail.

### 4.3 The admin console control

`foxy-adminpage/index.html` — the org plan modal (find it by the
`/organizations/'+id+'/plan` call site; **do not trust a line number**, seven
phases have edited this file this week). Add one optional text input, labelled so
it is obviously for a Payoneer payment-request id or an invoice number, and send
it in the POST body.

Honest empty state: if nothing is typed, send nothing — no placeholder, no
invented reference.

### 4.4 Guards — assert at the call site, not on the helper

A6's lesson applies directly: a guard that reads a helper's body stays green
while the caller stops calling it. So:

1. **The one that matters.** Build an org with `evaluation_offer_id` set and
   `evaluation_ends_at` in the past. Assert it is locked (402) and capture is
   refused. `POST /admin/v1/organizations/{id}/plan` with `plan="pro"`. Then
   assert **a customer dashboard route returns 200** and **`POST /v1/logs`
   returns 202**. Assert the outcome, not the helper — and per §6.5 of the
   playbook, assert `== 202`, never `!= 402`.
2. A paying org that never had an evaluation is unaffected.
3. `EvaluationRedemption` still exists for that org afterwards.
4. `payment_reference` reaches `admin_actions.detail`; omitting it stores no key.
5. The admin console posts the field (guard the call site).

**Make each of these fail on purpose before trusting it.**

### 4.5 Verify

```bash
cd backend && DATABASE_URL=postgresql+psycopg://foxy:foxy@localhost:5433/foxy_pytest \
  python -m pytest tests/integration -q          # baseline 928 (3 skipped)
pytest foxy-adminpage -q                         # the console's own suite
node --check   # every inline <script> in the changed HTML
```

Cross-surface, per §6.6: this touches `billing_state`, so also run
`pytest foxy-dashboard -q` (321) and `pytest desktop` **from the repo root** (775).

---

## 5 · M1 — the provider seam

**Branch:** `feat/payments-seam`. **This phase changes no behaviour at all.**

Create `backend/app/payments/` with:

- `base.py` — a `PaymentProvider` protocol: `checkout_session(org, plan, *, upgrade: bool)`,
  `portal_session(org)`, `cancel_subscription(org)`, `verify_webhook(body, headers) -> NormalisedEvent`,
  and a `configured` property.
- `normalised.py` — the event vocabulary the app already speaks, lifted out of
  Stripe's: `checkout_completed` · `card_setup_completed` · `card_attached` ·
  `card_detached` · `subscription_updated` · `subscription_cancelled` ·
  `invoice_recorded`, each carrying `org_id | customer_id | subscription_id |
  plan | status | invoice fields`.
- `stripe_provider.py` — today's code, moved, not rewritten.
- `registry.py` — pick the provider from config; return a null provider when
  nothing is configured so every route keeps answering **503**, exactly as now.

`billing.py`'s route handlers stay where they are and keep their status codes,
their rate limits, their `_GATE_EXEMPT` paths and their docstrings. Only the
provider calls move.

**Keep `billing_state.py` as the mapping's owner.** Its module docstring already
states the stored vocabulary is *not* Stripe's — that decision is what makes this
seam cheap, and it must survive: `NEVER_ACTIVATED` / `PAST_DUE` / `ENDED` and the
reason strings are the wire contract three surfaces switch on.

**What must NOT move.** The reason strings in `billing_state.py`
(`card_required`, `subscription_past_due`, `evaluation_expired`, …) are matched
verbatim by the dashboard, the desktop and the admin console. Changing one is a
cross-surface break, not a rename. `desktop/foxy_client.py` matches wire detail
strings as constants.

**Proof of no-change:** the existing ~179 tests are the golden vector and must
pass unchanged — **do not edit a billing test in this phase.** If a test needs
editing, the refactor changed behaviour and that is the finding to report back.

---

## 6 · M2 — the Paddle adapter

**Branch:** `feat/payments-paddle`.

`backend/app/payments/paddle_provider.py`, plus `PADDLE_*` settings in
`config.py` (empty by default → the provider reports unconfigured → 503, same as
Stripe today).

**Verify every API detail against Paddle's live documentation, not against this
plan and not from memory.** Names, event types, the signature-header format and
the customer-portal mechanism have all changed between Paddle Classic and Paddle
Billing. Report anything here that turns out to be wrong.

The three things that must map, and they are the reason Paddle was chosen:

1. **Org identity through checkout.** Paddle's checkout carries a custom-data
   field. Put `foxy_org_id` in it, exactly as `upgrade_session` does for Stripe.
   This is what `_handle_checkout` branches on to upgrade rather than provision —
   the fix for register #36 — and it must keep working identically.
2. **Webhook signature verification**, into the same durable-log-then-dispatch
   shape `stripe_webhook` already uses. Keep the idempotency: log first, `ON
   CONFLICT DO NOTHING`, dispatch and stamp in one transaction. That design is
   processor-independent and is why replay is safe.
3. **Subscription status → our vocabulary.** Paddle distinguishes states Stripe
   collapses. Register #41 is exactly this: Stripe's `unpaid` and `past_due` both
   store as `past_due`, so we cannot tell "card retrying" from "gave up", and the
   grace window exists to paper over it. **If Paddle reports those separately,
   say so in the report — it may close #41 for free, and that is a mapping
   decision the owner should make, not the executor.**

Log the exception **type**, not `str(exc)`, anywhere a key could be in the
message — `gemini.py` sets the precedent and hard rule 6 requires it.

Until M3, Paddle events land in `stripe_events`. That is ugly and it is
deliberate: it is one table rename versus shipping a second webhook log.

---

## 7 · M3 — the surfaces

**Branch:** `feat/payments-surfaces`. Migration **0062** (head is `0061`).

- One new column, `organizations.payment_processor` (nullable), so a row says
  which processor owns it. No renames.
- Customer-visible copy stops naming Stripe: the dashboard's "confirmed by Stripe
  at checkout" lines, the desktop's `billing_data.py` blurbs, the sale page, the
  invoice empty states. **Load `ui-ux-pro-max` → `impeccable` → `frontend-design`
  before touching any of it** — this is UI work and the rule has no exceptions.
- **Staff-facing copy in the admin console keeps naming the processor**, because
  there the processor's own status names are the truth and renaming them would
  make the ops console lie. That distinction is already the console's stated
  reasoning; keep it.
- `foxy-sale-page/index.html`'s anonymous buy button is the only acquisition
  entry point and the flow the sale page depends on — it is the last thing to
  move and it moves only once a Paddle checkout has been completed for real.

---

## 8 · Assumptions I want overruled

Say so in the report if any of these is wrong — every one of them is a claim, not
a preference:

1. That `set_organization_plan` clearing the evaluation fields is safe for every
   org, not just expired ones. Check `capture_block` and `dashboard_lock` for a
   case where clearing them makes an org *worse* off.
2. That no billing field is hashed into the chain. I read `chain.py` as taking
   `event_metadata ∪ {verdict_hash}` and nothing billing-shaped — **verify it**,
   because if a billing field is chain material, M3's column is a chain change
   and this plan is wrong.
3. That `payment_reference` in `admin_actions.detail` is a sufficient record of a
   manual payment. If the owner needs it to appear in the customer's own invoice
   list, that is an `Invoice`-shaped change and a bigger phase.
4. That the reason-string vocabulary can survive a processor swap unchanged. If
   Paddle exposes a state ours cannot express, adding a reason means new
   dashboard copy in the same phase — see §6.6, this exact split went red on
   `main` for three commits during E1.

---

## 9 · After each merge

Per the playbook §4, in the same session:

- **Devlog** → `Devlogs/2026-08-05.md`, appended, leading with what surprised you.
- **Area notes** → `Backend/CLAUDE.md`, `Database/CLAUDE.md`, `Admin Console/CLAUDE.md`,
  `Dashboard/CLAUDE.md`, `Desktop/CLAUDE.md` — re-stamp `updated:` /
  `verified-against:` / `verified-on:` for every area the phase touched.
- **Register** → open a new entry for the staff-activation/evaluation defect
  (§2's blocker) and close it with the M0 SHA. Note against **#41** whether
  Paddle can distinguish the two past-due states.
- **The payments note** → rewrite the coupling table (it is understated by ~2.5×)
  and record the Payoneer Checkout eligibility finding, dated, with the source.
- **`Where Claude Was Wrong`** → the note's own file list was treated as complete
  by three later documents. That is the vault being wrong, so it goes in
  `Worth Noting — Issues`; only add here if a *reasoning* error surfaces.

---

## 10 · Verification, end to end

```bash
# after M0 — the real question, on real rows
cd backend && DATABASE_URL=postgresql+psycopg://foxy:foxy@localhost:5433/foxy_pytest \
  python -m pytest tests/integration -q
pytest foxy-dashboard -q ; pytest foxy-adminpage -q ; pytest desktop ; pytest sdk/tests verifier -q

# after M1 — the no-change proof
git stash && <run the 179 billing tests> && git stash pop && <run them again>   # identical

# after M2 — with real Paddle sandbox keys, one end-to-end purchase:
#   signed-in upgrade → webhook → org upgraded, evaluation cleared, dashboard unlocks
```

**Baselines (a drop is a regression):** backend **928** (3 skipped) · dashboard
**321** · desktop **775** · SDK **143** · verifier **31**. Alembic head **0061**.

**Must not move:** the reason strings in `billing_state.py` · the 402 body shape ·
`_GATE_EXEMPT` · SDK ingest never being gated · the chain recipe · every existing
billing test passing unedited through M1.

**Watch CI *and* CD.** They are separate workflows; a green deploy is not a green
gate. And `[skip ci]` substring-matches the whole commit body — do not write
about the marker.
