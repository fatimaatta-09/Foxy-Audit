"""Foxy Audit desktop — Access / API keys data shaping (D9).

Everything `#page-keys` decides before it paints, with no Qt in it. Ported from
the live web (foxy-audit-premium.html:1438-1476 + loadKeys / createKey /
revokeKey / regenerateKey / revealSecret).

**The hard rule this module exists under:** a plaintext API key appears exactly
once, in the dialog that shows it, and nowhere else — not in a log line, not in
QSettings, not in a file, not on a widget that outlives the dialog. Nothing in
this module accepts, returns, formats or stores a plaintext key. The only value
it will ever see is `key_prefix`, which is the non-secret display fragment the
server hands out precisely so a key can be identified without being revealed.

`test_access_data.py::test_no_plaintext_key_ever_reaches_this_module` pins that.
"""

from __future__ import annotations

from home_data import _num, dict_rows, thousands

TABLE_COLUMNS = ("Name", "Key", "Status", "Last used", "")

#: What the server calls a key's state, and how it should read.
STATUS_TONES = {"active": "ok", "revoked": "mute", "expired": "warn"}

#: A revoked or expired key is history, not a live credential. It stays in the
#: table (an auditor needs to see that it existed) but is dimmed, so the
#: distinction between "this can be used right now" and "this once could" is
#: never a matter of reading the fine print.
DIM_STATUSES = ("revoked", "expired")


def key_status(item: dict) -> tuple[str, str]:
    """(label, tone). `expired` is derived server-side but is not a `status`
    value — an expired key still reads as "active" in the raw row."""
    if not isinstance(item, dict):
        return "unknown", "warn"
    if item.get("expired") and item.get("status") == "active":
        return "expired", STATUS_TONES["expired"]
    status = str(item.get("status") or "unknown")
    return status, STATUS_TONES.get(status, "warn")


def key_rows(data) -> list[dict]:
    """One dict per key. Carries `key_prefix` only — never a plaintext key."""
    rows = []
    for item in dict_rows(data):
        label, tone = key_status(item)
        rows.append({
            "id": str(item.get("id") or ""),
            "name": str(item.get("name") or "unnamed key"),
            # The server's non-secret display fragment. There is no code path
            # in the desktop that puts a full key on a table row.
            "prefix": str(item.get("key_prefix") or "—"),
            "status": label, "tone": tone,
            "dim": label in DIM_STATUSES,
            "revocable": label == "active",
            "last_used": item.get("last_used_at"),
            "created": item.get("created_at"),
            "expires": item.get("expires_at"),
        })
    return rows


def stat_row(data, quota: dict | None) -> list[tuple]:
    """Active keys · Total keys · Key limit · Last SDK activity."""
    rows = key_rows(data) if data is not None else []
    quota = quota if isinstance(quota, dict) else {}

    active_from_quota = quota.get("active_api_keys")
    active = (int(_num(active_from_quota)) if active_from_quota is not None
              else sum(1 for r in rows if r["status"] == "active"))

    limit = quota.get("api_key_limit", "__missing__")
    if limit == "__missing__":
        limit_text = "—"                       # not asked, or the call failed
    elif limit is None:
        limit_text = "∞"                       # explicitly unlimited
    else:
        limit_text = thousands(limit)

    used = [r["last_used"] for r in rows if r["last_used"]]
    # A raw ISO timestamp in a stat tile is both unreadable and too wide for
    # the tile — "3h ago" is the answer to "when did the SDK last talk to us".
    last = _relative(max(used)) if used else "—"
    return [
        ("Active keys", "—" if data is None else thousands(active), "fox"),
        ("Total keys", "—" if data is None else thousands(len(rows)), None),
        ("Key limit", limit_text, None),
        ("Last SDK activity", last, None),
    ]


def _relative(iso) -> str:
    """Imported lazily so this module stays free of anything but shaping."""
    from console_chrome import relative_time
    return relative_time(iso) or "—"


def create_body(name: str, expires_in_days: str | int | None) -> dict:
    """POST /v1/keys. Mirrors the web's clamp of 3650 days (html:createKey)."""
    body = {"name": (name or "").strip() or "unnamed key"}
    try:
        days = int(expires_in_days)
    except (TypeError, ValueError):
        days = 0
    if days > 0:
        body["expires_in_days"] = min(3650, days)
    return body


#: The copy the web uses on the shown-once dialog (html:2619). Quoted exactly:
#: it is the only warning the user gets, and softening it in one product would
#: be the difference between them copying the key and losing it.
SHOWN_ONCE_TITLE = "Shown once — copy it now. It cannot be recovered."

REVOKE_CONFIRM_TITLE = "Revoke this key?"


def revoke_confirm_body(name: str) -> str:
    """Irreversible, and it breaks whatever is using the key — so the dialog
    says what will actually happen rather than "are you sure"."""
    return (f"“{name}” stops working immediately, and anything still using it "
            f"will start failing. This cannot be undone — you would need to "
            f"create a new key and update your SDK configuration.")


REGENERATE_CONFIRM = (
    "Regenerating mints a fresh key and revokes the current one. We will email "
    "you a 6-digit code to confirm. Anything still using the old key will stop "
    "working until you update it.")


# ── the Connect-the-SDK card (html:1466-1476) ───────────────────────────────
def sdk_snippets(key_prefix: str | None = None) -> list[tuple]:
    """(caption, code) for the three copy-boxes.

    The env-var box shows the key PREFIX with a placeholder tail, never a real
    key — the page has no access to one, and a snippet that looked ready to
    paste would be a plaintext key on screen.
    """
    shown = f"{key_prefix}…" if key_prefix else "foxy_sk_your_key_here"
    return [
        ("1 · install", "pip install foxy-audit"),
        ("2 · configure", f"export FOXY_API_KEY={shown}"),
        ("3 · decorate",
         "@foxy.audit(policy=\"phi-strict\", mode=\"block\", agent=\"gpt-5.6\")\n"
         "def ask(prompt: str) -> str:\n"
         "    return client.responses.create(...)"),
    ]


def connection_result(ok: bool, *, status: int | None = None) -> tuple[str, str]:
    """(message, tone) for the Test-connection button."""
    if ok:
        return "✓ connected — the key works", "ok"
    if status == 401:
        return "✗ the key was rejected (401)", "bad"
    if status == 403:
        return "✗ this key is not allowed from here (403)", "bad"
    if status is not None:
        return f"✗ the server answered {status}", "bad"
    return "✗ could not reach the server", "bad"


MEMBER_NOTICE = (
    "API keys are managed by workspace admins. You can see that keys exist and "
    "when they were last used, but creating, regenerating and revoking them "
    "needs an admin account.")


#: The FALLBACK for a 402 from POST /v1/keys, used only when the server's own
#: message did not survive the trip (see `_on_key_create_failed`). The 402 body
#: also carries used/included counts; those do not cross the worker's
#: string-only error contract, so this says the same thing without inventing
#: numbers it cannot see. There used to be a `limit_reached_message(detail)`
#: here that formatted the counts — nothing could call it, because nothing ever
#: had the dict, so it was deleted rather than left looking like live behavior.
LIMIT_REACHED_FALLBACK = (
    "Your plan has no available active API-key slots. Revoke a key you no "
    "longer use, or upgrade to add another environment or service.")
