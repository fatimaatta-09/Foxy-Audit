"""Foxy Audit desktop — console shell chrome (D3).

The desktop twin of the web dashboard's top-bar behaviour and global
affordances, ported from live source:

    section title + crumb      foxy-audit-premium.html:3480-3488  (CTX/setTopbarContext)
    announcement banner        :3504-3530                          (loadAnnBanner)
    notifications bell/panel   :3751-3789                          (loadTopNotif)
    breach pip cursor          :3389-3407                          (loadBreachBadge)
    live connection dot        :3880-3900, 4024-4025               (pingHealth, 60 s)
    Ctrl+K command palette     :1712-1761                          (build/render)
    ? overlay + g-then-letter  :3998-4015                          (NAV, 1.2 s window)

The decision logic lives in module-level functions with NO Qt in them —
palette matching, banner priority, the breach cursor, notification routing —
so the behaviour is testable without a screen, and the widgets below stay thin.

Everything paints from foxy_tokens. Icon-only controls carry accessible names,
targets are >= 44 px, focus is always visible, and motion respects the OS
reduced-motion setting.
"""

from __future__ import annotations

import math
import re
from datetime import datetime, timezone

# ── The nine web sections, in the web's order, with its exact copy ──────────
# (id, sidebar label, top-bar title, crumb tail, icon) — CTX at html:3480-3484.
SECTIONS: list[tuple[str, str, str, str, str]] = [
    ("home",     "Home",     "Overview",             "Home",     "overview"),
    ("threats",  "Threats",  "Threat analytics",     "Threats",  "analytics"),
    ("ledger",   "Ledger",   "Audit ledger",         "Ledger",   "log"),
    ("verify",   "Verify",   "Verify a record",      "Verify",   "shield"),
    ("policy",   "Policy",   "Policy ruleset",       "Policy",   "system"),
    ("export",   "Export",   "Compliance passport",  "Export",   "link"),
    ("access",   "Access",   "API keys & SDK",       "Access",   "key"),
    ("billing",  "Billing",  "Usage & billing",      "Billing",  "analytics"),
    ("settings", "Settings", "Settings",             "Settings", "system"),
]

# Desktop-only extras, kept at the bottom of the sidebar (plan §7).
EXTRA_SECTIONS: list[tuple[str, str, str, str, str]] = [
    ("system",  "System",  "System health",        "System",  "system"),
    ("sandbox", "Sandbox", "Verification sandbox", "Sandbox", "shield"),
]

ALL_SECTIONS = SECTIONS + EXTRA_SECTIONS

# g-then-letter quick nav — the web's NAV map (html:3999) remapped onto the
# desktop section ids. 'd' is the web's legacy alias for home.
QUICK_NAV = {
    "h": "home", "d": "home", "a": "threats", "l": "ledger", "v": "verify",
    "p": "policy", "e": "export", "k": "access", "b": "billing", "s": "settings",
}
QUICK_NAV_WINDOW_MS = 1200          # the web's gTimer

# DELIBERATE DIVERGENCE from the web (owner's call, logged in the relay's
# OWNER_DECISIONS.md #1): the web pings health every 60 s and only refreshes
# the rest of its chrome when you interact with it. The desktop refreshes ALL
# chrome — live dot, notifications, breach pip, banner — on the console's
# existing 15 s poll, because a companion app that sits on screen all day
# should be more current than a browser tab you occasionally look at. There is
# no separate health timer: one cadence, one place to reason about.
CHROME_REFRESH_SECONDS = 15
WEB_HEALTH_PING_SECONDS = 60        # what the web does (html:4025), for reference
NOTIF_LIMIT = 30                    # /v1/notifications?limit=30
BREACH_SCAN_LIMIT = 500             # /v1/logs/breaches?limit=500
TOAST_MS = 2700

_HEX_RE = re.compile(r"[^a-f0-9]")
_SEQ_RE = re.compile(r"^#?\d+$")


def badge_text(count: int) -> str:
    """Unread counts cap at 99+ (web: `unread>99?'99+'`)."""
    return "" if count <= 0 else ("99+" if count > 99 else str(count))


# ── Command palette (html:1730-1743) ────────────────────────────────────────
def palette_entries(query: str, *, org_id: str | None = None) -> list[dict]:
    """Rows for the Ctrl+K palette, in the web's order: pages, then a hash
    verify, then a #seq jump, then the fixed actions.

    Each row is {kind, label, arg}; the caller turns `kind` into behaviour, so
    the matching itself stays pure and testable."""
    norm = (query or "").strip()
    lc = norm.lower()
    out: list[dict] = []

    for sid, _label, title, _crumb, _icon in ALL_SECTIONS:
        if not norm or lc in title.lower() or lc in sid:
            out.append({"kind": "page", "label": f"Go to {title}", "arg": sid})

    hexed = _HEX_RE.sub("", lc)
    if len(hexed) >= 8:
        out.append({"kind": "verify-hash",
                    "label": f"Verify ledger hash {hexed[:16]}…", "arg": hexed})

    if _SEQ_RE.match(norm):
        seq = norm.lstrip("#")
        out.append({"kind": "ledger-seq",
                    "label": f"Open the audit ledger (record #{seq})", "arg": seq})

    actions = [
        ("copy organization id org workspace", "Copy organization ID",
         "copy-org", org_id),
        ("verify a record hash paste", "Verify a record by hash…",
         "verify-focus", None),
        ("keyboard shortcuts help hotkeys cheat sheet", "Keyboard shortcuts",
         "shortcuts", None),
    ]
    for keywords, label, kind, arg in actions:
        if not norm or lc in label.lower() or lc in keywords:
            out.append({"kind": kind, "label": label, "arg": arg})
    return out


# ── Breach pip (html:3389-3407) ─────────────────────────────────────────────
def breach_pip(rows, seen_seq: int) -> tuple[int, int]:
    """→ (unread count, highest seq present).

    `seen_seq` is the persisted "last seen" cursor; visiting Threats stores the
    max and the pip clears. Mirrors the web's filter on `seq > seen`."""
    if not isinstance(rows, list) or not rows:
        return 0, 0
    seqs = []
    for row in rows:
        try:
            seqs.append(int(row.get("seq") or 0))
        except (AttributeError, TypeError, ValueError):
            continue
    if not seqs:
        return 0, 0
    return sum(1 for s in seqs if s > seen_seq), max(seqs)


# ── Notification routing (html:3774-3780) ───────────────────────────────────
def notification_target(kind: str | None) -> str | None:
    """Which section a notification opens. Unknown kinds just mark read."""
    return {"breach": "threats", "quota": "billing"}.get((kind or "").lower())


# ── Announcement banner (html:3504-3530) ────────────────────────────────────
def announcement(breaches=None, plan=None, usage=None, dismissed=None,
                 *, now: datetime | None = None) -> dict | None:
    """The one banner worth showing, or None.

    Priority is the web's: newest breach → trial ending within 7 days → quota
    at/over 90%. Only REAL signals — nothing is invented, and a dismissed id
    never comes back. `now` is injectable so the trial maths is testable."""
    dismissed = dismissed or {}
    now = now or datetime.now(timezone.utc)

    if isinstance(breaches, list) and breaches:
        seq = breaches[0].get("seq") or 0
        ident = f"breach:{seq}"
        if ident not in dismissed:
            return {"id": ident, "tone": "bad", "icon": "warn",
                    "text": "A policy breach was recorded on your workspace.",
                    "action": "Review threats", "target": "threats"}

    if isinstance(plan, dict) and plan.get("trial_ends_at"):
        raw = str(plan["trial_ends_at"])
        try:
            ends = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if ends.tzinfo is None:
                ends = ends.replace(tzinfo=timezone.utc)
            # The web's Math.ceil((ends - now) / 86400000), kept exactly so the
            # two surfaces never disagree about "ends in N days".
            days = math.ceil((ends - now).total_seconds() / 86400)
        except ValueError:
            days = -1
        if 0 <= days <= 7:
            ident = f"trial:{raw[:10]}"
            if ident not in dismissed:
                plural = "" if days == 1 else "s"
                return {"id": ident, "tone": "warn", "icon": "info",
                        "text": f"Your trial ends in {days} day{plural}.",
                        "action": "View billing", "target": "billing"}

    quota = (usage or {}).get("quota") if isinstance(usage, dict) else None
    if isinstance(quota, dict) and quota.get("monthly_log_quota"):
        limit = quota["monthly_log_quota"] or 0
        used = quota.get("used_this_month") or 0
        pct = round(used / limit * 100) if limit else 0
        if quota.get("over_quota"):
            if "quota:over" not in dismissed:
                return {"id": "quota:over", "tone": "bad", "icon": "warn",
                        "text": "You've reached your monthly capture quota — "
                                "new events may be paused.",
                        "action": "View billing", "target": "billing"}
        elif pct >= 90 and "quota:90" not in dismissed:
            return {"id": "quota:90", "tone": "warn", "icon": "info",
                    "text": f"You've used {pct}% of your monthly capture quota.",
                    "action": "View billing", "target": "billing"}
    return None


# ── Relative time (html:3757) ───────────────────────────────────────────────
def relative_time(iso: str | None, *, now: datetime | None = None) -> str:
    """'just now' / '5m ago' / '3h ago' / '2d ago' — the web's rel()."""
    if not iso:
        return ""
    try:
        when = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    except ValueError:
        return ""
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    now = now or datetime.now(timezone.utc)
    delta = (now - when).total_seconds()
    if delta < 60:
        return "just now"
    if delta < 3600:
        return f"{int(delta // 60)}m ago"
    if delta < 86400:
        return f"{int(delta // 3600)}h ago"
    return f"{int(delta // 86400)}d ago"


# ── Shortcut cheat sheet (html:3999 NAV, rendered by ShortcutsOverlay) ──────
SHORTCUT_PAIRS: list[tuple[str, str]] = [
    ("Ctrl / ⌘ + K", "Command palette"),
    ("?", "This shortcut list"),
    ("g then h", "Home"),
    ("g then a", "Threats"),
    ("g then l", "Ledger"),
    ("g then v", "Verify"),
    ("g then p", "Policy"),
    ("g then e", "Export"),
    ("g then k", "Access"),
    ("g then b", "Billing"),
    ("g then s", "Settings"),
    ("Esc", "Close a dialog or menu"),
]


def avatar_initial(me: dict | None) -> str:
    """First letter of the display name, else of the email — the web's chip."""
    me = me or {}
    source = (me.get("full_name") or me.get("email") or "").strip()
    return source[0].upper() if source else "?"
