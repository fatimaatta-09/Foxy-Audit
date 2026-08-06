"""Paddle Billing — checkout creation, signature verification, status mapping.

M2. Built DIRECTLY beside the Stripe code, not behind a shared abstraction: M1
proposed a provider seam and was cut, because an abstraction seam's job is to
hold two live implementations and Stripe has never run here — no key in the
environment, no payment ever taken. Stripe gets deleted once Paddle takes a real
payment; until then the two sit side by side and neither knows about the other.

No SDK dependency. Paddle's REST API is three fields over HTTPS, and
``openai_judge.py`` already set the precedent of using stdlib ``urllib`` rather
than adding a vendor package to the image.

EVERY DETAIL BELOW WAS READ OFF PADDLE'S LIVE DOCUMENTATION, not from memory.
Where the docs disagreed with the brief, the docs won and the report says so.

WHAT THE DOCS ACTUALLY SAY
--------------------------
* Signature header ``Paddle-Signature``, format ``ts=<unix>;h1=<hex>``. The
  signed payload is ``f"{ts}:{raw_body}"`` — HMAC-SHA256 under the notification
  destination's secret. Verified over the RAW BYTES: re-serialising parsed JSON
  changes the bytes and the digest can never match.
* The envelope is ``{event_id, event_type, occurred_at, notification_id, data}``.
  **``event_id`` (``evt_…``) is unique per EVENT; ``notification_id`` (``ntf_…``)
  is unique per DELIVERY ATTEMPT.** Paddle guarantees at-least-once delivery and
  retries whenever the endpoint does not answer 200 within five seconds, so
  ``event_id`` is the only correct idempotency key.
* ``custom_data`` set on a transaction is **copied by Paddle onto the
  subscription** it creates, and onto later transactions from that subscription.
  That is what keeps ``foxy_org_id`` reachable on every subsequent event.
* Subscription statuses are exactly ``trialing`` · ``active`` · ``past_due`` ·
  ``paused`` · ``canceled``.

SECRETS NEVER LEAVE THIS MODULE. The API key and the webhook secret are read
from settings at call time and never logged, never returned, never serialised.
Exception TYPES are logged, never ``str(exc)`` — a urllib error can carry the
request URL, and the API key travels in a header on that request.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

from .config import get_settings

log = logging.getLogger("foxy.paddle")

_SANDBOX_API = "https://sandbox-api.paddle.com"
_LIVE_API = "https://api.paddle.com"

#: plan tier → the config attribute holding that tier's Paddle price id.
#: Only the two tiers M2 sells. Portal, cancel, invoices and the one-time
#: "guardian" tier are M3.
_PRICE_ATTRS = {
    "pro": "paddle_price_pro",
    "max": "paddle_price_max",
}

# ── transaction origin: who decided this charge (M3b · register #97) ────────
# Verbatim from Paddle's transaction object schema — "Describes how this
# transaction was created" — read off the get-transaction API reference. All six
# values are listed so the next reader does not have to go and find them again:
ORIGIN_API = "api"                    # created via the Paddle API — OUR first purchase
ORIGIN_WEB = "web"                    # created by Paddle.js for a checkout
ORIGIN_SUBSCRIPTION_UPDATE = "subscription_update"          # a change, billed now
ORIGIN_SUBSCRIPTION_RECURRING = "subscription_recurring"    # A RENEWAL
ORIGIN_SUBSCRIPTION_CHARGE = "subscription_charge"          # one-time charge on a sub
ORIGIN_SUBSCRIPTION_PAYMENT_METHOD_CHANGE = "subscription_payment_method_change"

#: The origins that mean SOMEBODY CHOSE THIS PLAN, so the tier the price implies
#: is an instruction rather than an echo of an old one.
#:
#: `api` is what this product's own first purchases carry: the backend creates
#: the transaction with `POST /transactions` and Paddle.js only opens it, so it
#: is API-created even though a human clicked Buy. `web` covers a checkout that
#: creates its own transaction. `subscription_update` is a real plan change
#: billed immediately.
#:
#: Everything else — a renewal, a one-time charge, a $0 payment-method update —
#: re-states a decision already made, and must never overwrite one made since.
PLAN_CHOOSING_ORIGINS = frozenset({
    ORIGIN_API, ORIGIN_WEB, ORIGIN_SUBSCRIPTION_UPDATE,
})


def origin_of(data: dict) -> str:
    """The transaction's `origin`, lowercased. "" when the field is absent."""
    return str((data or {}).get("origin") or "").strip().lower()


def chooses_a_plan(data: dict) -> bool:
    """Whether this transaction may SET the org's tier.

    True for a first purchase or a deliberate change; False for a renewal, which
    only re-charges a decision already recorded.

    AN ABSENT `origin` COUNTS AS CHOOSING, and that direction is deliberate.
    Paddle always sends the field, so the fallback is only reachable on a payload
    that is malformed or predates it. Of the two ways to be wrong, failing to
    upgrade somebody who has just paid is far worse than re-applying a tier that
    already matches — and the second only bites once staff have downgraded by
    hand, which is a state a human can see and correct.
    """
    origin = origin_of(data)
    return True if not origin else origin in PLAN_CHOOSING_ORIGINS


#: Paddle's subscription status → the vocabulary already stored in
#: `organizations.subscription_status` and read by `billing_state`.
#:
#: THIS MAPPING IS NOT ALLOWED TO INVENT A NEW STORED VALUE. Three surfaces and
#: `desktop/foxy_client.py` match the existing strings, several verbatim as
#: constants, so a new one would be a cross-surface break rather than a rename.
#:
#: `trialing` → `active` mirrors what the Stripe path already stores.
#: `paused` → `past_due` is the one genuine judgement here, and it is
#: conservative in the direction that protects the customer: a paused
#: subscription bills nothing, so it is not `active`; but it has not ended and
#: the customer has not left, so calling it `cancelled` would stop capture — and
#: evidence, unlike a payment, cannot be recreated afterwards. `past_due` locks
#: the dashboard after the grace window while capture keeps running, which is
#: exactly the intended treatment of "not paying right now, not gone".
_STATUS_MAP = {
    "active": "active",
    "trialing": "active",
    "past_due": "past_due",
    "paused": "past_due",
    "canceled": "cancelled",
}


def configured() -> bool:
    """True when this deployment can call Paddle at all.

    The API key alone is the switch. `billing.upgrade_session` branches on this,
    so an unset key means every Paddle path is dark and the Stripe branch runs
    exactly as it does today.
    """
    return bool(get_settings().paddle_api_key)


def webhook_configured() -> bool:
    """True when an inbound webhook signature can be verified.

    Separate from `configured()` on purpose — they are different settings and a
    deployment can legitimately hold one without the other. Answering the webhook
    with the API key's state would report the wrong fault.
    """
    return bool(get_settings().paddle_webhook_secret)


def api_base() -> str:
    """Sandbox unless explicitly told otherwise.

    Defaulting to sandbox is the safe direction: a misconfigured `PADDLE_ENV`
    fails against test money rather than real money.
    """
    return _LIVE_API if get_settings().paddle_env.strip().lower() == "live" else _SANDBOX_API


def price_for(plan_tier: str) -> str:
    """The configured Paddle price id for a tier, or "" if it cannot be sold."""
    attr = _PRICE_ATTRS.get((plan_tier or "").strip().lower())
    return (getattr(get_settings(), attr, "") or "").strip() if attr else ""


def sellable_plans() -> list[str]:
    """Tiers this deployment has a Paddle price for. Honest empty list when none."""
    return [tier for tier in _PRICE_ATTRS if price_for(tier)]


def map_status(paddle_status: str | None) -> str | None:
    """Paddle's subscription status → our stored vocabulary. None when unknown.

    An unknown status returns None and the caller leaves the stored value alone.
    Paddle can add a status; guessing what an unrecognised one means about access
    is how a customer gets locked out by a vendor's release note.
    """
    return _STATUS_MAP.get((paddle_status or "").strip().lower())


def _request(method: str, path: str, body: dict | None = None) -> dict:
    """One authenticated JSON call to Paddle. Raises on any non-2xx.

    Never logs the key, the header, or `str(exc)`: a `urllib` error renders the
    request URL, and `HTTPError.read()` can echo request context. Only the
    exception type and the status code are considered safe to record.
    """
    settings = get_settings()
    payload = json.dumps(body or {}).encode("utf-8")
    req = urllib.request.Request(
        f"{api_base()}{path}", data=payload, method=method,
        headers={
            "Authorization": f"Bearer {settings.paddle_api_key}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        log.warning("paddle %s %s failed with HTTP %s", method, path, exc.code)
        raise
    except Exception as exc:                       # noqa: BLE001
        log.warning("paddle %s %s failed: %s", method, path, type(exc).__name__)
        raise


def create_checkout(org_id: str | None, plan_tier: str, *,
                    email: str | None = None) -> str:
    """Create a Paddle transaction and return the hosted checkout URL.

    ``custom_data.foxy_org_id`` is the whole point: it is the same contract
    ``foxy_org_id`` has in Stripe's Checkout metadata, and it is what lets the
    webhook upgrade THIS workspace instead of provisioning a second empty one
    (register #36). Paddle copies it onto the subscription it creates, so every
    later subscription event carries it too.

    ``org_id=None`` is the ANONYMOUS sale-page purchase: no workspace exists yet,
    so the key is OMITTED rather than sent empty. The webhook branches on its
    absence to provision instead of upgrade, and an empty string would look like
    a present-but-unresolvable org and be recorded as a failure.

    ⚠ REQUIRES A DEFAULT PAYMENT LINK configured in the Paddle dashboard
    (Checkout → Checkout settings → Default payment link). Paddle refuses to
    create ANY transaction without one, and the returned `checkout.url` is that
    link plus `?_ptxn=<transaction id>`. We deliberately do not override it
    per-transaction: the override has to be an approved domain that loads
    Paddle.js, and this application's dashboard does not.
    """
    price_id = price_for(plan_tier)
    if not price_id:
        raise ValueError(f"no Paddle price configured for plan {plan_tier!r}")
    custom: dict = {"foxy_plan": plan_tier}
    if org_id:
        custom["foxy_org_id"] = str(org_id)
    body: dict = {
        "items": [{"price_id": price_id, "quantity": 1}],
        "custom_data": custom,
    }
    if email:
        # Prefills the buyer's email at checkout. Paddle creates the customer
        # record itself as part of the checkout journey, so we deliberately do
        # NOT pre-create one — that would fork a second customer for anyone who
        # already exists under the same address.
        body["customer"] = {"email": email}
    data = (_request("POST", "/transactions", body) or {}).get("data") or {}
    url = ((data.get("checkout") or {}).get("url") or "").strip()
    if not url:
        # Almost always the missing default payment link. Say so: the alternative
        # is an operator staring at an empty 502 for an hour.
        raise RuntimeError(
            "Paddle returned no checkout URL — is a default payment link set "
            "under Checkout settings?"
        )
    return url


# ── inbound ─────────────────────────────────────────────────────────────────

class SignatureError(Exception):
    """The Paddle-Signature header is missing, malformed, stale, or wrong."""


def _parse_signature_header(header: str) -> tuple[int, str]:
    """`ts=<unix>;h1=<hex>` → (ts, h1). Raises SignatureError on anything else."""
    ts_raw = sig = ""
    for part in (header or "").split(";"):
        key, _, value = part.partition("=")
        if key.strip() == "ts":
            ts_raw = value.strip()
        elif key.strip() == "h1":
            sig = value.strip()
    if not ts_raw or not sig:
        raise SignatureError("malformed Paddle-Signature header")
    try:
        return int(ts_raw), sig
    except ValueError:
        raise SignatureError("malformed Paddle-Signature timestamp")


def verify_signature(body: bytes, header: str, *, now: float | None = None) -> None:
    """Verify a Paddle webhook signature. Returns None; raises SignatureError.

    ``body`` MUST be the raw bytes as received. Re-serialising parsed JSON
    reorders keys and changes whitespace, and the digest will never match again —
    a failure that looks exactly like an attack.

    The comparison is ``hmac.compare_digest``. Not a style preference: there is a
    published advisory against a Paddle library for using ``==`` here
    (GHSA-mjgf-xj26-9qf9), because a byte-at-a-time comparison leaks the expected
    digest through timing and lets an attacker forge a signature one nibble at a
    time. Constant time or nothing.

    A stale timestamp is rejected before the digest is computed, bounding replay
    of a captured-but-valid request. See `paddle_signature_tolerance_seconds`
    for why the window is 300s rather than Paddle's own 5.
    """
    secret = get_settings().paddle_webhook_secret
    if not secret:
        raise SignatureError("paddle webhook secret is not configured")
    ts, supplied = _parse_signature_header(header)

    tolerance = max(0, int(get_settings().paddle_signature_tolerance_seconds))
    if abs((now if now is not None else time.time()) - ts) > tolerance:
        raise SignatureError("Paddle-Signature timestamp is outside the tolerance window")

    signed_payload = str(ts).encode("ascii") + b":" + body
    expected = hmac.new(secret.encode("utf-8"), signed_payload, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, supplied):
        raise SignatureError("Paddle-Signature does not match")


def parse_event(body: bytes) -> dict:
    """The verified envelope as a plain dict. Call only AFTER verify_signature."""
    return json.loads(body.decode("utf-8"))


def org_id_from(data: dict) -> str | None:
    """The workspace this event belongs to, from Paddle's custom_data.

    Present on transactions and — because Paddle copies custom_data onto the
    subscription it creates — on subscription events too.
    """
    custom = (data or {}).get("custom_data") or {}
    value = str(custom.get("foxy_org_id") or "").strip()
    return value or None


def plan_from(data: dict) -> str | None:
    """Which tier was bought, resolved from the PRICE ID rather than trusted.

    `custom_data.foxy_plan` is only a fallback. The price id is what Paddle
    actually charged for, and it is the one field a buyer editing a checkout
    cannot talk us out of — so an org can never be granted a tier it did not pay
    for by tampering with custom data.
    """
    for item in (data or {}).get("items") or []:
        price_id = ((item or {}).get("price") or {}).get("id") or (item or {}).get("price_id")
        if not price_id:
            continue
        for tier in _PRICE_ATTRS:
            if price_id == price_for(tier):
                return tier
    return None


def occurred_at(envelope: dict) -> datetime | None:
    """The event's own RFC-3339 timestamp, if it parses."""
    raw = (envelope or {}).get("occurred_at")
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None
