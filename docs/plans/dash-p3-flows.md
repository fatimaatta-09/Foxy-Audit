# Dashboard P3 — Auth, account & the broken flows

**Plan of record** · 2026-07-29 · MAIN chat is the committer; executors build per this file.
Branch: `feat/dash-p3-flows`. **Independent of P1/P2** — this plan touches flows, backend and email, not
the token layer, so a second executor can run it in parallel.

**Requests source:** `G:\My Drive\Life\03 Projects\Foxy Audit\Dashboard\Changes Dashboard (Clarified for Claude).md`
**Files:** `foxy-dashboard/foxy-audit-premium.html` · `backend/app/routers/auth.py`, `account.py` ·
`backend/app/email.py`, `email_templates.py`, `user_notifications.py` · `backend/app/models.py`

---

## Context

P1 and P2 make the dashboard look finished. This plan makes it *work*. These are the items where something
is actually broken, missing, or does nothing when clicked — including one that locks the owner out of his
own product.

**Ordering note:** §1 is not sequenced with the rest. It ships first, on its own branch, ahead of
everything in all three plans.

---

## 1 · Password change locks you out — **ships first, alone**

**Owner report:** *"I can't get back into my account after changing the password."*

A user who changes their password is then unable to log in. This is the highest-severity item across all
three plans: it is a live, reproducible lockout in production, and every hour it stays open is an hour a
real customer could hit it.

**Investigate in this order:**
1. `POST /v1/auth/change-password` — is the new hash written before or after session invalidation?
2. Session/`token_hash` invalidation: are *all* sessions revoked, including the one being used to make the
   change, without a clean re-auth path?
3. Does the client hold a stale session cookie and retry against a revoked session, producing a 401 loop?
4. Is the step-up grant (which lives **inside** the refreshed session cookie) being dropped when the
   session is re-issued?

**Requirements:**
- Changing a password must either keep the current session valid or hand back a fresh one atomically.
- Other devices' sessions **should** be revoked (that is correct security) — the current one must not be
  orphaned.
- **Regression test** covering: change password → still authenticated → log out → log in with the new
  password → succeeds. Assert on the actual endpoints, not a mocked client.
- Reproduce the bug first and record the reproduction in the commit body. A fix for a bug nobody
  reproduced is a guess.

## 2 · Login page

Owner screenshot `cramped.png` shows three separate problems on one card.

**2.1 · Field/placeholder contrast.** Pale grey placeholder on a near-white field — effectively invisible.
Placeholder must clear **4.5:1** against the field background. Note the desktop app already made this exact
fix deliberately (`auth_windows.py` paints `PlaceholderText` with `--muted`, not `--muted2`) — match it.

**2.2 · Show/hide password → an eye icon**, not a text button. Morphs to a struck-through eye when
revealed. `aria-pressed` toggles, `aria-label` changes between "Show password" and "Hide password". Focus
visible. 44px minimum hit target.

**2.3 · "Forgot password?" is too small and hidden.** Same size as the rest of the form. Someone reading it
has already had a bad minute — it should be the easiest thing on the card to find.

**2.4** While here: check the SSO and "Book a demo" links for the same contrast problem.

## 3 · New-device login alert

**Requirement:** when a new device signs in, email the account owner.

- Reuse `send_email()` (`backend/app/email.py:21`) and the existing template shape.
- Reuse the existing device/session record — login history already exists (`/v1/auth/login-history`), so
  "new device" is a question the data can already answer.
- Include: time, approximate location if available, device/user-agent summary, and a **revoke this session**
  link.
- Fire **asynchronously** — never inside the login request path. Follow the `user_notifications_loop`
  pattern (own thread, own session) so a slow mail provider cannot stall a sign-in.
- Respect the user's notification preferences, but treat security alerts as **not** opt-out-able by
  default.

## 4 · Payment gate at signup

**Owner decision (2026-07-28): card captured, never charged without consent.**

**4.1** Card collected and validated at signup — a **$0 authorisation**, not a charge.
**4.2** Free tier stays free. **No charge without an explicit upgrade action by the user.**
**4.3** Dashboard is locked until a card is on file.
**4.4** Email 3–4 days before anything changes, per the owner's note.
**4.5** Cancellation must actually work and be reachable without contacting support.

**⚠ The one item with legal exposure.** Collecting card details for a "free" tier is regulated in several
jurisdictions, and the consent copy is what determines whether this is fine or a problem. **The signup
consent wording needs a human read before launch** — do not ship copy an executor wrote unreviewed. Flag it
to the owner explicitly rather than burying it in a commit.

**4.6** Honest empty/locked state: a user without a card sees a clear explanation of what is needed and
why, not a broken dashboard.

## 5 · First-run tutorial

**Owner decision: skippable, but it comes back.**

**5.1** Step-by-step arrow walkthrough highlighting the next control, "next step → next step".
**5.2** **Esc always exits. A skip control is always visible.** The original request was for a blocking
tutorial; that traps users who hit a bug mid-flow and is a genuine accessibility failure.
**5.3** If skipped, it **re-offers on next login** until completed, and stays available from the help menu.
This gets near-blocking completion without the trap.
**5.4** Fully keyboard navigable; focus moves to each highlighted step; screen-reader announces step
`n of m`. Respects `prefers-reduced-motion`.
**5.5** **Behind a feature flag**, so it can be switched off instantly if it misbehaves in production.
**5.6** Completion state persists server-side (per user), not in localStorage — it must survive a new
device.

## 6 · Notifications that actually work

**Owner report:** *"Notifications currently have no settings, most buttons don't work / do nothing."*

**6.1** Audit **every** notification control in Settings. For each: make it real, or remove it.
**A dead switch is worse than an absent one** — it teaches the user the product lies.
**6.2** Wire the preferences to the existing `_ALLOWED_PREFS` path in `backend/app/routers/account.py`.
Three toggles were already made real in an earlier phase (breach alerts, digest, rotation reminders) —
follow that pattern rather than inventing a second one.
**6.3** Each toggle needs a test proving the setting changes actual behaviour, not just that it persists.
**6.4** Notifications page paginated (P2 §3).

## 7 · Security surfaces

**7.1 · 2FA gating on sensitive reveals** — org ID reveal and password change both require step-up first
(P2 §11.5 depends on this). The backend already gates seven endpoints with step-up; reuse
`/v1/auth/step-up/request` + `/confirm`, and remember the grant lives **inside the refreshed session
cookie**, so the new cookie must be persisted.
**7.2 · Session/device list** with individual revoke and "log out everywhere".
**7.3 · Login history** surfaced in Settings (the endpoint exists).
**7.4** While in this area: confirm MFA enrol/disable still behaves after §1's session changes.

---

## Verification

```bash
cd backend && python -m pytest tests/integration -q     # per-file if TRUNCATE deadlocks
alembic heads                                           # exactly one
```

- **§1 regression test is the gate for this whole plan**: change password → still authenticated → logout →
  login with new password → succeeds.
- New-device email fires on a genuinely new device, not on every login. Assert it does **not** block the
  login response.
- Payment gate: $0 auth succeeds, free tier is never charged, cancellation reachable. Consent copy
  reviewed by a human — record who and when.
- Tutorial: Esc exits from every step; keyboard-only completion; returns after skip; feature flag kills it.
- **Every notification toggle changes real behaviour** — assert the effect, not the persistence.
- Step-up: sensitive reveals refuse without a grant and succeed with one; the refreshed cookie is kept.
- No secrets in logs or responses; nothing serialises `password_hash`, `key_hash` or `token_hash`.
- Push to `main` deploys production — watch the CD run to green.

## MAIN ↔ EXECUTOR protocol

1. **Every message ends with a prompt for the other side**, both directions. If nothing is queued, say so
   explicitly.
2. `TASK <n> — P3 §<x>` · branch `feat/dash-p3-<slug>` · report opens
   `TASK <n> · <branch> · <SHA> · DONE|BLOCKED`.
3. Prompts are self-contained — a fresh chat with no history starts from the block alone.
4. Gate: FF-safe · scope grep · no-fake-data · **no-secret grep** · single Alembic head · full backend
   suite · merge by SHA push · watch deploy.
5. Stale tasks are deleted on merge, in the same action.
6. **§1 ships alone, first, on its own branch.** Do not bundle a lockout fix with feature work — it needs
   to be revertable on its own.
