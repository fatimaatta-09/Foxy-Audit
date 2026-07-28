# P3 §4 — signup card consent copy · **DRAFT, NOT APPROVED**

> ## ⚠ THIS NEEDS A HUMAN READ BEFORE IT SHIPS
>
> Written by an executor chat. **Not reviewed by a lawyer. Not approved by the owner.**
> Collecting card details for a tier advertised as free is regulated in several jurisdictions,
> and the wording below is what decides whether this is fine or a problem. Do not paste it into
> the product on my say-so.
>
> The code is built so this is a copy decision, not an engineering one: the strings live in the
> signup/lock UI and in `/v1/billing/access`, and changing them changes nothing else.
>
> **Reviewed by: TBD · Date: TBD · Outcome: TBD**

---

## What the implementation actually does

Stated plainly, because the copy has to match the behaviour exactly or it is worse than useless:

| Claim | Mechanism | Where |
|---|---|---|
| The card is verified, not charged | Stripe Checkout `mode="setup"` — a SetupIntent, **no `line_items`**, so no charge can be created | `billing.card_setup_session` |
| The free tier is never charged | Nothing in the card path creates a charge; upgrades go through `/v1/billing/checkout-session`, which the user starts | `billing.py` |
| We store nothing chargeable | Only `card_on_file`, `card_brand`, `card_last4`, `card_added_at`. No PAN, no token, no payment-method id | migration `0055` |
| You get warning before anything changes | Trial-ending notice `billing_change_notice_days` (4) ahead, ignoring notification preferences | `user_notifications.send_trial_ending_notices` |
| Cancellation without contacting support | `POST /v1/billing/cancel` → `cancel_at_period_end`, plus the Stripe portal | `billing.cancel_subscription` |

If any row above stops being true, the copy below becomes a false statement. That is the thing to
re-check at review time, not the prose.

---

## Draft A — signup / card capture (recommended)

**Heading:** Add a card to activate your workspace

**Body:**

> We verify your card with a **$0 authorisation** — a check with your bank that the card is real.
> **No money is taken, and nothing is scheduled.**
>
> The Free plan is free. We will never charge this card unless you choose a paid plan yourself
> and confirm the price on a separate screen.
>
> You can remove your card, or close your account, at any time from Settings → Billing. No email
> and no support ticket.

**Checkbox (required, unticked by default):**

> I understand my card will be verified with a $0 authorisation and will not be charged unless I
> choose a paid plan.

**Under the button:** `$0 today · Free plan · Cancel anytime`

---

## Draft B — the locked-dashboard state (§4.6)

**Heading:** One step left — add a payment method

**Body:**

> Your workspace is ready. To open your dashboard, add a card so we can verify it.
>
> **You will not be charged.** This is a $0 authorisation, not a payment. The Free plan stays free,
> and your audit evidence keeps being recorded either way — nothing about your ledger depends on this.

The last clause matters and is true: the SDK ingest path is deliberately never card-gated, so a
customer without a card still captures evidence. Do not cut it.

---

## Draft C — the pre-change notice email (already implemented)

Shipped wording in `send_trial_ending_notices`:

> Your Foxy Audit trial ends on {date}, in {n} days.
> You will not be charged. Nothing happens to your card unless you choose to upgrade — your
> evidence and your ledger stay exactly where they are.

---

## What a reviewer should push back on

Flagging these because they are the parts I am least sure of, not to pre-empt the answer:

1. **"$0 authorisation" is jargon.** Accurate, but a consumer may not know it means "no money
   moves". Consider leading with "we will not charge you" and putting the mechanism second.
2. **US · ROSCA** requires clear and conspicuous disclosure of material terms, express informed
   consent, and simple cancellation *before* obtaining billing information. A required unticked
   checkbox is the intent of Draft A — confirm that a checkbox is the right instrument here, and
   that the disclosure sits *above* it rather than behind a link.
3. **EU/UK** — is this a "negative option" / inertia-selling arrangement at all, given no charge is
   ever scheduled? I believe it is not, because no subscription starts and nothing auto-converts.
   That belief should be checked, not assumed. SCA may also surface a bank challenge during the $0
   authorisation; the copy currently does not warn that the bank might ask for confirmation.
4. **Gating a free tier behind a card is a product decision with a conversion cost**, separate from
   the legal question. It is not my call, but it should be a deliberate one.
5. **The word "activate"** in Draft A implies the account is not yet live. It is — signup already
   provisions the org and the SDK key. Consider "unlock your dashboard" for accuracy.
6. **Storing `card_brand`/`card_last4`** is display-only and not PAN, but confirm it is in scope for
   the privacy policy's list of what we hold.

---

## Deployment note, not a copy note

`REQUIRE_CARD_ON_FILE` ships **off**. Every existing organisation reads as "no card on file", so
turning it on locks every current customer out of their dashboard until they add one. There is no
grandfather clause — deliberately, because a hidden cutoff date is a behaviour nobody asked for and
nobody would remember. If existing customers should be exempt, that is a decision to make
explicitly, and it needs its own migration.
