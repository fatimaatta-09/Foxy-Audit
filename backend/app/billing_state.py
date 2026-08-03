"""What an organisation's billing state means for access (D1).

One place that answers three different questions, because before this they were
answered in three places that could not agree:

* ``auth.py`` locked the dashboard for a missing CARD and knew nothing about
  subscriptions at all.
* ``logs.py`` blocked capture on ``{cancelled, unpaid}``.
* ``account.py`` computed ``ingestion_blocked`` from its own copy of that set.

None of the three could say WHICH condition applied, which is what
``GET /v1/billing/access`` now needs in order for the dashboard to render "add a
card" differently from "your last payment failed". Those are different problems
with different fixes, and one message covering both helps nobody.

THE STORED VOCABULARY IS NOT STRIPE'S
-------------------------------------
``billing._handle_subscription_change`` maps Stripe's status names onto ours
before they are stored, and the mapping is lossy in one direction that matters
here: Stripe's ``unpaid`` — retries exhausted, Stripe has given up — is stored
as ``past_due``. So a stored ``past_due`` covers both "their bank declined once
and Stripe is retrying" and "this subscription is over and nobody paid". The
grace window below is what separates them, and it is why the window exists
rather than an instant lock.

``canceled`` is stored ``cancelled``; ``trialing`` is stored ``active``.
Everything else Stripe sends is stored verbatim, which is how ``incomplete`` and
``incomplete_expired`` reach us.

WHAT LOCKS, AND WHAT DOES NOT
-----------------------------
The dashboard lock and the capture block are deliberately NOT the same set:

* **Capture is never blocked by ``past_due``.** Evidence is the thing a customer
  would most regret losing and cannot recreate afterwards; a declined card is
  recoverable and their audit trail is not. ``auth.py`` already states this
  principle for the card gate ("a missing card can never cause a customer to
  silently lose audit evidence") and it applies with more force to a customer
  who is mid-retry.
* **``cancelled`` does not lock the dashboard**, though it does stop capture.
  That is today's shipped behaviour and it is kind: someone who left can still
  read and export the evidence they already paid for. Leaving is not owing.
* **An evaluation whose CREDITS ran out does not lock either** (E1). That org is
  still inside a live offer — the window is open, the allowance is simply spent —
  so it keeps reading its own evidence. Only the WINDOW closing locks.

Which leaves the lock meaning one thing: **you owe us, or you never paid.**
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from .config import get_settings

log = logging.getLogger("foxy.billing_state")

# ── the stored statuses, grouped by what they mean ──────────────────────────
#: Never activated: a subscription was created and its first payment never
#: completed (an abandoned SCA challenge, a declined first charge). Nothing was
#: ever paid, so there is no grace to extend.
NEVER_ACTIVATED = frozenset({"incomplete", "incomplete_expired"})
#: Owing: paid before, not paying now. Recoverable — see the grace window.
PAST_DUE = frozenset({"past_due"})
#: Over. Blocks capture, not the dashboard.
#: ``unpaid`` cannot currently be written by the webhook (it is mapped to
#: ``past_due``) but staff tooling and older rows can still hold it, so it stays.
ENDED = frozenset({"cancelled", "unpaid"})

# ── the reasons, as the wire sees them ──────────────────────────────────────
# These strings are the shared vocabulary between the 402 body's `code` and
# `/v1/billing/access`'s `reason`. One vocabulary, so the dashboard needs one
# switch rather than two that can drift apart.
NONE = "none"
CARD_REQUIRED = "card_required"
SUBSCRIPTION_INCOMPLETE = "subscription_incomplete"
SUBSCRIPTION_PAST_DUE = "subscription_past_due"
SUBSCRIPTION_CANCELLED = "subscription_cancelled"
TRIAL_EXPIRED = "trial_expired"
SUBSCRIPTION_INACTIVE = "subscription_inactive"   # capture-side name, unchanged
# E1 — the evaluation pair, moved here from logs.py. These two strings are
# unchanged from the 402 bodies logs.py used to build inline, so the SDK and any
# client already switching on them keep working.
EVALUATION_EXPIRED = "evaluation_expired"
EVALUATION_CREDITS_EXHAUSTED = "evaluation_credits_exhausted"


@dataclass(frozen=True)
class Condition:
    """One billing condition, ready to become either a 402 body or a banner."""
    reason: str
    message: str


def _aware(value: datetime | None) -> datetime | None:
    """Treat a naive stamp as UTC. Columns are timestamptz, but a row written by
    a test or a manual UPDATE can still arrive without a zone."""
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def on_a_paid_plan(org) -> bool:
    """True when this org is on a tier a subscription is supposed to cover.

    A free-tier org has no subscription to fail, so no subscription state can
    ever lock it — whatever ``subscription_status`` happens to hold. An unset
    tier counts as free: that is what every org created outside billing has.
    """
    return (org.plan_tier or "").strip().lower() not in {"", "free"}


def grace_ends_at(org) -> datetime | None:
    """When a ``past_due`` org stops being given the benefit of the doubt.

    ``None`` means "we do not know when this started", and the caller must read
    that as *not yet locked*. Every row that existed before ``past_due_since``
    was added reads that way, as does any status set by hand rather than by the
    webhook. Failing open on missing data is the same choice ``grandfathered``
    makes below, for the same reason: the alternative is locking real customers
    out over a column nobody populated.
    """
    started = _aware(getattr(org, "past_due_since", None))
    if started is None:
        return None
    days = max(0, int(get_settings().subscription_past_due_grace_days))
    return started + timedelta(days=days)


def subscription_lock(org, now: datetime | None = None) -> Condition | None:
    """The subscription condition that locks this org's dashboard, if any.

    Card state is NOT considered here — the card gate is a separate decision
    with its own flag and its own grandfather clause. See ``card_lock``.
    """
    if not get_settings().subscription_lock_enabled or not on_a_paid_plan(org):
        return None
    status = (org.subscription_status or "").strip().lower()
    if status in NEVER_ACTIVATED:
        return Condition(
            SUBSCRIPTION_INCOMPLETE,
            "Your subscription was never completed, so no payment has been made. "
            "Finish setting up billing to unlock your dashboard.",
        )
    if status in PAST_DUE:
        ends = grace_ends_at(org)
        if ends is not None and (now or datetime.now(timezone.utc)) >= ends:
            return Condition(
                SUBSCRIPTION_PAST_DUE,
                "Your last payment failed and the grace period has ended. "
                "Update your payment method to unlock your dashboard — your "
                "audit evidence has kept recording throughout.",
            )
    return None


def grandfathered(org) -> bool:
    """True when this org must never be card-gated, whatever the flag says.

    Two independent exemptions, and both are needed:

    * **Created before the cutoff.** This is the rule that makes the flag safe to
      turn on. `card_on_file` is false on every row written before that column
      existed, so without a date exemption, enabling the gate locks out every
      customer the product already has — all at once, for a card they were never
      asked for. The cutoff is UNSET by default, which exempts everyone: a date
      baked into config is wrong the moment it passes.

    * **Has a Stripe subscription.** A paying customer demonstrably has a card at
      Stripe; `card_on_file` only tracks the newer $0-authorisation flow and so
      under-reports reality for anyone who subscribed before it existed. Locking
      a paying customer out over a flag that post-dates their payment would be
      absurd, and it stays true for anyone who subscribes through Checkout rather
      than the setup session.

    The date rule alone satisfies "flipping the flag locks nobody out today". The
    subscription rule is what keeps that true tomorrow.

    Note this exempts a `past_due` org from the CARD gate — it holds a
    subscription id — and that is still correct. The subscription lock is what
    answers for a failed payment, and it answers with a message about the payment
    rather than about a card.
    """
    if getattr(org, "stripe_subscription_id", None):
        return True
    raw = (get_settings().card_gate_grandfather_before or "").strip()
    if not raw:
        # No cutoff chosen, so nobody has decided which orgs are new enough to
        # gate. Exempt everyone rather than guess — the alternative is locking
        # out every existing customer on a config nobody set.
        return True
    created = getattr(org, "created_at", None)
    if created is None:
        # A row with no creation stamp predates the columns we could judge it by;
        # exempt it rather than lock somebody out on missing data.
        return True
    try:
        cutoff = datetime.fromisoformat(raw)
    except ValueError:
        log.warning("card_gate_grandfather_before is not an ISO datetime; "
                    "treating every org as grandfathered")
        return True
    return _aware(created) < _aware(cutoff)


def card_lock(org) -> Condition | None:
    """The card gate (P3 §4): this deployment requires a card and there is none."""
    if not get_settings().require_card_on_file or org.card_on_file:
        return None
    if grandfathered(org):
        return None
    return Condition(
        CARD_REQUIRED,
        "Add a payment method to unlock your dashboard. "
        "Your card is verified, not charged — the free tier stays free.",
    )


#: One message for both surfaces. The expiry is the same fact whether it is
#: refusing a capture or explaining a locked dashboard, and the action is the
#: same action, so saying it twice in two voices would only invite drift.
_EVALUATION_EXPIRED_MESSAGE = (
    "Your evaluation offer has ended. Upgrade to unlock your workspace and "
    "continue capturing events — everything you already recorded is kept, and "
    "stays verifiable."
)


def evaluation_lock(org, now: datetime | None = None) -> Condition | None:
    """The evaluation WINDOW has closed. This one locks (E1 · register #36).

    The owner's decision is a lock that requires an upgrade, not a quiet fall
    back to the free tier — the evaluation is the whole relationship, and when it
    ends the org is told to buy rather than silently demoted to something it
    never signed up for.

    WHY ``plan_tier`` STILL READS "premium" ON A LOCKED ORG
    ------------------------------------------------------
    Granting an offer sets ``plan_tier="premium"`` (``billing.py`` signup and
    redeem), and nothing here resets it, so a locked org still reads premium and
    still gets premium seat limits and anchor cadence. That looks like a bug and
    is not: "correcting" it to ``free`` would hand back the free monthly quota,
    ``capture_block`` would stop firing, and capture would resume — which is the
    opposite of the decision above. The lock is what ends the regime; the tier
    label is left alone until money changes it. ``_upgrade_existing_org`` in
    ``billing.py`` is the one place that rewrites both, together.
    """
    if not getattr(org, "evaluation_offer_id", None):
        return None
    ends = _aware(getattr(org, "evaluation_ends_at", None))
    if ends is None or (now or datetime.now(timezone.utc)) < ends:
        return None
    return Condition(EVALUATION_EXPIRED, _EVALUATION_EXPIRED_MESSAGE)


def dashboard_lock(org, now: datetime | None = None) -> Condition | None:
    """Everything that locks the dashboard, most actionable condition first.

    The evaluation one is reported first because it is the most specific: an
    expired-evaluation org reads ``plan_tier="premium"`` with no subscription at
    all, so any other gate that fired would describe it in terms of a plan it
    never bought.

    Then the money one, ahead of the card one. A failed or never-completed
    payment is a specific thing the customer must go and do; a missing card is a
    policy requirement of this deployment. An org in both states is better told
    "your payment failed" than "add a card" — the first is what is actually
    costing them access, and the card they would be asked for is very likely the
    one that just declined.
    """
    return evaluation_lock(org, now) or subscription_lock(org, now) or card_lock(org)


def capture_block(org, now: datetime, pending: int = 1) -> Condition | None:
    """The condition that stops this org capturing ``pending`` more events, if any.

    Ordered exactly as ``logs.py`` has always ordered it — trial → subscription →
    the evaluation pair, expiry before credits — because that order is asserted
    elsewhere and the message a customer gets should name the first thing that
    went wrong, not the last.

    ``pending`` is the size of the batch being asked about. It exists because the
    evaluation allowance is all-or-nothing per batch: eight credits spent of ten
    refuses a batch of five outright rather than part-writing it. The default of
    1 answers the question every OTHER caller is asking — "can this org capture
    at all?" — which is what ``/v1/billing/access`` and ``/v1/usage`` need.

    One ordering caveat, stated because it is a real (if unreachable) change:
    ``logs.py``'s monthly-quota gate (``credits_exhausted``) sits BETWEEN the
    subscription gate and the evaluation pair and is not in this function, so
    moving the evaluation pair in here puts it ahead of the quota. That is
    unobservable for every org the product creates — both grant paths set
    ``monthly_log_quota = NULL`` alongside the evaluation fields, so the quota
    gate is inert on exactly the orgs this pair applies to — and where staff
    tooling has set both by hand, naming the expired offer is the better answer
    anyway.

    Deliberately unchanged by D1: ``past_due`` and ``incomplete`` lock the
    dashboard and DO NOT appear here. See the module docstring.
    """
    if ((org.plan_tier or "").strip().lower() == "free"
            and org.trial_ends_at is not None
            and now >= _aware(org.trial_ends_at)):
        return Condition(
            TRIAL_EXPIRED,
            "Your 7-day trial has ended. Upgrade to continue capturing events.",
        )
    if on_a_paid_plan(org) and (org.subscription_status or "").strip().lower() in ENDED:
        return Condition(
            SUBSCRIPTION_INACTIVE,
            "Your subscription is not active. Update billing to continue capturing events.",
        )
    # `pending <= 0` means a batch that adds nothing new — every row was a
    # duplicate of one already in the chain. That has always been answered with
    # the original receipts rather than a 402, and re-answering it with money
    # talk would break idempotent retries for no gain: no credit is spent.
    if getattr(org, "evaluation_offer_id", None) and pending > 0:
        expired = evaluation_lock(org, now)
        if expired is not None:
            return expired
        limit = org.evaluation_credit_limit or 0
        if (org.evaluation_credits_used or 0) + pending > limit:
            return Condition(
                EVALUATION_CREDITS_EXHAUSTED,
                "This evaluation offer has no remaining event credits. "
                "Upgrade to continue capturing events.",
            )
    return None


def describe(org, now: datetime | None = None) -> Condition:
    """The single condition worth telling this org about, locking or not.

    ``/v1/billing/access`` reports this as ``reason`` alongside a separate
    ``locked`` boolean, and the two are not the same question. A ``past_due``
    org inside its grace window is NOT locked but very much wants to hear about
    it — that warning is the whole point of the window. So the dashboard reads
    ``locked`` to decide whether to block, and ``reason`` to decide what to say.

    Most severe first, so an org in two states is told about the one that
    actually stops it working.
    """
    now = now or datetime.now(timezone.utc)
    lock = dashboard_lock(org, now)          # subscription first, then the card
    if lock is not None:
        return lock
    status = (org.subscription_status or "").strip().lower()
    if get_settings().subscription_lock_enabled and on_a_paid_plan(org) and status in PAST_DUE:
        # Inside the grace window, or with no start recorded. Not locked yet.
        ends = grace_ends_at(org)
        when = (f" Update it by {ends.date().isoformat()} to keep your dashboard."
                if ends is not None else "")
        return Condition(
            SUBSCRIPTION_PAST_DUE,
            "Your last payment failed." + (when or " Update your payment method."),
        )
    blocked = capture_block(org, now)
    if blocked is not None:
        # Rename the capture-side code for the dashboard's vocabulary: the org
        # is not locked out, but new evidence is not being recorded, and that is
        # worth a banner.
        if blocked.reason == SUBSCRIPTION_INACTIVE:
            return Condition(SUBSCRIPTION_CANCELLED, blocked.message)
        return blocked
    return Condition(NONE, "No action needed.")
