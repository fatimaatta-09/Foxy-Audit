"""Foxy Audit desktop — Home page data shaping (D4).

Everything the Home page *decides* before it paints, with no Qt in it: the
greeting fallback chain, coverage status wording and chip values, the usage
trend metric map, hero delta maths, grading-donut slices, and the activity
merge. Ported from the live web loaders so the two products agree:

    greeting                foxy-audit-premium.html:2253-2255
    capture coverage        :2361-2412  (loadCoverage)
    usage trend metrics     :3568-3578  (METRICS / renderTrend / setTrendMetric)
    hero spark + delta      :3579-3591  (renderHero)
    grading donut           :3597-3604
    onboarding stepper      :3439-3459  (loadFirstRun)
    activity feed           :3901-3955  (LBL / kindFor / merge / renderFeed)

Keeping it Qt-free means the page's judgement can be tested without a screen —
and the widgets stay thin enough to be obviously correct.
"""

from __future__ import annotations

from datetime import datetime, timezone

# ── usage trend (web METRICS, html:3568) ────────────────────────────────────
TREND_METRICS: dict[str, dict] = {
    "logs":     {"key": "logs_count",   "label": "Logs",     "tone": "fox"},
    "tokens":   {"key": "tokens_sum",   "label": "Tokens",   "tone": "blue"},
    "breaches": {"key": "breach_count", "label": "Breaches", "tone": "bad"},
}

# ── activity feed labels (web LBL, html:3902-3906) — all 13 ─────────────────
ACTION_LABELS: dict[str, str] = {
    "key.create": "Created an API key",
    "key.revoke": "Revoked an API key",
    "key.regenerate": "Regenerated the API key",
    "policy.update": "Updated the policy",
    "member.role_change": "Changed a member role",
    "member.enable": "Re-enabled a member",
    "member.disable": "Disabled a member",
    "mfa.enable": "Enabled two-factor",
    "mfa.disable": "Disabled two-factor",
    "profile.update": "Updated the profile",
    "preferences.update": "Updated preferences",
    "session.revoke": "Revoked a device session",
    "export.create": "Started a compliance export",
}

ACTIVITY_LIMIT = 30            # renderFeed slices to 30
ACTIVITY_TICK_SECONDS = 30     # the web re-ticks [data-ago] every 30 s


def dict_rows(value) -> list[dict]:
    """The dict items of `value`, skipping anything else.

    Most list payloads come back through a Pydantic `response_model`, so their
    items are guaranteed dicts. `/v1/analytics/threats` is the exception — it
    has no response_model at all (backend/app/routers/analytics.py:25) and
    hand-builds its rows — and the Threats page is built entirely on it. A
    single stray non-dict row there would take the page down with an
    AttributeError, so the shaping skips what it cannot read rather than
    trusting the wire.
    """
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _num(value) -> float:
    """The web's `+v||0`: unparseable is zero, never an exception."""
    try:
        out = float(value)
    except (TypeError, ValueError):
        return 0.0
    return 0.0 if out != out else out


def thousands(value) -> str:
    """The web's money() — 1234 → '1,234'."""
    return f"{int(_num(value)):,}"


def fmt_pct(value) -> str:
    """The web's fmtPct (html:2220): `v==null?'—':v+'%'`.

    Note it does NOT round — a clean rate of 97.4 reads "97.4%", and the
    backend already rounds to one decimal. Rounding here would quietly
    disagree with the website about the same number."""
    if value is None:
        return "—"
    text = f"{float(value):g}" if isinstance(value, (int, float)) else str(value)
    return f"{text}%"


def fmt_verdict(seconds) -> str:
    """The web's fmtVerdict (html:2217):
    `sec==null?'—':(sec<1?Math.round(sec*1000)+'ms':sec.toFixed(1)+'s')`.

    Sub-second latency is the good case and the one people care about, so it
    is shown in whole milliseconds rather than as "0.4s". Zero is not null —
    it renders "0ms", because a measured zero is a measurement."""
    if seconds is None:
        return "—"
    value = _num(seconds)
    if value < 1:
        return f"{round(value * 1000)}ms"
    return f"{value:.1f}s"


def greeting_name(me: dict | None) -> str:
    """First name → email localpart → 'there' (web:2254).

    Capitalised the same way, so the desktop greets you exactly as the web
    does rather than inventing its own politeness."""
    me = me or {}
    full = (me.get("full_name") or "").strip()
    if full:
        first = full.split()[0]
    else:
        email = (me.get("email") or "").strip()
        first = email.split("@")[0] if email else ""
    return (first[0].upper() + first[1:]) if first else "there"


def head_subtitle(stats: dict | None) -> str:
    """'N events logged · M pending grading' (web:2257). Honest when unknown."""
    if not isinstance(stats, dict) or stats.get("total_logged") is None:
        return "—"          # "0 events logged" is a measurement we don't have
    grading = stats.get("grading") or {}
    pending = int(_num(grading.get("pending")))
    return f"{thousands(stats.get('total_logged'))} events logged · {pending} pending grading"


# ── capture coverage (web loadCoverage, html:2370-2408) ─────────────────────
def coverage_view(data: dict | None) -> dict:
    """Shape /v1/coverage into everything the card shows.

    `pct` is None when nothing has been reported yet — the card must then say
    so rather than printing a confident 0%, which would read as "we observed
    nothing of everything" instead of "there is nothing to observe"."""
    if not isinstance(data, dict):
        return {
            "status": "unavailable", "status_tone": "warn",
            "status_label": "unavailable",
            "message": "Capture coverage could not be loaded. No conclusion is shown.",
            "pct": None, "pct_text": "—", "gauge_tone": "mute",
            "note": "", "chips": [], "clients": [], "ok": False,
        }
    state = data.get("status") or "unknown"
    label = {"verified": "observed coverage verified",
             "partial": "partial coverage"}.get(state, "coverage unknown")
    chain = {"verified": "chain verified",
             "failed": "chain check failed"}.get(
                 data.get("chain_verification"), "chain check not run")
    total = int(_num(data.get("total_events")))
    missing = int(_num(data.get("missing_events")))
    denom = total + missing
    pct = round(total / denom * 100) if denom > 0 else None
    if pct is None:
        note = "No client-reported events yet."
        tone = "mute"
    else:
        tail = f" — {thousands(missing)} missing." if missing > 0 else "."
        note = f"of client-reported events were observed by Foxy{tail}"
        tone = "ok" if pct >= 95 else "warn" if pct >= 80 else "bad"
    return {
        "status": state,
        "status_tone": "ok" if state == "verified" else "warn",
        "status_label": label,
        "message": f"{data.get('message') or 'Coverage is unavailable.'} {chain}.",
        "pct": pct,
        "pct_text": "—" if pct is None else f"{pct}%",
        "gauge_tone": tone,
        "note": note,
        "chips": [
            ("Events observed", thousands(total), False),
            ("SDK clients", thousands(data.get("instrumented_clients")), False),
            ("Missing events", thousands(missing), True),      # danger tone
            ("No client identity",
             thousands(data.get("events_without_client_identity")), False),
        ],
        "clients": [coverage_client_row(c) for c in dict_rows(data.get("clients"))],
        "ok": True,
    }


def coverage_client_row(client: dict) -> dict:
    """One per-client continuity row: id, events, seq range + gaps, status."""
    gaps = client.get("missing_ranges") or []
    dups = client.get("duplicate_client_sequences") or []
    gap_text = ", ".join(
        str(g.get("start")) if g.get("start") == g.get("end")
        else f"{g.get('start')}–{g.get('end')}" for g in gaps)
    first, last = client.get("first_client_seq"), client.get("last_client_seq")
    seq = f"{first}–{last}" if first is not None and last is not None else "—"
    if gaps:
        status, tone = "gap", "bad"
    elif dups:
        status, tone = "dup seq", "warn"
    else:
        status, tone = "continuous", "ok"
    return {"client_id": str(client.get("client_id") or "—"),
            "events": thousands(client.get("events")),
            "seq": seq, "gap_text": gap_text,
            "status": status, "tone": tone}


# ── hero (web renderHero, html:3579-3591) ───────────────────────────────────
def hero_spark(days: list | None, window: int = 30) -> list[dict]:
    """Last `window` days of log volume, as FoxChart rows."""
    days = days if isinstance(days, list) else []
    return [{"value": _num(d.get("logs_count"))} for d in days[-window:]]


def hero_delta(days: list | None) -> str:
    """'▲ +12% vs 7-day avg' — today against the mean of the prior seven.

    Empty string when there is no baseline: a percentage against zero history
    would be a fabricated comparison."""
    days = days if isinstance(days, list) else []
    if len(days) < 2:
        return ""
    last = _num(days[-1].get("logs_count"))
    prior = days[-8:-1]
    base = sum(_num(d.get("logs_count")) for d in prior) / len(prior) if prior else 0
    if base <= 0:
        return ""
    pct = round((last - base) / base * 100)
    return f"{'▲ +' if pct >= 0 else '▼ '}{pct}% vs 7-day avg"


def trend_rows(days: list | None, metric: str) -> tuple[list[dict], dict]:
    """(chart rows, metric spec) for the usage trend switcher."""
    spec = TREND_METRICS.get(metric) or TREND_METRICS["logs"]
    days = days if isinstance(days, list) else []
    return ([{"label": d.get("day"), "value": _num(d.get(spec["key"]))}
             for d in days], spec)


def grading_slices(stats: dict | None) -> tuple[list[dict], int]:
    """(donut slices, total). Total 0 means the donut shows its empty state —
    'Nothing graded yet' — rather than an empty ring."""
    grading = (stats or {}).get("grading") or {}
    slices = [
        {"label": "Graded", "value": _num(grading.get("graded")), "tone": "ok"},
        {"label": "Pending", "value": _num(grading.get("pending")), "tone": "warn"},
        {"label": "In progress", "value": _num(grading.get("in_progress")), "tone": "blue"},
        {"label": "Failed", "value": _num(grading.get("failed")), "tone": "bad"},
    ]
    return slices, int(sum(s["value"] for s in slices))


#: The web's Home stat row, in order (html:1029-1034). Tones follow the site:
#: tile 0 is `.v fox`, tile 1 is `.v danger`, tiles 2 and 3 are unmodified.
STAT_LABELS = ("Breaches stopped", "Open alerts", "Clean rate",
               "Time to verdict")


def stat_row(stats: dict | None, open_alerts: int | None = None) -> list[tuple]:
    """The four home KPIs: (label, value, tone) — the web's row, in its order.

    Tiles 0/2/3 come from GET /v1/stats and tile 1 from
    GET /v1/analytics/threats, which is exactly how the site splits them: its
    loadStats leaves dv[1] alone and loadThreats fills it (html:2228, 3350).
    Open alerts (live) and breaches (all-time) are different measurements, so
    one is never used to stand in for the other.

    Every tile is an em dash until its own source answers. The site ships "0"
    in the markup for tiles 0 and 1; here an unmeasured count is not shown as
    zero.
    """
    stats = stats if isinstance(stats, dict) else {}
    breaches = stats.get("breaches")
    clean = stats.get("clean_rate")
    ttv = stats.get("avg_seconds_to_verdict")
    return [
        (STAT_LABELS[0], "—" if breaches is None else thousands(breaches), "fox"),
        (STAT_LABELS[1],
         "—" if open_alerts is None else thousands(open_alerts), "bad"),
        (STAT_LABELS[2], fmt_pct(clean), None),
        (STAT_LABELS[3], fmt_verdict(ttv), None),
    ]


# ── onboarding stepper (web loadFirstRun, html:3439-3459) ───────────────────
def onboarding_view(data: dict | None) -> dict | None:
    """None means "show nothing" — no data, or the user dismissed it."""
    if not isinstance(data, dict) or data.get("dismissed"):
        return None
    steps = dict_rows(data.get("steps"))
    # The server always sends `done`, but if it ever stops, counting the ticked
    # steps beats printing "0 / 3 done" above three green checkmarks.
    done = (int(_num(data.get("done"))) if data.get("done") is not None
            else sum(1 for s in steps if s.get("done")))
    total = int(_num(data.get("total"))) or len(steps) or 3
    if done >= total:
        title = "You're all set."
        sub = ("Every step is done — your workspace is fully live. "
               "Dismiss this whenever you like.")
    elif data.get("complete"):
        title = "Your audit trail is live."
        sub = ("The essentials are done. One optional step remains — invite "
               "your team to review the trail.")
    else:
        title = "Let's get your audit trail live."
        sub = ("Three quick steps to a tamper-evident audit trail. This card "
               "retires itself once you're rolling.")
    return {"title": title, "sub": sub, "done": done, "total": total,
            "progress_text": f"{done} / {total} done",
            "tone": "ok" if done >= total else None,
            "steps": [{"done": bool(s.get("done")),
                       "title": str(s.get("title") or ""),
                       "desc": str(s.get("desc") or ""),
                       "page": _section_for(s.get("page")),
                       "label": str(s.get("label") or "Open")}
                      for s in steps]}


# The web's page ids differ from the desktop section ids (D3's SECTIONS).
_WEB_TO_SECTION = {"dashboard": "home", "analytics": "threats", "keys": "access"}


def _section_for(web_page) -> str:
    page = str(web_page or "home")
    return _WEB_TO_SECTION.get(page, page)


# ── activity feed (web merge/kindFor, html:3913-3933) ───────────────────────
def activity_kind(action: str | None) -> str:
    action = action or ""
    if action.startswith("key."):
        return "key"
    if "disable" in action or "revoke" in action or "breach" in action:
        return "danger"
    return "gear"


def merge_activity(audit: list | None, logins: list | None) -> list[dict]:
    """Account audit + login history, newest first (the web's merge()).

    Each side degrades on its own: both endpoints are admin-only, so a member
    simply gets fewer rows rather than an error."""
    out: list[dict] = []
    for entry in dict_rows(audit):
        kind = activity_kind(entry.get("action"))
        target = entry.get("target")
        actor = entry.get("actor_email") or ""
        out.append({
            "when": entry.get("created_at"),
            "kind": kind, "tone": kind,
            "title": ACTION_LABELS.get(entry.get("action"))
                     or entry.get("action") or "Account change",
            "sub": (f"{target} · " if target else "") + actor,
        })
    for entry in dict_rows(logins):
        ok = bool(entry.get("success"))
        ip = entry.get("ip")
        out.append({
            "when": entry.get("created_at"),
            "kind": "login", "tone": "login" if ok else "danger",
            "title": "Signed in" if ok else "Failed sign-in attempt",
            "sub": (entry.get("email") or "") + (f" · {ip}" if ip else ""),
        })
    out.sort(key=lambda item: _epoch(item.get("when")), reverse=True)
    return out[:ACTIVITY_LIMIT]


def _epoch(iso) -> float:
    if not iso:
        return 0.0
    try:
        when = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    except ValueError:
        return 0.0
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return when.timestamp()


# ── quick ledger check (web lookupLedgerHash, html:2308-2325) ───────────────
HASH_LENGTH = 64


def normalize_hash(text: str | None) -> str:
    """Web parity: lowercase, strip everything that is not hex."""
    raw = (text or "").strip().lower()
    return "".join(ch for ch in raw if ch in "0123456789abcdef")


def quick_verify_request(text: str | None) -> tuple[str, str, str]:
    """Decide what to do with what the user typed, before any network call.

    Returns (action, message, tone) where action is "clear", "reject" or
    "check". Deciding here means the widget never has to guess."""
    norm = normalize_hash(text)
    if not norm:
        return "clear", "", "muted"
    if len(norm) != HASH_LENGTH:
        return "reject", f"enter a {HASH_LENGTH}-character chain hash", "bad"
    return "check", "checking ledger...", "muted"


def quick_verify_result(data: dict | None) -> tuple[str, str]:
    """Turn /v1/verify/hash/<hash> into (message, tone). Web:2315-2323.

    A record can be untampered *and* a policy breach — the two say different
    things and the web keeps them distinct, so we do too."""
    if not isinstance(data, dict):
        return "could not reach the server", "bad"
    if not data.get("found"):
        return "not in this ledger", "bad"
    if not data.get("verified"):
        return f"tampering detected at seq {data.get('seq')}", "bad"
    status = data.get("status") or "clean"
    if status == "breach":
        return f"record intact; policy breach - seq {data.get('seq')} - {status}", "bad"
    return f"record intact - seq {data.get('seq')} - {status}", "ok"


# ── recent ledger preview (web recentRow, html:2318) ───────────────────────
RECENT_LEDGER_LIMIT = 4


def short_hash(value: str | None) -> str:
    """The web's shortHash: first 10 hex characters, or an honest dash."""
    text = (value or "").strip()
    return f"{text[:10]}…" if len(text) > 10 else (text or "—")


def verdict_of(item: dict) -> tuple[str, str]:
    """(label, tone) for a ledger row — mirrors the web's verdictOf."""
    status = (item.get("status") or item.get("verdict") or "").lower()
    if status in ("breach", "flagged"):
        return "breach", "bad"
    if status in ("pending", "queued", ""):
        return "pending", "warn"
    return status, "ok"


def recent_ledger_rows(data: dict | None) -> list[dict]:
    """Newest few /v1/logs rows, shaped for the Home preview card."""
    items = (data or {}).get("items") if isinstance(data, dict) else None
    rows = []
    for item in dict_rows(items)[:RECENT_LEDGER_LIMIT]:
        label, tone = verdict_of(item)
        rows.append({
            "seq": item.get("seq"),
            "name": item.get("policy_tag") or "—",
            "agent": item.get("agent") or "",
            "hash": short_hash(item.get("chain_hash")),
            "verdict": label, "tone": tone,
            "when": item.get("created_at"),
        })
    return rows


# ── active alerts (web alertRow / loadThreats, html:3324-3345) ─────────────
ALERT_LIMIT = 5


def risk_tone(score) -> str:
    value = _num(score)
    return "bad" if value >= 70 else ("warn" if value >= 40 else "ok")


def alert_rows(data: dict | None) -> list[dict]:
    """Shape /v1/analytics/threats.recent_high_risk for the Home alerts card."""
    raw = (data or {}).get("recent_high_risk") if isinstance(data, dict) else None
    rows = []
    # dict_rows, not `raw or []`: /v1/analytics/threats has no response_model,
    # so nothing upstream guarantees these items are dicts.
    for alert in dict_rows(raw)[:ALERT_LIMIT]:
        risk = int(_num(alert.get("risk_score")))
        reason = (alert.get("reason") or "").strip()
        rows.append({
            "seq": alert.get("seq"),
            "policy": alert.get("policy_tag") or "policy",
            "agent": alert.get("agent") or "",
            "risk": risk, "tone": risk_tone(risk),
            # The web truncates at 120 chars; a judge reason can run long and
            # this card is a summary, not the record.
            "reason": reason[:120],
            "when": alert.get("timestamp"),
        })
    return rows


def open_alert_count(data: dict | None) -> int | None:
    """The web's "Open alerts" tile prefers total_threats over the page size,
    and shows nothing rather than a made-up 0 when the call failed."""
    if not isinstance(data, dict):
        return None
    total = data.get("total_threats")
    if total is not None:
        return int(_num(total))
    raw = data.get("recent_high_risk")
    return len(dict_rows(raw)) if isinstance(raw, list) else None
