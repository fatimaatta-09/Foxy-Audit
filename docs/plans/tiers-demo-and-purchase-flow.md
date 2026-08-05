# Tiers, demo, and the purchase flow

MAIN chat, 2026-08-06 · **verified against `main` @ `a44e1c0`**

> **Cites name symbols, not line numbers.** Re-locate by content. If a cite here
> disagrees with the file, the file wins — and say so in the report.

---

## 1 · Context

The commercial model changed. Until now the product sold a free tier plus paid
plans through an anonymous checkout. The new shape, decided 2026-08-06:

| Route | How it works |
|---|---|
| **Demo** | 7 days. **Manually approved** — self-serve signup was letting anyone farm free access. Dashboard + SDK only; desktop download locked. **The dashboard locks at day 7**, and the only way out is buying a plan. |
| **Pro / Max** | Account first, then pay. Paddle subscription. On return they are signed in, get their API key once, and land on a page offering the SDK, the desktop app, and the dashboard. |
| **Premium** | Sold by conversation. They contact, you talk, you send a Payoneer link, staff activate the workspace. **No Paddle involved.** Fully negotiated — credits, terms, price. |
| ~~Free~~ | **Removed for new signups.** Existing free organisations are grandfathered and see no change. |

## 2 · Owner decisions, 2026-08-06

| Decision | Choice |
|---|---|
| Premium entitlements | Unlimited, **and Foxy pays for the LLM judge** (`PLATFORM_KEY_TIERS` stays as-is) |
| Premium on the pricing page | "From $X/mo — talk to us". Fully customisable; no fixed amount |
| Signup order for Pro/Max | **Account first, then pay** |
| Demo approval | **Approve first — the applicant waits.** Dedicated queue in the admin console |
| Demo clock | Starts **at approval**, not at signup |
| Desktop for demo users | Download **locked on the welcome page**; no backend entitlement check |
| Existing free orgs | **Grandfathered.** Nothing changes for them |

## 3 · What the premises actually are

Checked against the code, not against the brief.

| # | Premise in the brief | Verdict | What the code says |
|---|---|---|---|
| 1 | "the 3-options page" is new | **FALSE — it exists** | `foxy-sale-page/welcome.html` already shows the API key with a copy button and a shown-once warning, the Windows installer and Linux AppImage, and a dashboard button. It needs the **SDK link** and the **demo locking**, not building. |
| 2 | the dashboard needs an upgrade button wiring to checkout | **FALSE — E3 built it** | `upgradePlanBtn` "See upgrade options" already routes to the upgrade page, which calls `/v1/billing/upgrade-session`. |
| 3 | "after 7 days the page is locked" is current behaviour | **FALSE — this is new** | `dashboard_lock()` is `evaluation_lock or subscription_lock or card_lock`. **Trial expiry is not in it** — it lives only in `capture_block`. Today a free org past its trial still reads its dashboard; it just cannot record. A trial gate on the dashboard is a **new lock**. |
| 4 | "premium" is an available name | **FALSE — heavily load-bearing** | It is what evaluation offers set (`plan_tier="premium"`), it is `PLATFORM_KEY_TIERS`, `canonical_plan` maps `guardian`/`enterprise` onto it, and quota/seat/cadence/key limits all key off it. A paid Premium org and an evaluation org are **indistinguishable by tier alone**. |
| 5 | Premium can be "any amount of anything" | **half true** | **Only `monthly_log_quota` is a per-org column**, and `set_organization_plan` already accepts an override for it. Seats, API-key limits and anchor cadence are **per-tier config** — one value shared by every Premium org. |
| 6 | manual approval is a setting | **FALSE — nothing exists** | No approval, pending or awaiting state anywhere in `models.py`. `/v1/signup` provisions instantly. |
| 7 | the sale page sells the tiers you named | **FALSE** | It has `signup-companion` ×3 and `signup-guardian` ×2, which Paddle has no price for. They 422 and fall into a lead-capture fallback that says *"we'll email you to finish checkout."* Five buttons that look like buying and are not. |
| 8 | linking the buttons to the checkout subdomain is work | **FALSE — already done** | Paddle returns `<default payment link>?_ptxn=…`. Both buttons already follow `checkout_url`. Setting the default payment link points every button at the subdomain with **zero code changes**. |

### ⚠ The collision nobody mentioned

**Selling Premium breaks the evaluation regime's only marker.**

An evaluation offer sets `plan_tier="premium"` (`billing.signup` and
`billing.redeem_offer`). `billing_state.evaluation_lock`'s docstring is explicit
that the tier is left reading premium on a locked org **on purpose**, because
correcting it to `free` would hand back the free quota and restart capture.

So once Premium is a tier customers *buy*, `plan_tier="premium"` means two
different things: "paying enterprise customer" and "evaluation, possibly
expired". Everything that reads the tier — quota, seats, anchor cadence, and
`PLATFORM_KEY_TIERS`, **which spends Foxy's own LLM key** — cannot tell them
apart. An expired evaluator currently reads premium, and would therefore be
granted platform-key grading on Foxy's budget.

The four `evaluation_*` columns already distinguish them; nothing consults them
for entitlement. **This must be resolved in M4a before Premium is sold.**

## 4 · Phases

Ordered by what unblocks money, not by what is most interesting.

| Phase | Branch | Scope | Blocks revenue? |
|---|---|---|---|
| **M3a** | `feat/paddle-checkout-page` | `checkout.foxyaudit.tech` — the Paddle.js page. **Already prompted.** | **YES — no card can be taken without it** |
| **M4a** | `feat/tier-model` | Backend: separate paid-premium from evaluation-premium, the demo state + approval, the trial dashboard lock scoped to demo orgs, clock-at-approval | Partly |
| **M4b** | `feat/sale-page-tiers` | Pricing page: 3 tiers with real prices; remove companion/guardian; demo route; Premium contact route | **YES — Paddle domain review needs prices** |
| **M4c** | `feat/admin-approvals` | Admin console: the approvals queue | No |
| **M4d** | `feat/welcome-page` | `welcome.html`: SDK link, demo locking, account-first ordering | No |
| **M3b** | `feat/paddle-surfaces` | Registers #98, #93, #94, #97 | No |

**The revenue-critical path is M3a + M4b + Paddle verification.** Everything else
matters and none of it stands between you and a paying customer.

## 5 · M4a — the tier model

### 5.1 Separate paid Premium from evaluation Premium

This is the phase's real work and everything else depends on it. **Do not add a
new `plan_tier` value** — three surfaces and `desktop/foxy_client.py` switch on
the existing strings, and `admin_orgs.set_organization_plan` validates against
`{free, pro, max, premium}`.

The distinguishing fact already exists on the row: an evaluation org has
`evaluation_offer_id` set. So the rule is *premium **and** no evaluation offer =
paid premium*. Put that in **one** predicate in `billing_state.py` — next to
`on_a_paid_plan` — and route every entitlement decision through it, starting with
`judge_routing.PLATFORM_KEY_TIERS`, which is the one that spends Foxy's money.

**Verify the direction before building.** An expired evaluator must NOT get
platform keys. A paid Premium customer MUST. Guard both.

### 5.2 The demo state and approval

- Demo orgs are `plan_tier="free"` internally with `trial_ends_at` — the existing
  machinery, labelled "Demo" in the UI. **Do not invent a `demo` tier**; every
  quota, seat and cadence lookup would need a new branch for no behavioural gain.
- Approval: a dedicated queue was chosen over reusing `suspended`. That means a
  real pending state. Model it so a pending org **cannot capture and cannot read
  the dashboard**, and say so honestly on screen — "we're reviewing your request",
  not a broken page.
- **`trial_ends_at` is set at APPROVAL, not at signup.** It stays NULL while
  pending. Check `capture_block`'s trial branch handles NULL — it already guards
  `trial_ends_at is not None`, so a pending org is not "expired", it is "not
  started". Confirm that reading.

### 5.3 The trial dashboard lock — new, and scoped

Add a trial condition to `dashboard_lock`. **It must not fire on grandfathered
free orgs**, which is the whole reason the owner chose to grandfather them.

Scope it to organisations that came in through the demo route. That needs a
marker — decide whether it is the pending/approved state itself or a separate
column, and say which and why. A date cutoff is the wrong tool here: the
[[grandfather clause]] already demonstrates that a date baked into config is
wrong the moment it passes.

`trial_expired` is already in the shared reason vocabulary and already has
dashboard copy (`test_d4_payment_lock.py` asserts the vocabulary matches the
backend). **Adding a lock that emits an existing reason needs no new copy** —
confirm that before writing any.

## 6 · M4b — the sale page

- Pricing page: **Pro $49/mo · Max $199/mo · Premium "From $X/mo — talk to us"**.
  Real numbers, published. This is not a breach of the no-fake-data rule — that
  rule forbids **inventing** numbers, not publishing the real ones. Paddle's
  domain review requires it.
- **Remove the `signup-companion` and `signup-guardian` buttons** — five of them.
  Paddle has no price for either, so today they silently become a mailing-list
  signup for someone who thought they were buying.
- Rewrite the checkout fallback message. *"We'll email you to finish checkout"*
  was honest when no processor was configured; once Paddle works it only fires on
  a real error, and it tells the buyer the wrong thing.
- The Premium route is a contact form, not a checkout. **`book-a-demo.html`
  already exists with reCAPTCHA and a lead pipeline** — reuse that pattern rather
  than building a second one.
- Demo route: signup → "we're reviewing your request" → email on approval.

**UI work — load `ui-ux-pro-max`, then `impeccable`, then `frontend-design`,
before touching anything.** Pricing is the page that decides whether someone
believes the product is real.

## 7 · M4d — welcome.html, account-first

The page exists and already does most of this. Changes only:

- Add the **SDK** alongside the desktop downloads (the third of the three things).
- **Demo accounts:** the desktop download renders locked with an honest "available
  on a paid plan" label. Not hidden — locked and explained.
- Account-first means the buyer is **signed in** when they return from Paddle,
  which is the only reason showing an API key on this page is safe. Confirm the
  session actually exists at that moment before relying on it.

**Never email an API key.** `billing._deliver_credentials` exists specifically to
send a set-password link instead, and its docstring says why. That rule survives
this change.

## 8 · Assumptions I want overruled

1. That Premium needs no per-org seat/key/cadence columns. Today Premium is
   unlimited on all three, so any negotiated number is *satisfied* — you cannot
   **cap** one Premium customer differently, except on credits. If a real
   customer needs capping, this is wrong and it is a migration.
2. That demo orgs can stay `plan_tier="free"` internally. If anything reads the
   tier to mean "has never paid" in a way that breaks for a *pending* org, say so.
3. That the trial lock can be scoped without a new column. If the only honest
   marker is a new column, take it — a wrong scope here locks out grandfathered
   customers, which is the one outcome the owner ruled out.
4. That `evaluation_offer_id is None` is a sufficient test for "paid premium".
   Check whether a paid Premium customer could ever also hold an evaluation row.

## 9 · Verification

```bash
cd backend && DATABASE_URL=postgresql+psycopg://foxy:foxy@localhost:5433/foxy_pytest \
  python -m pytest tests/integration -q      # 985 + 3 skipped before additions
pytest foxy-dashboard -q ; pytest foxy-adminpage -q ; pytest desktop ; pytest sdk/tests verifier -q
```

**Must not move:** the reason strings in `billing_state.py` · the 402 body shape ·
`_GATE_EXEMPT` · SDK ingest never being gated · grandfathered free orgs seeing no
change · the chain recipe.

**The blast-radius question only production can answer** — how many organisations
are on the free tier today, and how many hold an evaluation offer. Ask the owner
to run it before M4a merges.
