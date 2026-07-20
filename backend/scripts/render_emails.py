"""Dev-only: render every branded transactional email (P17) to standalone .html files
so you can eyeball them in a browser (Gmail/Outlook rendering still needs a real send).

    cd backend && python scripts/render_emails.py [OUT_DIR]

Writes OUT_DIR/*.html (default: ./email_preview/) plus an index.html linking them all. Each sample
mirrors the copy at its real call site — this is a visual harness, not the source of truth, so if you
change wording in a router/helper, update the matching sample here too. Purely offline; no DB, no send.
"""

from __future__ import annotations

import os
import sys

# allow `python scripts/render_emails.py` from the backend/ dir
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import email_templates as et   # noqa: E402

CODE = "042917"
RESET = "https://foxyaudit.tech/dashboard/reset?reset_token=demo-token-abc123"
INVITE = "https://foxyaudit.tech/dashboard/reset?reset_token=demo-invite-xyz789"


def _samples() -> list[tuple[str, str, tuple[str, str]]]:
    """(slug, subject, (html, text)) for each real email."""
    out: list[tuple[str, str, tuple[str, str]]] = []

    # ── OTP codes (mfa / auth_staff / keys / auth_human step-up) ──
    out.append(("otp_customer_signin", "Your Foxy Audit sign-in code", et.layout(
        title="Your sign-in code", preheader=f"Your Foxy Audit sign-in code is {CODE}",
        blocks=[et.paragraph("Use this one-time code to finish signing in to Foxy Audit."),
                et.code_block(CODE),
                et.muted("It expires in 5 minutes. If you didn't try to sign in, ignore this email.")])))

    out.append(("otp_staff_signin", "Your Foxy Audit staff sign-in code", et.layout(
        title="Your staff sign-in code", preheader=f"Your Foxy Audit staff sign-in code is {CODE}",
        blocks=[et.paragraph("Use this one-time code to finish signing in to the Foxy Audit staff "
                             "console."),
                et.code_block(CODE),
                et.muted("It expires in 5 minutes. If you didn't try to sign in, ignore this email.")],
        surface="staff")))

    out.append(("otp_stepup", "Your Foxy Audit security code", et.layout(
        title="Confirm it's you", preheader=f"Your Foxy Audit security code is {CODE}",
        blocks=[et.paragraph("Use this one-time code to confirm a sensitive change to your account."),
                et.code_block(CODE),
                et.muted("It expires in 5 minutes. If you didn't request this, ignore this email and "
                         "change your password.")])))

    out.append(("otp_key_regen", "Your Foxy Audit key-regeneration code", et.layout(
        title="Confirm API-key regeneration", preheader=f"Your Foxy Audit key-regeneration code is {CODE}",
        blocks=[et.paragraph("Use this one-time code to regenerate your API key. Your current key is "
                             "revoked the moment the new one is issued."),
                et.code_block(CODE),
                et.muted("It expires in 5 minutes. If you didn't request this, ignore this email and "
                         "change your password.")])))

    # ── password reset / invite (password_reset) ──
    out.append(("reset_password", "Reset your Foxy Audit password", et.layout(
        title="Reset your password",
        preheader="Choose a new password for your Foxy Audit account — this link expires in 1 hour.",
        blocks=[et.paragraph("We received a request to reset your Foxy Audit password. Use the "
                             "button below to choose a new one."),
                et.button("Choose a new password", RESET),
                et.muted("This link expires in 1 hour. If you didn't request it, you can safely "
                         "ignore this email — your password stays the same.")])))

    out.append(("invite_customer", "You're invited to Foxy Audit — set your password", et.layout(
        title="Set your password",
        preheader="You've been invited to Foxy Audit — set your password to activate your account.",
        blocks=[et.paragraph("You've been invited to Foxy Audit. Set your password to activate your "
                             "account."),
                et.button("Set your password", INVITE),
                et.muted("This invitation link expires in 7 days. If you weren't expecting it, you "
                         "can ignore this email.")])))

    # ── customer alerts (worker breach) ──
    out.append(("breach", "\U0001f534 Policy breach flagged — Foxy Audit", et.layout(
        title="Policy breach flagged",
        preheader="A policy breach was flagged in your audit trail (record #4821, risk 0.86).",
        blocks=[et.paragraph("A policy breach was flagged in your audit trail (record #4821, risk 0.86)."),
                et.callout("PII disclosure: response contained an unredacted email address.", tone="bad"),
                et.muted("Open your dashboard ledger to review. Only hashes are stored — never the "
                         "prompt or response.")])))

    # ── support reply (admin_inbox) ──
    out.append(("support_reply", "Re: your message to Foxy Audit", et.layout(
        title="A reply from Foxy Audit support",
        preheader="Thanks for reaching out — here are the setup steps you asked about.",
        blocks=[et.paragraph("Thanks for reaching out! Here are the setup steps you asked about."),
                et.paragraph("1. Install the SDK.  2. Drop in your org key.  3. Wrap your model call."),
                et.divider(), et.muted("— The Foxy Audit team")])))

    # ── contact confirmation + priority alert (leads) ──
    out.append(("contact_confirm", "We got your message — Foxy Audit", et.layout(
        title="We got your message",
        preheader="Thanks for reaching out to Foxy Audit — your message is with our team.",
        blocks=[et.paragraph("Thanks for reaching out to Foxy Audit."),
                et.paragraph("Your message is with our team and we'll get back to you shortly.")])))

    out.append(("priority_lead", "\U0001f534 Priority Enterprise — Dana Rivera", et.layout(
        title="Priority enterprise inquiry",
        preheader="Dana Rivera <dana@bigco.com> sent a priority enterprise inquiry.",
        blocks=[et.paragraph("Dana Rivera <dana@bigco.com> sent a priority enterprise inquiry:"),
                et.divider(),
                et.paragraph("We need SOC2 evidence and a custom SLA for 40 seats."),
                et.divider(),
                et.muted("Open it in the admin inbox to claim & reply.")], surface="staff")))

    # ── staff broadcast (admin_config) ──
    out.append(("broadcast", "[Foxy Audit] Scheduled maintenance", et.layout(
        title="Scheduled maintenance",
        preheader="Anchoring will pause for ~15 min tonight at 02:00 UTC.",
        blocks=[et.callout("Platform warning announcement", tone="warn"),
                et.paragraph("Anchoring will pause for ~15 minutes tonight at 02:00 UTC for a wallet "
                             "rotation. No customer action needed.")], surface="staff")))

    # ── ops health alerts (anchor / usage), compact ──
    out.append(("ops_anchor", "[Foxy Audit] anchoring needs attention", et.layout(
        title="Anchoring needs attention",
        preheader="2 org(s) with a failed latest anchor; 5 with a stale confirmed anchor.",
        blocks=[et.paragraph("Public-chain anchoring health check flagged issues:"),
                et.info_rows([("Failed latest anchor", "2 org(s)"),
                              ("Stale confirmed anchor", "5 org(s)")]),
                et.muted("Check the worker logs and the funded wallet balance.")],
        surface="staff", variant="compact")))

    out.append(("ops_grading", "[Foxy Audit] 7 grading failure(s) need attention", et.layout(
        title="Grading failures need attention",
        preheader="7 interaction(s) are parked in the grading dead-letter.",
        blocks=[et.paragraph("7 interaction(s) are parked in grading_status='failed' — the Gemini "
                             "judge exhausted all retries."),
                et.muted("Check the worker logs and the affected org's policy/config.")],
        surface="staff", variant="compact")))

    return out


def main() -> None:
    out_dir = sys.argv[1] if len(sys.argv) > 1 else "email_preview"
    os.makedirs(out_dir, exist_ok=True)
    rows = _samples()
    links = []
    for slug, subject, (html, _text) in rows:
        path = os.path.join(out_dir, f"{slug}.html")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(html)
        links.append(f'<li><a href="{slug}.html">{slug}</a> &mdash; <code>{subject}</code></li>')
    index = ("<!doctype html><meta charset=utf-8><title>Foxy email previews</title>"
             "<body style='font-family:system-ui;max-width:720px;margin:40px auto;'>"
             "<h1>Foxy Audit — transactional email previews (P17)</h1>"
             f"<p>{len(rows)} templates.</p><ul style='line-height:2'>" + "".join(links) + "</ul>")
    with open(os.path.join(out_dir, "index.html"), "w", encoding="utf-8") as fh:
        fh.write(index)
    print(f"Wrote {len(rows)} previews + index.html to {os.path.abspath(out_dir)}")


if __name__ == "__main__":
    main()
