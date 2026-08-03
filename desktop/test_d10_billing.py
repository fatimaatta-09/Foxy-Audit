"""D10 — Usage & billing: money, honest gaps, and who may press what.

The heavy tests here are about numbers, because this is the one page where a
wrong figure is a claim about what somebody is being charged:

* every amount comes from the server's `amount_cents` and the invoice's own
  `currency`, with integer arithmetic only;
* a missing value is a dash, never a zero;
* nothing is totalled that the server did not total;
* and no control appears that the endpoint behind it would refuse.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest
from PyQt6.QtWidgets import QApplication

import billing_data as bd

_HERE = Path(__file__).resolve().parent


# ══ money ═══════════════════════════════════════════════════════════════════
@pytest.mark.parametrize("cents,currency,expected", [
    (1999, "usd", "$19.99"),
    (100, "USD", "$1.00"),
    (5, "usd", "$0.05"),
    (0, "usd", "$0.00"),
    (123456789, "usd", "$1,234,567.89"),
    (1999, "eur", "€19.99"),
    (1999, "gbp", "£19.99"),
    (-2500, "usd", "-$25.00"),          # a refund, not "-25.-00"
    (199900, "sek", "1,999.00 SEK"),    # no symbol we can vouch for → ISO code
])
def test_money_is_the_servers_amount_in_the_servers_currency(cents, currency,
                                                             expected):
    assert bd.money(cents, currency) == expected


def test_a_three_decimal_currency_is_not_ten_times_too_large():
    """Stripe quotes KWD in thousandths: 1000 is 1.000 KWD, not 10.00. These
    fell through to divmod(_, 100) and rendered TEN TIMES too large — and
    `billing.py:544` stores whatever currency the webhook sends, with no
    allowlist, so this is reachable rather than theoretical."""
    assert bd.money(1000, "kwd") == "1.000 KWD"
    assert bd.money(1234567, "BHD") == "1,234.567 BHD"
    assert bd.money(-2000, "omr") == "-2.000 OMR"
    for code in bd.THREE_DECIMAL:
        assert bd.money(1000, code).startswith(("1.000", "-"))


def test_a_currency_we_cannot_scale_exactly_renders_no_number():
    """A number that is out by a factor of ten is worse than no number."""
    assert bd.money(1999, "") == bd.MISSING
    assert bd.money(1999, "US") == bd.MISSING        # not an ISO 4217 code
    assert bd.money(1999, "dollars") == bd.MISSING
    assert bd.money(1999, "u$d") == bd.MISSING


def test_a_zero_decimal_currency_is_not_divided_by_a_hundred():
    """Stripe sends 1000 for ¥1000 — the smallest unit IS the yen. Dividing
    these by 100 would understate every invoice by 100×, which is the whole
    reason "never assume USD" is a rule."""
    assert bd.money(1000, "jpy") == "¥1,000"
    assert bd.money(50000, "KRW") == "50,000 KRW"
    assert bd.money(1000, "usd") == "$10.00"     # …and the usual case is intact


def test_money_never_touches_a_float():
    """`cents / 100` is how a total lands a cent away from the invoice the
    customer is reading. Checked structurally: no true division and no float()
    anywhere in the module."""
    tree = ast.parse((_HERE / "billing_data.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        assert not isinstance(getattr(node, "op", None), ast.Div), \
            "true division in billing_data — money must stay integral"
        if isinstance(node, ast.Call):
            assert getattr(node.func, "id", None) != "float", "float() on money"
    # and the arithmetic that IS there is exact for the awkward cases
    for cents in (1, 9, 10, 99, 100, 101, 999, 1000, 100_000_003):
        whole, minor = divmod(cents, 100)
        assert bd.money(cents, "usd") == f"${whole:,}.{minor:02d}"


def test_a_missing_amount_is_a_dash_not_a_zero():
    assert bd.money(None, "usd") == bd.MISSING
    assert bd.money(1999, None) == bd.MISSING     # no currency ⇒ no claim
    assert bd.money("oops", "usd") == bd.MISSING


# ══ honest gaps ═════════════════════════════════════════════════════════════
def test_the_stat_row_shows_dashes_before_anything_is_known():
    """The web renders `Number(undefined||0)` here, i.e. "0". On a billing page
    "0 credits used" and "we have not heard from the server" are different
    sentences and must not look the same."""
    assert [v for _l, v, _t in bd.stat_row(None, None)] == [bd.MISSING] * 4


def test_unlimited_is_told_apart_from_unknown():
    """NULL means unlimited for `monthly_log_quota` (account.py:69) — a
    documented value, not a gap. `∞` is a claim; `—` is the absence of one."""
    row = bd.stat_row({"used_this_month": 12, "monthly_log_quota": None,
                       "remaining": None}, [])
    assert [v for _l, v, _t in row][:3] == ["12", "∞", "∞"]
    partial = bd.stat_row({"used_this_month": 12}, [])
    assert [v for _l, v, _t in partial][:3] == ["12", bd.MISSING, bd.MISSING]


def test_tokens_are_summed_but_labelled_as_a_count():
    days = [{"tokens_sum": 100}, {"tokens_sum": 250}, {}]
    assert bd.token_total(days) == "350"
    assert bd.token_total([]) == bd.MISSING       # nothing measured, not zero
    assert bd.stat_row(None, days)[3][0] == "Tokens · 30d"


def test_evaluation_credits_relabel_the_whole_row():
    """An evaluation org is on a different meter. Showing plan credits there
    would be the right number for the wrong thing."""
    quota = {"used_this_month": 900, "monthly_log_quota": 1000,
             "evaluation": {"active": True, "capture_available": True,
                            "credits_total": 500, "credits_used": 120,
                            "credits_remaining": 380}}
    row = bd.stat_row(quota, [])
    assert [l for l, _v, _t in row][:3] == list(bd.EVAL_TILES[:3])
    assert [v for _l, v, _t in row][:3] == ["120", "500", "380"]
    pct, label, _tone = bd.headroom(quota)
    assert pct == 24 and "380 of 500 evaluation credits" in label


# ══ headroom + banners ══════════════════════════════════════════════════════
def test_headroom_is_integer_and_capped():
    assert bd.headroom({"monthly_log_quota": 1000, "used_this_month": 250})[0] == 25
    assert bd.headroom({"monthly_log_quota": 100, "used_this_month": 250})[0] == 100
    assert bd.headroom({"monthly_log_quota": 0, "used_this_month": 5})[0] == 0
    assert bd.headroom({"monthly_log_quota": None})[1] == "unlimited plan"
    assert bd.headroom({})[1] == bd.MISSING       # nothing known, no gauge claim
    assert bd.headroom({"monthly_log_quota": 10, "used_this_month": 20,
                        "over_quota": True})[2] == "bad"


def test_the_over_quota_banner_needs_a_real_signal():
    assert bd.over_quota_banner(None) == ""
    assert bd.over_quota_banner({"over_quota": False}) == ""
    assert bd.over_quota_banner({"over_quota": True}) == bd.OVER_QUOTA
    ended = {"evaluation": {"active": False, "credits_remaining": 5}}
    assert bd.over_quota_banner(ended) == bd.EVAL_ENDED
    spent = {"evaluation": {"active": True, "credits_remaining": 0}}
    assert bd.over_quota_banner(spent) == bd.EVAL_EXHAUSTED
    fine = {"evaluation": {"active": True, "credits_remaining": 5}}
    assert bd.over_quota_banner(fine) == ""


def test_entitlements_only_mentions_what_the_payload_carried():
    """An absent `credits_included` read as "used of unlimited" and an absent
    `seat_limit` as "custom seats" — entitlements nobody granted. A shorter
    true line beats a complete invented one."""
    assert bd.entitlements(None) == "" and bd.entitlements({}) == ""
    thin = bd.entitlements({"plan_tier": "evaluation"})
    assert thin == ""                       # nothing known ⇒ nothing said
    seats_only = bd.entitlements({"seat_limit": None})
    assert seats_only == "custom seats · active"
    assert "unlimited" not in bd.entitlements({"credits_used": 4})


def test_entitlements_says_nothing_when_nothing_is_known():
    assert bd.entitlements(None) == "" and bd.entitlements({}) == ""
    text = bd.entitlements({"credits_used": 4, "credits_included": 10,
                            "seat_limit": 3, "active_seats": 1,
                            "api_key_limit": 2, "active_api_keys": 2})
    assert "4 used of 10" in text and "1 / 3 seats" in text
    assert "2 / 2 active keys" in text and text.endswith("active")


# ══ invoices ════════════════════════════════════════════════════════════════
INVOICES = [
    {"id": "b", "amount_cents": 4900, "currency": "eur", "status": "paid",
     "created_at": "2026-07-01T00:00:00Z", "period_start": "2026-06-01T00:00:00Z",
     "period_end": "2026-07-01T00:00:00Z"},
    {"id": "a", "amount_cents": 4900, "currency": "eur", "status": "open",
     "created_at": "2026-06-01T00:00:00Z"},
]


def test_invoice_rows_carry_the_servers_amount_and_status():
    rows = bd.invoice_rows(INVOICES)
    assert [r["amount"] for r in rows] == ["€49.00", "€49.00"]
    assert [r["tone"] for r in rows] == ["ok", "warn"]
    assert rows[1]["period"] == bd.MISSING        # no period ⇒ no invented one
    assert bd.invoice_rows(None) == []


def test_an_unknown_invoice_status_is_not_quietly_fine():
    row = bd.invoice_rows([{"id": "x", "status": "uncollectible"}])[0]
    assert row["tone"] == "bad" and row["status"] == "uncollectible"
    assert row["amount"] == bd.MISSING            # no amount_cents was sent


def test_invoice_bars_are_oldest_first_and_skip_amountless_rows():
    """A bar of height zero for an invoice with no amount would read as a
    £0.00 invoice, which is a number the server never sent."""
    points = bd.invoice_points(INVOICES + [{"id": "c", "currency": "eur"}])
    assert [p["value"] for p in points] == [4900, 4900]   # cents, not rounded
    assert points[0]["sub"] == "€49.00"
    assert len(points) == 2


def test_the_two_billing_failures_stay_apart():
    """503 is "this deployment has no Stripe keys"; 400 is "this workspace has
    no Stripe customer yet". Collapsing them sends an admin hunting for a
    setting that is not theirs to change."""
    assert "isn't enabled" in bd.portal_result(503)
    assert "paid plan" in bd.portal_result(400)
    assert bd.portal_result(503) != bd.portal_result(400)
    assert "admin" in bd.portal_result(403)
    assert "reach the server" in bd.portal_result(0)
    assert "isn't enabled" in bd.invoice_link_result(503)
    assert "No hosted invoice" in bd.invoice_link_result(404)


def test_nothing_claims_unlimited_just_because_a_call_failed():
    """`monthly_log_quota is None` means unlimited — and is also what an empty
    payload looks like. The gauge painted a full unlimited bar on a panel
    whose own label read "—"."""
    assert bd.unlimited({"monthly_log_quota": None}) is True
    assert bd.unlimited({}) is False
    assert bd.unlimited(None) is False
    assert bd.unlimited({"monthly_log_quota": 1000}) is False


def test_plan_view_defaults_only_where_the_web_does():
    """"free"/"Free tier" describe a payload that arrived without those
    fields. A payload that never arrived gets dashes: the card used to read
    "Free · Free tier" beside its own "Couldn't load"."""
    blank = bd.plan_view(None)
    assert blank["known"] is False
    assert [blank[k] for k in ("tier", "status", "quota")] == [bd.MISSING] * 3
    # …but a real answer with no tier IS the free tier
    assert bd.plan_view({"has_billing_account": False})["tier"] == "Free"
    view = bd.plan_view({"plan_tier": "growth", "monthly_log_quota": None,
                         "has_billing_account": True})
    assert view["tier"] == "Growth" and view["quota"] == "unlimited"
    assert view["status"] == "Active" and view["has_billing_account"] is True
    assert bd.plan_view({"plan_tier": "free"})["status"] == "Free tier"


# ══ the built page ══════════════════════════════════════════════════════════
@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture(scope="module")
def console(app, tmp_path_factory):
    from PyQt6.QtCore import QSettings
    from fox_settings import FoxSettings
    from foxy_client import MemorySecretStore
    from dashboard import DashboardWindow

    path = tmp_path_factory.mktemp("d10") / "console.ini"
    store = QSettings(str(path), QSettings.Format.IniFormat)
    win = DashboardWindow(settings=FoxSettings(store, MemorySecretStore()))
    yield win
    win.close()


def _as_admin(console, *, admin: bool = True, session: bool = True):
    from urllib.parse import urlsplit
    from fox_settings import FoxSettings
    from test_foxy_client import _inject_session
    console.client.http._jar.clear()
    if session:
        hosts = {urlsplit(console.settings.backend_url()).hostname,
                 urlsplit(FoxSettings().backend_url()).hostname}
        for host in filter(None, hosts):
            _inject_session(console.client.http, domain=host)
    console._role = "admin" if admin else "member"


USAGE = {"quota": {"plan_tier": "growth", "monthly_log_quota": 1000,
                   "used_this_month": 250, "remaining": 750,
                   "credits_used": 250, "credits_included": 1000,
                   "seat_limit": 5, "active_seats": 2,
                   "api_key_limit": 3, "active_api_keys": 1},
         "days": [{"day": "2026-07-27", "logs_count": 10, "tokens_sum": 900,
                   "breach_count": 2},
                  {"day": "2026-07-28", "logs_count": 4, "tokens_sum": 120,
                   "breach_count": 0}]}


def test_the_billing_page_builds(console):
    for attr in ("bil_tiles", "bil_entitlements", "bil_warning", "bil_gauge",
                 "bil_trend", "bil_usage_rows", "bil_plan_rows", "bil_manage",
                 "bil_upgrade", "bil_inv_chart", "bil_inv_rows",
                 "billing_scroll"):
        assert hasattr(console, attr), attr


def test_usage_fills_the_tiles_and_the_daily_table(console):
    console._on_billing_usage(USAGE)
    values = [t[1].text() for t in console.bil_tiles]
    assert values == ["250", "1,000", "750", "1,020"]
    assert console.bil_gauge_lbl.text() == "25% of 1,000"
    assert console.bil_warn_card.isHidden()          # not over quota
    assert not console.bil_ent_card.isHidden()
    assert console.bil_usage_rows.count() == 2
    assert console.bil_usage_empty.isHidden()


def test_a_failed_usage_call_claims_nothing(console):
    """Not "0 credits used" — that is a measurement we never made."""
    from panel_state import PanelState
    console._on_billing_usage(None, ok=False)
    assert [t[1].text() for t in console.bil_tiles] == [bd.MISSING] * 4
    assert console.bil_usage_empty.state() is PanelState.ERROR
    assert console.bil_ent_card.isHidden() and console.bil_warn_card.isHidden()
    assert console.bil_gauge_lbl.text() == bd.MISSING


def test_the_over_quota_banner_only_shows_when_over(console):
    console._on_billing_usage({"quota": {"monthly_log_quota": 10,
                                         "used_this_month": 40,
                                         "over_quota": True}, "days": []})
    assert not console.bil_warn_card.isHidden()
    assert console.bil_warning.text() == bd.OVER_QUOTA
    console._on_billing_usage(USAGE)
    assert console.bil_warn_card.isHidden()


def test_an_empty_usage_window_is_not_an_error(console):
    from panel_state import PanelState
    console._on_billing_usage({"quota": {"monthly_log_quota": 10}, "days": []})
    assert console.bil_usage_empty.state() is PanelState.EMPTY
    assert "No rollups yet" in console.bil_usage_empty.title.text()


def test_a_member_never_sees_a_button_that_would_403(console):
    """`POST /v1/billing/portal` and the invoice link are both admin-only. The
    web keys these off `has_billing_account` alone, so a member sees "Manage
    billing" and finds out by pressing it."""
    _as_admin(console, admin=False)
    console._on_billing_plan({"plan_tier": "growth", "has_billing_account": True})
    assert console.bil_manage.isHidden()
    assert console.bil_upgrade.isHidden()        # they DO have an account
    console._on_invoices(INVOICES)
    row = console.bil_inv_rows.itemAt(0).widget()
    assert not any(b.text() == "PDF ↗"
                   for b in row.findChildren(type(console.bil_manage)))

    _as_admin(console, admin=True)
    console._on_billing_plan({"plan_tier": "growth", "has_billing_account": True})
    assert not console.bil_manage.isHidden()
    console._on_invoices(INVOICES)
    row = console.bil_inv_rows.itemAt(0).widget()
    assert any(b.text() == "PDF ↗"
               for b in row.findChildren(type(console.bil_manage)))


def test_upgrade_shows_only_without_a_billing_account(console):
    _as_admin(console)
    console._on_billing_plan({"plan_tier": "free", "has_billing_account": False})
    assert not console.bil_upgrade.isHidden() and console.bil_manage.isHidden()
    # and neither button appears before the plan is known at all
    console._on_billing_plan(None, ok=False)
    assert console.bil_upgrade.isHidden() and console.bil_manage.isHidden()


def test_the_trial_row_appears_only_with_a_trial(console):
    console._on_billing_plan({"plan_tier": "growth"})
    assert console.bil_plan_rows["trial_ends"][0].isHidden()
    console._on_billing_plan({"plan_tier": "growth",
                              "trial_ends_at": "2026-08-10T00:00:00Z"})
    assert not console.bil_plan_rows["trial_ends"][0].isHidden()
    assert console.bil_plan_rows["trial_ends"][1].text() != bd.MISSING


def test_invoice_rows_render_the_amount_the_server_sent(console):
    _as_admin(console)
    console._on_invoices(INVOICES)
    assert console.bil_inv_rows.count() == 2
    texts = " ".join(
        lbl.text() for i in range(console.bil_inv_rows.count())
        for lbl in console.bil_inv_rows.itemAt(i).widget().findChildren(
            type(console.bil_gauge_lbl)))
    assert "€49.00" in texts and "$" not in texts     # never assumed USD


def test_the_plan_panel_has_all_three_states(console):
    from panel_state import ERROR_TITLE, PanelState
    _as_admin(console)
    console._on_billing_plan({"plan_tier": "growth", "has_billing_account": True})
    assert console.bil_plan_state.state() is PanelState.OK
    assert console.bil_plan_state.isHidden()

    console._on_billing_plan({})                 # answered, nothing on file
    assert console.bil_plan_state.state() is PanelState.EMPTY
    assert not console.bil_plan_state.isHidden()
    assert "No plan on file" in console.bil_plan_state.title.text()

    console._on_billing_plan(None, ok=False)     # never answered
    assert console.bil_plan_state.state() is PanelState.ERROR
    assert console.bil_plan_state.title.text() == ERROR_TITLE


def test_a_mid_session_promotion_reaches_the_buttons(console):
    """The role gates both billing side-doors and used to be read once, at
    sign-in — so a user promoted to admin kept a hidden "Manage billing" until
    they signed out and back in."""
    _as_admin(console, admin=False)
    console._on_billing_plan({"plan_tier": "growth", "has_billing_account": True})
    console._on_invoices(INVOICES)
    assert console.bil_manage.isHidden()

    console._on_billing_role({"role": "admin"})   # the per-visit /v1/auth/me
    assert not console.bil_manage.isHidden()
    row = console.bil_inv_rows.itemAt(0).widget()
    assert any(b.text() == "PDF ↗"
               for b in row.findChildren(type(console.bil_manage)))

    # …and a failed re-fetch is not evidence of a demotion
    console._on_billing_role(None)
    assert not console.bil_manage.isHidden()


def test_invoices_empty_and_error_say_different_things(console):
    from panel_state import ERROR_TITLE, PanelState
    console._on_invoices([])
    assert console.bil_inv_empty.state() is PanelState.EMPTY
    assert "No invoices yet" in console.bil_inv_empty.title.text()
    console._on_invoices(None, ok=False)
    assert console.bil_inv_empty.title.text() == ERROR_TITLE


# ══ E3 · the upgrade, and whose workspace it buys (#36) ═════════════════════
def test_no_desktop_surface_opens_the_anonymous_checkout():
    """`Upgrade plan ↗` used to open foxyaudit.tech/pricing.html. That page
    checks out ANONYMOUSLY: no org id reaches Stripe, so `_handle_checkout`
    cannot match the workspace the customer is signed in to, misses the customer
    lookup (an org that never paid has no `stripe_customer_id`) and provisions a
    SECOND, empty organisation. The customer pays, lands somewhere new, and
    their evidence stays behind a lock the money never touched.

    `POST /v1/billing/upgrade-session` is the one route that carries
    `foxy_org_id`. The prose in these modules names the wrong door in order to
    warn about it, so this reads the CALLS, not the comments."""
    calls, urls = set(), set()
    for name in ("dashboard.py", "billing_data.py", "billing_page.py"):
        tree = ast.parse((_HERE / name).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                if node.value.startswith("/v1/billing/"):
                    calls.add(node.value)
                # Real links only. The prose in these modules names the wrong
                # door in order to warn about it, and a docstring is not a link.
                if node.value.startswith("http") and "pricing" in node.value:
                    urls.add(node.value)
    assert "/v1/billing/checkout-session" not in calls, (
        "the desktop is buying through the anonymous, org-blind checkout (#36)")
    assert "/v1/billing/upgrade-session" in calls, "the desktop cannot upgrade at all"
    assert not urls, f"the desktop still hands a purchase to a public page: {urls}"


def test_plan_choices_offers_only_what_the_server_sells():
    """A tier appears only when the server listed it — it lists nothing it has
    no Stripe price for, so this is what stops the desktop offering a plan
    checkout would 422 on."""
    payload = {"current_tier": "free", "plans": [
        {"tier": "pro", "mode": "subscription", "monthly_log_quota": 50000},
        {"tier": "guardian", "mode": "payment", "monthly_log_quota": None},
    ]}
    choices = bd.plan_choices(payload)
    assert [c["tier"] for c in choices] == ["pro", "guardian"]
    assert choices[0]["label"] == "Pro — 50,000 events / month"
    assert choices[1]["label"] == "Guardian — unlimited events · one-time"
    # Nothing to sell, no payload, junk in the list: all say "nothing", never a
    # default tier the customer could then be charged for.
    for junk in (None, {}, {"plans": None}, {"plans": [{"tier": ""}, "x", None]}):
        assert bd.plan_choices(junk) == []


def test_no_price_is_invented_anywhere_in_the_upgrade():
    """The amount lives in Stripe and is confirmed at checkout. A number written
    in a client is exactly the fake data this project forbids — and here it
    would be a fake number about money."""
    choices = bd.plan_choices({"plans": [
        {"tier": "pro", "mode": "subscription", "monthly_log_quota": 50000}]})
    text = choices[0]["label"] + bd.UPGRADE_BLURB + bd.UPGRADE_NONE
    assert not re.search(r"[$£€]\s*\d|\bUSD\b|/\s*mo\b", text), text
    # A quota the payload never carried is a dash, not "unlimited": promising
    # capacity because a field was missing is the same class of invention.
    absent = bd.plan_choices({"plans": [{"tier": "pro"}]})
    assert absent[0]["label"] == f"Pro — {bd.MISSING}"


def test_the_upgrade_failures_stay_apart():
    """Three different situations with three different people who can fix them:
    a role, a deployment, and a plan. One message would send an admin looking
    for a setting that is not theirs to change."""
    seen = {bd.upgrade_result(s) for s in (403, 503, 422, 0, None, 500)}
    assert len(seen) == 5, seen          # 0 and None are the same situation
    assert "admin" in bd.upgrade_result(403)
    assert bd.upgrade_result(None) == "Could not reach the server."


def test_the_upgrade_button_now_takes_the_role_the_route_takes(console):
    """`/v1/billing/upgrade-session` is admin-only. While this button opened a
    public page it needed no role — and this page's own rule is that a control
    which cannot work is worse than no control."""
    _as_admin(console, admin=False)
    console._on_billing_plan({"plan_tier": "free", "has_billing_account": False})
    assert console.bil_upgrade.isHidden(), "a member is offered an admin-only checkout"
    _as_admin(console, admin=True)
    console._on_billing_plan({"plan_tier": "free", "has_billing_account": False})
    assert not console.bil_upgrade.isHidden()


def test_the_plan_dialog_carries_the_tier_and_not_the_label(app):
    """The label is for the human; `plan` on the wire is the tier the server
    named. Sending the label would 422 every time."""
    from billing_page import PlanDialog
    dialog = PlanDialog(bd.plan_choices({"plans": [
        {"tier": "pro", "mode": "subscription", "monthly_log_quota": 50000},
        {"tier": "max", "mode": "subscription", "monthly_log_quota": None}]}))
    try:
        assert dialog.plans.count() == 2
        assert dialog.tier() == "pro"
        dialog.plans.setCurrentIndex(1)
        assert dialog.tier() == "max"
    finally:
        dialog.deleteLater()
