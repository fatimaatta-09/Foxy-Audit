"""Foxy Audit desktop — Settings data shaping (D11, account half).

Everything `#page-settings` decides before it paints, with no Qt in it. Ported
from the live web (foxy-audit-premium.html:1546-1670 + saveName / savePref /
loadDevices / mfa* / changePassword / saveIpAllowlist / mintBadge /
deleteWorkspace) and checked against the routes it calls.

**Two rules this module is built around.**

*Secrets pass through, they never settle.* Passwords, the 2FA code and the
badge token arrive from a field and go straight onto the wire. Nothing here
accepts a secret as state, formats one into a message, or returns one — the
same rule D9 holds for API keys and D7 for judge keys.

*A destructive action states what it destroys.* Revoking a device, turning off
2FA, revoking a badge and deleting the workspace all name the consequence
rather than asking "are you sure" — the wording lives here so it is testable
and cannot drift between the button and the dialog.
"""

from __future__ import annotations

import re

from home_data import dict_rows

MISSING = "—"


# ── account & identity ──────────────────────────────────────────────────────
NAME_MAX = 120          # the web's maxlength on #setName


def identity_view(me: dict | None) -> dict:
    """The four identity rows from `/v1/auth/me`. Three are read-only here:
    email and role are changed by an admin, org id never."""
    d = me if isinstance(me, dict) else {}
    return {
        "full_name": str(d.get("full_name") or ""),
        "email": str(d.get("email") or MISSING),
        "role": str(d.get("role") or MISSING),
        "org_id": str(d.get("org_id") or MISSING),
        "mfa_enabled": bool(d.get("mfa_enabled")),
        "known": bool(d),
    }


def profile_body(name: str) -> dict:
    return {"full_name": (name or "").strip()[:NAME_MAX]}


# ── data & privacy toggles ──────────────────────────────────────────────────
#: (key, title, description, default) — the six the backend accepts
#: (account.py:593-597). The defaults match what the server does when the key
#: is ABSENT, so an unset preference is shown the way it actually behaves:
#: breach alerts and the weekly digest deliver unless turned off, everything
#: else is opt-in.
PREFERENCES = (
    ("notify_breach_alerts", "Breach alerts",
     "immediate notice on every breach verdict", True),
    ("notify_weekly_digest", "Weekly digest",
     "summary of activity every Monday", True),
    ("notify_key_rotation_reminders", "Key rotation reminders",
     "nudge after 90 days unrotated", False),
    ("hide_sensitive_metadata", "Hide sensitive metadata by default",
     "mask chain heads and org ids until you reveal them", False),
    # notify_product_updates and notify_security_alerts were removed here when the
    # web deleted them (P3 §6): nothing ever sent product updates, and new-device
    # alerts are deliberately not opt-out-able, so a switch offering to silence
    # them was a lie. The server no longer accepts either key — offering them here
    # would be a control that silently fails to save. "Web wins."
)

PREF_KEYS = tuple(key for key, _t, _d, _v in PREFERENCES)


def preference_values(data: dict | None) -> dict:
    """`GET /v1/account/preferences` → {key: bool}, absent meaning the
    documented default rather than False."""
    prefs = (data or {}).get("preferences") if isinstance(data, dict) else None
    prefs = prefs if isinstance(prefs, dict) else {}
    return {key: bool(prefs.get(key, default))
            for key, _t, _d, default in PREFERENCES}


def preference_body(key: str, value: bool) -> dict:
    """The web sends one key at a time and the server merges (account.py:613).
    Sending the whole bag would let a stale toggle overwrite a change made in
    another window."""
    return {"preferences": {key: bool(value)}}


# ── password + two-factor ───────────────────────────────────────────────────
PASSWORD_MIN = 8


def password_problem(current: str, new: str) -> str | None:
    """The local check before `POST /v1/auth/change-password`.

    Deliberately shallow: the server owns the real policy, and a client that
    invented extra rules would refuse passwords the server accepts. This only
    catches what is certainly wrong.
    """
    if not (current or "").strip():
        return "Enter your current password."
    if not new:
        return "Enter a new password."
    if len(new) < PASSWORD_MIN:
        return f"Use at least {PASSWORD_MIN} characters."
    if new == current:
        return "The new password is the same as the current one."
    return None


MFA_ON = "Two-factor is on for your account."
MFA_OFF = "Two-factor is off. Sign-in needs only your password."
MFA_CODE_SENT = "We emailed you a 6-digit code."
MFA_DISABLE_WARNING = (
    "Turning this off means your password alone can sign in to this "
    "workspace. Confirm with your password to continue.")

_CODE = re.compile(r"^\d{6}$")


def mfa_code_ok(code: str | None) -> bool:
    return bool(_CODE.match((code or "").strip()))


# ── access control (IP allow-list) ──────────────────────────────────────────
IP_HELP = ("Restrict dashboard access to specific IPs or CIDR ranges, "
           "comma-separated. Blank allows all.")
IP_LOCKOUT = ("Include your own address — an allow-list that leaves you out "
              "locks you out of your own workspace.")

_IPV4 = re.compile(r"^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$")


def _octets_ok(text: str) -> bool:
    match = _IPV4.match(text)
    return bool(match) and all(0 <= int(p) <= 255 for p in match.groups())


def allowlist_problem(text: str | None) -> str | None:
    """Catch a malformed entry before it becomes a lock-out.

    The server validates too, but this one is worth doing locally: a rejected
    save is recoverable, and a save that succeeds with the wrong entry is not
    — you have just locked yourself out of the page you would fix it on.
    """
    raw = (text or "").strip()
    if not raw:
        return None                      # blank is "allow all", not an error
    for part in (p.strip() for p in raw.split(",")):
        if not part:
            continue
        host, _, bits = part.partition("/")
        if not _octets_ok(host):
            return f"“{part}” is not an IPv4 address or CIDR range."
        if bits and not (bits.isdigit() and 0 <= int(bits) <= 32):
            return f"“{part}” has an invalid prefix length."
    return None


def allowlist_body(text: str | None) -> dict:
    return {"allowlist": (text or "").strip()}


# ── devices & sessions ──────────────────────────────────────────────────────
DEVICE_EMPTY = ("No other devices", "This is the only active session.")


def device_rows(data) -> list[dict]:
    """`GET /v1/auth/sessions` → one row per device, this one flagged."""
    items = (data or {}).get("items") if isinstance(data, dict) else data
    rows = []
    for s in dict_rows(items):
        rows.append({
            "id": str(s.get("id") or ""),
            "agent": str(s.get("user_agent") or "unknown device")[:90],
            "ip": str(s.get("ip") or ""),
            "current": bool(s.get("current")),
            "created_at": s.get("created_at"),
            "last_seen_at": s.get("last_seen_at"),
            # You cannot revoke the session you are using — the web hides the
            # button, and "Log out everywhere" is the control that does that.
            "revocable": not bool(s.get("current")),
        })
    return rows


def revoke_device_warning(agent: str) -> str:
    return (f"“{agent}” will be signed out immediately and will need to sign "
            f"in again. This cannot be undone.")


LOGOUT_ALL_WARNING = (
    "Every device is signed out, including this one — you will be returned to "
    "the sign-in screen.")


# ── trust badge ─────────────────────────────────────────────────────────────
BADGE_BLURB = (
    "A public badge showing that this workspace keeps a tamper-evident audit "
    "ledger. It carries aggregate status only — no interaction, agent or "
    "policy ever appears in it.")
BADGE_REVOKE_WARNING = (
    "The badge stops rendering everywhere it is embedded, immediately. You "
    "can mint a new one afterwards, but it will have a different URL.")


def badge_view(data: dict | None, base_url: str = "") -> dict:
    """`POST /v1/account/badge` → the three things the card shows.

    The server may hand back a ready-made URL; if it only returns the token we
    build the URLs from the configured backend, never from a guessed host.
    """
    d = data if isinstance(data, dict) else {}
    token = str(d.get("token") or d.get("badge_token") or "")
    blank = {"token": "", "svg_url": "", "verify_url": "", "embed": ""}
    if not token:
        return blank
    root = (base_url or "").rstrip("/")
    # `url` is the key the route actually returns; `svg_url` was a guess that
    # happened to work only because the fallback built the same string.
    svg = str(d.get("url") or d.get("svg_url")
              or (f"{root}/v1/badge/{token}.svg" if root else ""))
    verify = str(d.get("verify_url") or (f"{root}/verify/{token}" if root else ""))
    if not svg or not verify:
        return blank            # no backend configured ⇒ no URL to promise
    return {"token": token, "svg_url": svg, "verify_url": verify,
            "embed": f'<a href="{verify}"><img src="{svg}" '
                     f'alt="Foxy Audit — tamper-evident ledger"></a>'}


# ── what Foxy stores (static, and load-bearing) ─────────────────────────────
STORES_TITLE = "What Foxy stores"
STORES = (
    ("Hashes and metadata", "A customer-keyed HMAC commitment of the prompt "
     "and the response, token counts, your policy tag and the verdict."),
    ("Never the content", "Raw prompts and responses never leave your process "
     "through the SDK and are never stored here — not in the ledger, not in "
     "exports, not in logs."),
    ("The chain", "Each record's hash includes its predecessor's, so altering "
     "any historical row breaks every record after it."),
)

DESKTOP_CARD = ("You're on the desktop app ✓",
                "This console is the desktop build — there is nothing to "
                "download.")


# ── danger zone ─────────────────────────────────────────────────────────────
DELETE_TITLE = "Delete this workspace?"
DELETE_WARNING = (
    "Every member is signed out and the workspace is locked. Your evidence is "
    "retained and support can reverse this, but nobody can sign in or capture "
    "anything until they do.")
DELETE_PROMPT = "Type the workspace name to confirm:"

#: Shown when this session has no way to know the workspace name. /v1/health
#: carries it and is Bearer-only, so an email-and-password session simply does
#: not have it — the server compares the typed name anyway (account.py:266),
#: so it decides rather than us blocking the button forever.
DELETE_UNKNOWN_NAME = (
    "This session cannot look up the workspace name, so it will be checked by "
    "the server. Type it exactly as it appears on your account.")


def delete_confirmed(typed: str | None, name: str | None) -> bool:
    """Type-the-name confirmation, trimmed and case-insensitive.

    Case-insensitive on purpose: this is a "prove you meant it" gate, not a
    password, and a user who typed their own workspace name in the wrong case
    has proved it.
    """
    return bool((typed or "").strip()
                and (typed or "").strip().casefold()
                == (name or "").strip().casefold())


def delete_body(name: str) -> dict:
    return {"confirm_name": (name or "").strip()}


# ── shared result wording ───────────────────────────────────────────────────
def save_result(status: int | None, detail: str = "", *, what: str = "change"
                ) -> tuple[str, str]:
    """(message, tone) for any of this page's small saves."""
    if status is None:
        return ("✓ saved", "ok")
    if status == 403:
        return (detail.strip() or f"You may not make that {what}.", "bad")
    if status == 401:
        return ("Your session expired — sign in again.", "bad")
    if status in (400, 422):
        return (detail.strip() or "The server rejected that value.", "bad")
    if status == 0:
        return ("Could not reach the server — nothing was saved.", "bad")
    return (f"Save failed — {detail.strip() or f'HTTP {status}'}", "bad")


def badge_link_html(badge: dict, colour: str) -> str:
    """The verify link as rich text.

    Escaped here, in one place: the URL comes back from the server and this is
    the only spot in the page where a server value becomes markup.
    """
    from html import escape
    url = escape(str((badge or {}).get("verify_url") or ""), quote=True)
    if not url:
        return ""
    return f'<a href="{url}" style="color:{escape(colour, quote=True)};">{url}</a>'


#: Shown on a preference row when its save failed AND the corrective re-read
#: also failed, so the switch on screen is a value nobody confirmed.
PREF_UNCONFIRMED = "not saved - this switch may not match your account"


# -- recent logins (admin-only: auth_human.py:437) ---------------------------
LOGINS_EMPTY = ("No sign-ins recorded",
                "Successful and failed sign-in attempts appear here.")
LOGINS_MEMBER = ("Sign-in history for the workspace is visible to admins. "
                 "Your own devices are listed above.")


def login_rows(data) -> list[dict]:
    """`GET /v1/auth/login-history` - newest first, as the server returns it.

    A FAILED attempt is the point of this list, so it is toned and labelled,
    never quietly rendered like a success.
    """
    rows = []
    for item in dict_rows(data):
        ok = bool(item.get("success"))
        rows.append({
            "email": str(item.get("email") or MISSING),
            "ip": str(item.get("ip") or ""),
            "agent": str(item.get("user_agent") or "")[:70],
            "success": ok,
            "outcome": "signed in" if ok else "failed",
            "tone": "ok" if ok else "bad",
            "when": item.get("created_at"),
        })
    return rows


HELP_TITLE = "Help & support"
HELP_LINES = (
    ("Documentation", "https://foxyaudit.tech/docs"),
    ("Email support", "mailto:support@foxyaudit.tech"),
)


def desktop_version_line(version: str | None) -> str:
    """The web offers a download; we say which build you are already running.
    An unknown version says so rather than inventing one."""
    text = (version or "").strip()
    return f"Version {text}" if text else "Version unknown in this build"
