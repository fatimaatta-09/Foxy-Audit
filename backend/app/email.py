"""Transactional email via Brevo (Sendinblue).

Used for staff MFA codes (Phase 5 · 5B.5) and, later, password resets / invites
(5D). If BREVO_API_KEY is unset the send is a logged no-op — so dev and tests
never touch the network. send_email never raises into the caller.
"""

from __future__ import annotations

import logging

import requests

from .config import get_settings

log = logging.getLogger("foxy.email")

_BREVO_URL = "https://api.brevo.com/v3/smtp/email"


def send_email(*, to: str, subject: str, html: str, text: str | None = None) -> bool:
    """Send one transactional email. Returns True on a 2xx from Brevo, else False
    (also False when no API key is configured). Never raises."""
    s = get_settings()
    if not s.brevo_api_key:
        log.info("email suppressed (no BREVO_API_KEY): to=%s subject=%r", to, subject)
        return False
    try:
        resp = requests.post(
            _BREVO_URL,
            headers={"api-key": s.brevo_api_key, "content-type": "application/json"},
            json={
                "sender": {"email": s.brevo_sender_email, "name": s.brevo_sender_name},
                "to": [{"email": to}],
                "subject": subject,
                "htmlContent": html,
                **({"textContent": text} if text else {}),
            },
            timeout=10,
        )
        resp.raise_for_status()
        return True
    except Exception as exc:  # noqa: BLE001 — email must never break the caller
        log.warning("brevo send failed (to=%s): %s", to, exc)
        return False
