"""Foxy Audit desktop — Settings data shaping, admin half (D11b).

The four admin-only cards of the web's `#page-settings`
(foxy-audit-premium.html:1669-1706 + `loadTeam` / `addUser` / `changeUserRole`
/ `disableUser` / `enableUser` / `resendInvite` / `loadAccountAudit` /
`loadWebhooks` / `addWebhook` / `testWebhook` / `deleteWebhook` / `loadSso` /
`saveSso` / `removeSso`), with no Qt in it. Every endpoint and payload key
below was read off the routers, not the web: `auth_human.py:449-655`,
`account.py:341-356`, `webhooks.py`, `sso.py:46-122`.

**The rules this module inherits.**

*Secrets pass through, they never settle.* Exactly two functions here touch
one — `invite_result` and `webhook_secret`, each returning it for a single hop
into D9's shown-once dialog. Neither keeps it, formats it into a message, or
lets it near a row; nothing in this module can persist or log anything at all
(pinned by an AST scan in `test_d11b_settings_admin.py`). Same rule D9 holds
for API keys.

*A member is told, not refused.* The web hides these four cards from
non-admins. Hiding leaves a member wondering where team management went; every
one of these endpoints is `require_role("admin")`, so the desktop renders the
card with the reason instead. The notices live here so the card and its status
strip cannot drift apart.
"""

from __future__ import annotations

import re

from home_data import dict_rows

MISSING = "—"

#: Why each admin card is inert for a member. All four endpoints are
#: `require_role("admin")`, so this is the server's rule restated, not a guess.
TEAM_MEMBER_NOTICE = ("Only an admin can add, disable or change the role of a "
                      "workspace user.")
AUDIT_MEMBER_NOTICE = ("The workspace's account-change trail is visible to "
                       "admins.")
WEBHOOK_MEMBER_NOTICE = ("Only an admin can add or remove an outbound "
                         "webhook endpoint.")
SSO_MEMBER_NOTICE = ("Only an admin can configure single sign-on for this "
                     "workspace.")


# ── team ────────────────────────────────────────────────────────────────────
VALID_ROLES = ("member", "admin")

_EMAIL = re.compile(r"^[^@\s]+@[^@\s.]+\.[^@\s]+$")


def team_rows(data, me_email: str = "") -> list[dict]:
    """`GET /v1/auth/users` → one row per dashboard user.

    Which actions a row offers is decided here rather than in the widget: the
    server refuses to disable your own account (auth_human.py:537) and refuses
    to demote the last active admin (auth_human.py:576), and a button that is
    always going to be refused should not be drawn.
    """
    me = (me_email or "").strip().lower()
    users = []
    for u in dict_rows(data):
        email = str(u.get("email") or MISSING)
        role = str(u.get("role") or "member")
        disabled = bool(u.get("disabled"))
        users.append({
            "id": str(u.get("id") or ""),
            "email": email,
            "role": role,
            "disabled": disabled,
            "is_me": bool(me) and email.strip().lower() == me,
            "status": "disabled" if disabled else "active",
            "tone": "mute" if disabled else "ok",
            "meta": role + (" · disabled" if disabled else ""),
        })
    # The last ACTIVE admin cannot be DEMOTED — the server refuses with a 400
    # (auth_human.py:574-578), and offering the button is offering a dead end.
    # It is deliberately NOT applied to `disable`: the server has no such guard
    # there (auth_human.py:530-545 only blocks disabling yourself), and a
    # client that invented the rule would hide a button the server accepts.
    active_admins = sum(1 for u in users
                        if u["role"] == "admin" and not u["disabled"])
    for u in users:
        last_admin = (u["role"] == "admin" and not u["disabled"]
                      and active_admins <= 1)
        u["last_admin"] = last_admin
        u["can_change_role"] = (not u["is_me"] and not u["disabled"]
                                and not last_admin)
        u["next_role"] = "member" if u["role"] == "admin" else "admin"
        u["can_disable"] = not u["is_me"] and not u["disabled"]
        u["can_enable"] = u["disabled"]
        # `resend-invite` 400s on a disabled user (auth_human.py:631).
        u["can_reinvite"] = not u["disabled"] and not u["is_me"]
    return users


TEAM_EMPTY = ("No dashboard users yet",
              "Invite an auditor and they appear here with their role.")

#: The web calls `POST /v1/auth/users` with no password, so the server invites
#: by email (auth_human.py:494-506) and there is no temp password to reveal.
#: We do the same; the shown-once path below is for the case where a server
#: still returns one.
INVITE_BLURB = ("They get an email with a link to set their own password. "
                "Admins can manage keys, policy, team and billing; members "
                "read the evidence.")


def invite_problem(email: str | None, role: str | None) -> str | None:
    """The local check before the invite. Shallow on purpose — the server owns
    the real rule (auth_human.py:474-476) and a client that invented extra ones
    would refuse addresses the server accepts."""
    text = (email or "").strip()
    if not text:
        return "Enter the person's email address."
    if not _EMAIL.match(text):
        return "That does not look like an email address."
    if (role or "").strip().lower() not in VALID_ROLES:
        return "Pick a role."
    return None


def create_user_body(email: str, role: str) -> dict:
    """No `password` key: omitting it is what makes the server send an invite
    link instead of minting a temporary password (auth_human.py:494)."""
    return {"email": (email or "").strip().lower(),
            "role": (role or "member").strip().lower()}


def invite_result(data) -> tuple[str, str]:
    """(message, secret) for a created user.

    The secret is returned for exactly one hop — into the shown-once dialog —
    and is never stored, logged or echoed anywhere else. It is empty on the
    normal invite path, which is the path the desktop takes.
    """
    d = data if isinstance(data, dict) else {}
    email = str(d.get("email") or "the new user")
    if d.get("invited"):
        return (f"Invite email sent to {email}", "")
    secret = str(d.get("temp_password") or "")
    if secret:
        return (f"{email} added", secret)
    return (f"{email} added", "")


SEAT_LIMIT_FALLBACK = ("Your plan has no available dashboard seats. Upgrade "
                       "to invite another user.")


def disable_user_warning(email: str) -> str:
    return (f"{email} is signed out immediately and cannot sign in again "
            f"until an admin re-enables the account.")


def role_change_warning(email: str, role: str) -> str:
    if role == "admin":
        return (f"{email} will be able to manage API keys, policy, billing "
                f"and every other member of this workspace.")
    return (f"{email} will lose access to keys, policy, billing and team "
            f"management, and will keep read access to the evidence.")


# ── account activity ────────────────────────────────────────────────────────
#: Every `action` string `account_audit.record_account_action` is called with
#: anywhere in the backend (verified by grep over `backend/app/`), not only the
#: eight the web happens to map. An unknown action still falls through to its
#: raw value — the web's own behaviour — so a new server action shows up
#: honestly rather than disappearing.
AUDIT_LABELS = {
    "key.create": "Created API key",
    "key.revoke": "Revoked API key",
    "policy.update": "Updated policy",
    "member.role_change": "Changed member role",
    "member.enable": "Re-enabled member",
    "member.disable": "Disabled member",
    "mfa.enable": "Enabled 2FA",
    "mfa.disable": "Disabled 2FA",
    "account.profile_update": "Updated profile",
    "account.step_up": "Confirmed identity",
    "auth.logout_all": "Signed out every device",
    "auth.session_revoke": "Revoked a device session",
    "export.create": "Created an export",
    "sso.configure": "Configured SSO",
    "sso.remove": "Removed SSO",
    "webhook.create": "Added a webhook",
    "webhook.delete": "Removed a webhook",
    # P3 §4.5 added this action server-side; without a label the desktop would
    # show the raw action id to a customer reading their own account history.
    "billing.cancel": "Cancelled the subscription",
    # P3 §7.1 · the workspace id left /v1/auth/me and became a step-up-gated
    # reveal, which is audited — so "who un-masked it, and when" reads as a
    # sentence here rather than as a raw action id.
    "account.org_id_reveal": "Revealed the organization id",
}

AUDIT_EMPTY = ("No account changes recorded yet",
               "Changes to keys, policy, team, SSO and 2FA are recorded here.")
AUDIT_BLURB = ("A record of changes to keys, policy, team, and 2FA in this "
               "workspace.")


def audit_rows(data) -> list[dict]:
    """`GET /v1/account/audit?limit=50` → newest first, as the server returns
    them (account.py:349-356)."""
    rows = []
    for a in dict_rows(data):
        action = str(a.get("action") or MISSING)
        rows.append({
            "action": action,
            "label": AUDIT_LABELS.get(action, action),
            "target": str(a.get("target") or ""),
            "actor": str(a.get("actor_email") or ""),
            "when": a.get("created_at"),
        })
    return rows


# ── outbound webhooks ───────────────────────────────────────────────────────
#: `webhook_delivery.VALID_EVENTS` — the server 422s on anything else.
WEBHOOK_EVENTS = (("breach", "breach", True), ("graded", "graded", False))

WEBHOOK_BLURB = ("POST a signed JSON event to your endpoint when an "
                 "interaction is graded or breaches. Each request carries an "
                 "X-Foxy-Signature HMAC you verify with the signing secret.")
WEBHOOK_EMPTY = ("No webhooks yet",
                 "Add an endpoint and Foxy will POST signed events to it.")
WEBHOOK_SECRET_TITLE = "Webhook signing secret · verify X-Foxy-Signature"


def webhook_rows(data) -> list[dict]:
    """`GET /v1/webhooks` → the four things a row shows. The secret is never
    in this payload — the server sends an 11-character prefix (webhooks.py:47)
    and the full value exists only in the create response."""
    rows = []
    for w in dict_rows(data):
        status = str(w.get("last_status") or "")
        rows.append({
            "id": str(w.get("id") or ""),
            "url": str(w.get("url") or MISSING),
            "events": str(w.get("events") or ""),
            "active": bool(w.get("active", True)),
            "prefix": str(w.get("secret_prefix") or ""),
            "last_status": status,
            "tone": _delivery_tone(status),
            # The delivery outcome is toned; the events and the secret prefix
            # are not. Colouring the whole line made a working endpoint's
            # prefix green and a timed-out one's red, which says nothing.
            "status_text": f"last {status}" if status else "never delivered",
            "meta": " · ".join(p for p in (
                str(w.get("events") or ""),
                str(w.get("secret_prefix") or "")) if p),
        })
    return rows


def _delivery_tone(status: str) -> str:
    """`last_status` is whatever `_deliver_one` returned — an HTTP code as a
    string, or an error word. Unknown stays neutral rather than guessing."""
    text = (status or "").strip()
    if not text:
        return "mute"
    if text[:1] == "2" and text[:3].isdigit():
        return "ok"
    if text[:3].isdigit():
        return "bad"
    return "bad"          # "error", "timeout" — anything non-numeric failed


def webhook_problem(url: str | None, events: list[str] | None) -> str | None:
    text = (url or "").strip()
    if not text:
        return "Enter the URL Foxy should POST to."
    if not text.startswith(("https://", "http://")):
        return "The URL must start with https:// or http://."
    if not events:
        return "Pick at least one event."
    return None


def webhook_body(url: str, events: list[str]) -> dict:
    return {"url": (url or "").strip(), "events": list(events)}


def webhook_secret(data) -> str:
    """The `whsec_` value, for one hop into the shown-once dialog."""
    d = data if isinstance(data, dict) else {}
    return str(d.get("secret") or "")


def webhook_remove_warning(url: str) -> str:
    return (f"Foxy stops POSTing events to {url} immediately. Adding it back "
            f"later issues a NEW signing secret — the current one cannot be "
            f"recovered.")


def webhook_test_result(status: int | None, data, detail: str = "") -> str:
    """`POST /v1/webhooks/{id}/test` answers with the delivery's own status,
    which is the point of the button — a 200 from Foxy carrying "error" from
    your endpoint is a failure, and saying "test sent" would hide it."""
    if status is not None:
        return f"Test failed — {detail.strip() or f'HTTP {status}'}"
    result = str((data or {}).get("status") or "") if isinstance(data, dict) else ""
    if not result:
        return "Test sent — the server did not report a status"
    if _delivery_tone(result) == "ok":
        return f"Test delivered — your endpoint answered {result}"
    return f"Test not delivered — your endpoint answered {result}"


# ── enterprise SSO ──────────────────────────────────────────────────────────
SSO_BLURB = ("Route users whose email matches your domain to your identity "
             "provider. Works with any OIDC IdP (Okta, Entra ID, Auth0, "
             "Google Workspace). New users join this workspace as members.")
SSO_REMOVE_WARNING = ("Anyone signing in with this domain goes back to email "
                      "and password. Members already created by SSO keep "
                      "their accounts.")

SSO_NOT_SET_UP = "not set up"
#: Shown instead of a status when nobody answered — a member, or the backend
#: down. "not set up" would be a claim about a workspace we did not read.
SSO_UNKNOWN = "unknown"
SSO_SECRET_UNKNOWN = "Whether a secret is stored is not known from here."


def sso_secret_help(has_secret: bool) -> str:
    """Whether the field is optional depends on whether one is already stored
    (sso.py:86-90). Saying "leave blank to keep the saved one" with nothing
    saved describes a save that will 422."""
    return ("A secret is stored, encrypted. Leave this blank to keep it."
            if has_secret else
            "Required — no secret is stored for this workspace yet.")


def sso_secret_placeholder(has_secret: bool) -> str:
    return "blank keeps the stored secret" if has_secret else "from your IdP"


def sso_view(data) -> dict:
    """`GET /v1/auth/sso/connection` → the form's values.

    `client_secret` is deliberately absent: the route never returns it
    (sso.py:56-62, `has_secret` only), so there is nothing to render and
    nothing to accidentally echo back on save.
    """
    d = data if isinstance(data, dict) else {}
    configured = bool(d.get("configured"))
    active = bool(d.get("active"))
    return {
        "configured": configured,
        "domain": str(d.get("email_domain") or ""),
        "issuer": str(d.get("issuer") or ""),
        "client_id": str(d.get("client_id") or ""),
        "active": active if configured else True,
        "has_secret": bool(d.get("has_secret")),
        "status": ("active" if active else "disabled") if configured
                  else SSO_NOT_SET_UP,
        "tone": ("ok" if active else "warn") if configured else "mute",
    }


def sso_problem(domain: str | None, issuer: str | None, client_id: str | None,
                secret: str | None, has_secret: bool) -> str | None:
    """Mirrors the server's own validation (sso.py:79-90) so a typo comes back
    instantly instead of as a 422, including the one rule that is easy to miss:
    a blank secret only keeps the stored one if there IS a stored one."""
    d = (domain or "").strip().lower().lstrip("@")
    iss = (issuer or "").strip()
    if not d:
        return "Enter the email domain your IdP owns."
    if "." not in d or "@" in d or "/" in d:
        return "The domain must be bare, e.g. acme.com."
    if not iss:
        return "Enter your IdP's issuer URL."
    if not iss.startswith("https://"):
        return "The issuer URL must start with https://."
    if not (client_id or "").strip():
        return "Enter the client ID from your IdP."
    if not (secret or "").strip() and not has_secret:
        return "Enter the client secret — there is none stored yet."
    return None


def sso_body(domain: str, issuer: str, client_id: str, secret: str,
             active: bool) -> dict:
    """The secret's only appearance: straight from the field into the body.
    Blank is sent as blank, which is how the server is told to keep the stored
    one (sso.py:86-90)."""
    return {"email_domain": (domain or "").strip().lower().lstrip("@"),
            "issuer": (issuer or "").strip(),
            "client_id": (client_id or "").strip(),
            "client_secret": secret or "",
            "active": bool(active)}


def sso_callback_url(base_url: str | None) -> str:
    """What to paste into the IdP's redirect field.

    The web uses `location.origin`, which for a page served by the API is the
    API. A desktop app has no origin, and the server builds this from the
    request it receives (sso.py:42-43) — so the configured backend is the only
    honest source. With none configured we say so rather than printing a path
    that resolves against nothing.
    """
    root = (base_url or "").strip().rstrip("/")
    if not root:
        return ""
    return f"{root}/v1/auth/sso/callback"


SSO_CALLBACK_UNKNOWN = ("Set a backend URL to see the callback URL for this "
                        "workspace.")
