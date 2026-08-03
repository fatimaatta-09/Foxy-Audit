"""Foxy Audit desktop — Usage & billing data shaping (D10).

Everything `#page-billing` decides before it paints, with no Qt in it. Ported
from the live web (foxy-audit-premium.html:1479-1539 + loadBilling / loadPlan /
openBillingPortal / openInvoice) and checked against the endpoints it reads:
`GET /v1/usage?days=30`, `GET /v1/invoices`, `GET /v1/billing/plan`.

**The rule this module exists under: every amount on screen is the server's.**

* Money is formatted from `amount_cents` with the invoice's OWN `currency`,
  scaled by that currency's real minor unit: 100 for most, 1 for the
  zero-decimal set (¥1000 is 1000, not 10.00) and 1000 for the three-decimal
  set (1.000 KWD is 1000, not 10.00). There is no default currency and no
  conversion, and a code we cannot scale exactly renders "—" rather than a
  number that is out by a factor of ten.
* The arithmetic is integer-only. `cents / 100` is a float, and a float is the
  wrong type for money: it is how a total ends up one cent away from the
  invoice the customer is looking at. `divmod` is exact.
* Nothing is totalled that the server did not total. The only sum here is
  token counts (a count, not money), and it is labelled as what it is.
* A missing value renders as "—", never as 0. On a billing page "0 credits
  used" and "we did not receive your usage" are very different sentences.
"""

from __future__ import annotations

from home_data import _num, dict_rows, thousands

#: Currencies whose smallest unit IS the major unit — Stripe sends 1000 for
#: ¥1000, not ¥10.00 (stripe.com/docs/currencies#zero-decimal). Dividing these
#: by 100 would understate every invoice by 100×, which is exactly the failure
#: "never assume USD" is about.
ZERO_DECIMAL = frozenset({
    "BIF", "CLP", "DJF", "GNF", "JPY", "KMF", "KRW", "MGA", "PYG", "RWF",
    "UGX", "VND", "VUV", "XAF", "XOF", "XPF",
})

#: Currencies quoted in thousandths — Stripe sends 1000 for 1.000 KWD
#: (stripe.com/docs/currencies#three-decimal). Dividing these by 100 renders
#: them TEN TIMES too large. `billing.py:544` stores whatever currency the
#: webhook sends with no allowlist and prices are set in the Stripe dashboard
#: outside this repo, so this is reachable, not theoretical.
THREE_DECIMAL = frozenset({"BHD", "JOD", "KWD", "OMR", "TND"})

#: How many minor units make one major unit, per currency.
_MINOR_UNITS = {**{c: 1 for c in ZERO_DECIMAL},
                **{c: 1000 for c in THREE_DECIMAL}}

#: Symbols for the currencies most Foxy invoices are in. Anything else prints
#: its ISO code — "1,234.56 SEK" is honest, a wrong symbol is not.
SYMBOLS = {"USD": "$", "EUR": "€", "GBP": "£", "JPY": "¥", "CAD": "CA$",
           "AUD": "A$", "CHF": "CHF ", "INR": "₹"}

MISSING = "—"


def money(cents, currency: str | None) -> str:
    """`$1,234.56` — an amount from the invoice's own cents and currency.

    Integer arithmetic throughout: `divmod` splits the minor units off exactly,
    where `cents / 100` introduces a float the moment it is called. The sign is
    handled before the split because `divmod(-1999, 100)` is `(-20, 1)` in
    Python, which would print a refund as -20.01.
    """
    if cents is None or currency is None:
        return MISSING
    try:
        amount = int(cents)
    except (TypeError, ValueError):
        return MISSING
    code = str(currency or "").upper()
    if not code.isalpha() or len(code) != 3:
        return MISSING          # not an ISO code; we cannot render it exactly
    sign = "-" if amount < 0 else ""
    amount = abs(amount)
    per_unit = _MINOR_UNITS.get(code, 100)
    if per_unit == 1:
        body = thousands(amount)
    else:
        whole, minor = divmod(amount, per_unit)
        body = f"{thousands(whole)}.{minor:0{len(str(per_unit)) - 1}d}"
    symbol = SYMBOLS.get(code)
    return f"{sign}{symbol}{body}" if symbol else f"{sign}{body} {code}"


def count(value) -> str:
    """A count for a stat tile — "—" when the server did not send one.

    The web renders `Number(undefined||0)` as "0" here. On a page about what a
    customer is being charged for, a fabricated zero is worse than a dash.
    """
    if value is None:
        return MISSING
    return thousands(int(_num(value)))


#: `dict.get` cannot tell "the server said null" from "the server did not say".
#: On this page those mean unlimited and unknown respectively, so the two are
#: kept apart explicitly wherever a NULL is meaningful.
ABSENT = "__absent__"


def optional_count(value, *, unlimited: str = "∞") -> str:
    """Like `count`, but a documented NULL means unlimited rather than unknown.

    `monthly_log_quota` / `remaining` are NULL when the plan is unlimited
    (account.py:69-71). A key that is simply missing is NOT that: promising
    unlimited capacity because a payload was short is the same class of
    invention as a fabricated zero. Callers pass `ABSENT` for that case.
    """
    if value is ABSENT:
        return MISSING
    return unlimited if value is None else count(value)


# ── the stat row ────────────────────────────────────────────────────────────
PLAN_TILES = ("Credits used", "Credits included", "Remaining", "Tokens · 30d")
EVAL_TILES = ("Evaluation credits used", "Evaluation credits",
              "Evaluation credits left", "Tokens · 30d")


def stat_row(quota: dict | None, days) -> list[tuple]:
    """(label, value, tone) ×4 — the web's four tiles (html:1486-1491).

    An evaluation org is on a different meter entirely (`quota.evaluation`),
    and the web relabels all three credit tiles for it. Showing plan credits
    to an org spending evaluation credits would be the right number for the
    wrong meter.
    """
    q = quota if isinstance(quota, dict) else {}
    tokens = token_total(days)
    ev = q.get("evaluation") if isinstance(q.get("evaluation"), dict) else None
    if ev:
        labels, values = EVAL_TILES, (ev.get("credits_used"),
                                      ev.get("credits_total"),
                                      ev.get("credits_remaining"))
        shown = [count(v) for v in values]
    else:
        labels = PLAN_TILES
        shown = [count(q.get("used_this_month", None)),
                 optional_count(q.get("monthly_log_quota", ABSENT)),
                 optional_count(q.get("remaining", ABSENT))]
    return [(labels[0], shown[0], "fox"), (labels[1], shown[1], None),
            (labels[2], shown[2], None), (labels[3], tokens, None)]


def token_total(days) -> str:
    """Tokens over the window. A sum of counts, not of money — and it says so
    on the tile ("Tokens · 30d") rather than presenting itself as a total the
    server computed."""
    rows = dict_rows(days)
    if not rows:
        return MISSING
    return thousands(sum(int(_num(d.get("tokens_sum"))) for d in rows))


# ── quota headroom ──────────────────────────────────────────────────────────
def headroom(quota: dict | None) -> tuple[int, str, str | None]:
    """(percent, label, tone) for the gauge (web html:3143-3145).

    Integer percent, computed the web's way, and only when there IS a quota —
    an unlimited plan has no headroom to draw and says so instead of showing a
    full or empty bar that means nothing.
    """
    q = quota if isinstance(quota, dict) else {}
    ev = q.get("evaluation") if isinstance(q.get("evaluation"), dict) else None
    if ev:
        total = int(_num(ev.get("credits_total")))
        used = int(_num(ev.get("credits_used")))
        pct = _percent(used, total) if total else 100
        left = count(ev.get("credits_remaining"))
        return pct, f"{left} of {count(total)} evaluation credits remaining", (
            "bad" if not ev.get("capture_available") else None)
    limit = q.get("monthly_log_quota", ABSENT)
    if limit is ABSENT:
        return 0, MISSING, None
    if limit is None:
        return 0, "unlimited plan", None
    total = int(_num(limit))
    used = int(_num(q.get("used_this_month")))
    pct = _percent(used, total) if total else 0
    return (pct, f"{pct}% of {thousands(total)}",
            "bad" if q.get("over_quota") else None)


def _percent(used: int, total: int) -> int:
    """Integer percent, rounded half-up and capped, matching the web's
    `Math.round(used/total*100)` — without the float. The module keeps its
    arithmetic integral throughout so there is one rule to check rather than
    "integers for money, floats for everything else"."""
    return min(100, (used * 100 + total // 2) // total)


def unlimited(quota: dict | None) -> bool:
    """True only when the server SAID unlimited.

    This read `q.get("monthly_log_quota") is None`, which is also what a failed
    load looks like — so the gauge painted a full unlimited bar over a panel
    whose own label said "—". A drawn bar is a claim like any other.
    """
    q = quota if isinstance(quota, dict) else {}
    return q.get("monthly_log_quota", ABSENT) is None and not q.get("evaluation")


# ── entitlements strip + the over-quota banner ──────────────────────────────
def entitlements(quota: dict | None) -> str:
    """The one-line summary under the stat row (web html:3148-3154). Empty when
    nothing is known — an entitlements line assembled out of defaults would
    describe a plan nobody is on."""
    q = quota if isinstance(quota, dict) else {}
    if not q:
        return ""
    parts = []
    # Each clause is built only from keys the payload actually carries. An
    # absent `credits_included` used to read "used of unlimited", and an absent
    # `seat_limit` as "custom seats" — descriptions of an entitlement nobody
    # sent. A shorter true line beats a complete invented one.
    if "credits_included" in q or "credits_used" in q:
        parts.append(f"Credits: {count(q.get('credits_used'))} used of "
                     f"{optional_count(q.get('credits_included', ABSENT), unlimited='unlimited')}")
    if "seat_limit" in q:
        parts.append("custom seats" if q.get("seat_limit") is None
                     else f"{count(q.get('active_seats'))} / "
                          f"{count(q.get('seat_limit'))} seats")
    if "api_key_limit" in q:
        parts.append("custom keys" if q.get("api_key_limit") is None
                     else f"{count(q.get('active_api_keys'))} / "
                          f"{count(q.get('api_key_limit'))} active keys")
    if q.get("trial_active") and q.get("trial_ends_at"):
        parts.append(f"trial ends {_date(q.get('trial_ends_at'))}")
    elif q.get("over_quota"):
        parts.append("capture paused")
    elif parts:
        parts.append("active")
    return " · ".join(parts)


OVER_QUOTA = ("Over your included credits. New captures are paused until the "
              "plan is upgraded or the next billing period begins.")
EVAL_EXHAUSTED = ("Evaluation credits are exhausted. New capture is disabled; "
                  "existing evidence remains available.")
EVAL_ENDED = ("Evaluation access has ended. New capture is disabled; existing "
              "evidence remains available.")


def over_quota_banner(quota: dict | None) -> str:
    """The warning, or "" — shown only on a real signal (web html:3155,3178)."""
    q = quota if isinstance(quota, dict) else {}
    ev = q.get("evaluation") if isinstance(q.get("evaluation"), dict) else None
    if ev:
        if not ev.get("active"):
            return EVAL_ENDED
        if int(_num(ev.get("credits_remaining"))) == 0:
            return EVAL_EXHAUSTED
        return ""
    return OVER_QUOTA if q.get("over_quota") else ""


# ── daily usage table ───────────────────────────────────────────────────────
USAGE_COLUMNS = ("Day", "Logs", "Tokens", "Breaches")
USAGE_EMPTY = ("No rollups yet",
               "Usage appears within about five minutes of SDK activity.")


def usage_rows(days) -> list[dict]:
    """Newest first, like the web's `.slice().reverse()` (html:3188)."""
    rows = []
    for d in reversed(dict_rows(days)):
        breaches = int(_num(d.get("breach_count")))
        rows.append({
            "day": _day(d.get("day")),
            "logs": count(d.get("logs_count")),
            "tokens": count(d.get("tokens_sum")),
            "breaches": thousands(breaches),
            "breached": breaches > 0,
        })
    return rows


def trend_points(days) -> list[dict]:
    return [{"label": _day(d.get("day")), "value": int(_num(d.get("logs_count")))}
            for d in dict_rows(days)]


# ── the plan card ───────────────────────────────────────────────────────────
def plan_view(data: dict | None) -> dict:
    """`GET /v1/billing/plan` → the four rows plus which buttons apply.

    The web's defaults ("free", "Free tier") apply to a payload that ARRIVED
    without those fields — that genuinely is the free tier. They must not
    apply to no payload at all: a failed call rendered "Free · Free tier" in
    the same card whose status strip said "Couldn't load".
    """
    p = data if isinstance(data, dict) else {}
    if not p:
        return {"tier": MISSING, "status": MISSING, "quota": MISSING,
                "trial_ends": "", "has_billing_account": False, "known": False}
    tier = str(p.get("plan_tier") or "") or "free"
    status = str(p.get("subscription_status") or "") or (
        "free tier" if tier == "free" else "active")
    quota = p.get("monthly_log_quota", ABSENT)
    return {
        "tier": tier.capitalize(),
        "status": status.capitalize(),
        "quota": optional_count(quota, unlimited="unlimited"),
        "trial_ends": _date(p.get("trial_ends_at")) if p.get("trial_ends_at") else "",
        "has_billing_account": bool(p.get("has_billing_account")),
        "known": bool(p),
    }


# ── the upgrade (E3 · #36) ──────────────────────────────────────────────────
# `Upgrade plan ↗` used to open https://foxyaudit.tech/pricing.html, which is
# the ANONYMOUS acquisition flow: it checks out by email and its Stripe metadata
# carries no org id, so `_handle_checkout` cannot match the workspace the
# customer is signed in to, misses the customer lookup (an org that never paid
# has no `stripe_customer_id`) and provisions a SECOND, empty organisation. The
# customer pays and finds none of their evidence.
#
# `POST /v1/billing/upgrade-session` is the authenticated twin E1 added: same
# Checkout, plus `foxy_org_id`. Admin-only, and under `/v1/billing/`, so it stays
# reachable from a locked workspace — which is the only kind an expired
# evaluation leaves behind. The web made the same swap in the same phase; these
# two surfaces have to tell one story about whose workspace got bought.


def plan_choices(data) -> list[dict]:
    """`GET /v1/billing/plans` → what this deployment can actually sell.

    A tier only appears when its Stripe price is configured server-side, so the
    desktop never offers a plan checkout cannot complete. NO PRICE is shaped
    here and none is available to shape: the amount lives in Stripe and is
    confirmed at checkout, and a number invented in a client would be exactly
    the fake data this project forbids. The quota is real — it comes from config.
    """
    out = []
    plans = (data or {}).get("plans") if isinstance(data, dict) else None
    for row in dict_rows(plans):
        tier = str(row.get("tier") or "").strip()
        if not tier:
            continue
        quota = row.get("monthly_log_quota", ABSENT)
        events = ("unlimited events" if quota is None
                  else MISSING if quota is ABSENT
                  else f"{count(quota)} events / month")
        once = " · one-time" if str(row.get("mode") or "") == "payment" else ""
        out.append({"tier": tier, "label": f"{tier.capitalize()} — {events}{once}"})
    return out


#: Shown when the server sells nothing from here. The same situation the web's
#: upgrade page describes, in the same words, because it is the same fact.
UPGRADE_NONE = ("This workspace is billed outside self-serve checkout. "
                "Email support and we will move you across.")

#: The dialog's own line. It says which workspace, because that is the only
#: thing this flow decides that the browser could not decide for itself.
UPGRADE_BLURB = ("Checkout opens in your browser and applies to the workspace "
                 "you are signed in to. No price is shown here — Stripe "
                 "confirms the amount before you pay.")


def upgrade_result(status: int | None) -> str:
    """What `POST /v1/billing/upgrade-session` refusing means, in words.

    Kept apart the way `portal_result` keeps its cases apart: 403 is a role the
    signed-in user does not have, 503 is a deployment with no Stripe keys, and
    422 is a plan this deployment does not sell. One message for all three would
    send somebody looking for a setting that is not theirs to change.
    """
    if status == 403:
        return "Buying a plan needs an admin account."
    if status == 503:
        return "Checkout isn't switched on for this workspace yet."
    if status == 422:
        return "That plan isn't on sale from here."
    if status is None or status == 0:
        return "Could not reach the server."
    return f"Could not start checkout (HTTP {status})."


def portal_result(status: int | None) -> str:
    """What POST /v1/billing/portal's failure means, in words.

    503 and 400 are different situations and the web keeps them apart: one is
    "this deployment has no Stripe keys", the other "this workspace has no
    Stripe customer yet". Collapsing them would send an admin looking for a
    setting that is not theirs to change.
    """
    if status == 503:
        return "Billing isn't enabled on this workspace yet."
    if status == 400:
        return "Start a paid plan first — there is no billing account to manage yet."
    if status == 403:
        return "Managing billing needs an admin account."
    if status is None or status == 0:
        return "Could not reach the server."
    return f"Could not open the billing portal (HTTP {status})."


def invoice_link_result(status: int | None) -> str:
    if status == 503:
        return "Billing isn't enabled on this workspace yet."
    if status == 404:
        return "No hosted invoice document is available for that row."
    if status == 403:
        return "Opening an invoice needs an admin account."
    if status is None or status == 0:
        return "Could not reach the server."
    return f"Could not open the invoice (HTTP {status})."


# ── invoice history ─────────────────────────────────────────────────────────
INVOICE_COLUMNS = ("Date", "Amount", "Status", "Period")
INVOICE_EMPTY = ("No invoices yet",
                 "Invoices appear after your first Stripe billing cycle.")

#: The web's note under the bar chart, quoted — it is a statement about what
#: Foxy stores, and softening it would misdescribe the product.
NO_LINE_ITEMS = ("Bars show invoice totals only — Foxy stores no per-line-item "
                 "breakdown.")

STATUS_TONES = {"paid": "ok", "open": "warn", "draft": "warn"}


def invoice_rows(data) -> list[dict]:
    rows = []
    for i in dict_rows(data):
        period = (f"{_date(i.get('period_start'))} – {_date(i.get('period_end'))}"
                  if i.get("period_start") else MISSING)
        rows.append({
            "id": str(i.get("id") or ""),
            "date": _date(i.get("created_at")),
            "amount": money(i.get("amount_cents"), i.get("currency")),
            "status": str(i.get("status") or "unknown"),
            "tone": STATUS_TONES.get(str(i.get("status") or ""), "bad"),
            "period": period,
        })
    return rows


def invoice_points(data) -> list[dict]:
    """Bars for the invoice chart, oldest first (web html:3210).

    The bar's `value` stays in CENTS. The web rounds to whole units for the
    bar height, which is fine for a picture but means the number behind the
    bar is not the invoice amount; keeping cents makes the tooltip's `sub` and
    the bar the same quantity.
    """
    points = []
    for i in reversed(dict_rows(data)):
        amount = i.get("amount_cents")
        if amount is None:
            continue                # no amount, no bar — never a zero-height lie
        points.append({"label": _date(i.get("created_at")),
                       "value": int(_num(amount)),
                       "sub": money(amount, i.get("currency"))})
    return points


# ── dates ───────────────────────────────────────────────────────────────────
def _date(iso) -> str:
    """Local calendar date, the web's `toLocaleDateString` (html:3129)."""
    if not iso:
        return MISSING
    from console_chrome import local_date
    return local_date(iso) or MISSING


def _day(value) -> str:
    """A rollup's `day` is a bare YYYY-MM-DD, not a timestamp (html:3128)."""
    text = str(value or "")
    if not text:
        return MISSING
    from console_chrome import local_date
    return local_date(f"{text}T00:00:00", short=True) or text
