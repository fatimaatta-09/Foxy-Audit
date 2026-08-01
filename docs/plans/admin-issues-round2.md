# Admin Console — issues round 2

**This file is the single source of truth.** If scope changes, it changes here
first and the executor prompt is re-issued.

Source: `G:\My Drive\Life\03 Projects\Foxy Audit\Admin Console\Admin issues new.md`
(9 items, 3 screenshots). Planned 2026-08-02, against `main` @ `b682bc5`.

## Context

The seven-phase punch-list shipped yesterday and is live. This is the owner's
follow-up list after using it: three password-change gaps, two visual defects
that P4 introduced, one new feature, one deletion, and one question.

Two things are worth knowing before reading the items.

**Items 4 and 6 are the same component.** `opsChip()`
(`foxy-adminpage/index.html:2360`) emits `<span class="chip safe">`, so the
unreadable `OK` on the health hero cards and the templated `ACTIVE` in tables are
one chip in two contexts. **This is a P4 regression I caused**: I kept red, amber
and green out of the card faces specifically so a status pill would read on top
of them, then never checked the pill itself against a face. `.chip.safe` uses
`--ok-soft`, a translucent green, which turns to mud over teal.

**Item 7 is smaller than it looks and riskier than it sounds.** The credit
machinery already exists. What does not exist is any way for an existing account
to redeem anything — see P4 below, and read its warning before writing code.

### Owner decisions (asked and answered 2026-08-02)

1. **The credit link must work for existing accounts too**, not just new signups.
2. **The info (i) button swaps in over the glyph on hover** — with a keyboard and
   touch path, which the choice does not give for free. See P3.
3. **Chips become solid fill, no border.**

### Item 8 — the answer, no work required

**Leads is the pre-sales pipeline.** `backend/app/routers/admin_leads.py` is a
kanban over the `marketing_leads` table, bucketed `new → trial → converted →
churned`, carrying UTM attribution (`utm_source`/`medium`/`campaign`) and the
`converted_org_id` link to the org a lead became.

It is **distinct from the Inbox**, which shows only the leads that sent a
message. Leads shows everyone who ever hit the funnel, whether they wrote to you
or not. Reads are viewer-gated; moving a lead's status is operator+ and audited.

In short: Inbox is "who is talking to us", Leads is "who is in the pipeline and
where did they come from".

---

## Phases

`P1` is backend-only and runs in parallel. `P2 → P3 → P4` all edit
`foxy-adminpage/index.html` and are **strictly sequential** — same phase-stacking
rule as last time.

| Phase | Branch | Items |
|---|---|---|
| ~~P1~~ | ~~`feat/admin-pw-reuse-block`~~ | **✅ merged 2026-08-02 at `f4d2889`** — both surfaces |
| ~~P2~~ | ~~`feat/admin-pw-modal`~~ | **✅ merged 2026-08-02 at `05a390a`** — form, confirm, meter |
| ~~P3~~ | ~~`feat/admin-chips-info`~~ | **✅ merged 2026-08-02 at `c31fb57`** — chips, (i), Recent Activity |
| P4 | `feat/admin-credit-link` | 7 — credit via shareable link |

Every phase: `git fetch origin && git worktree add ../wt-<phase> origin/main`.
Re-check `git merge-base --is-ancestor origin/main HEAD` **immediately before
pushing**, not when branching. `ui-ux-pro-max` then `frontend-design` on P2–P4.
`code-review` before every merge.

**Never spell the CI skip marker out in a commit body** — GitHub substring-matches
the whole message and will silently skip the run. Write "the skip marker".

---

## P1 — Reject reusing the current password (backend)

> "You can change your password to the exact same password you already have and
> no error is shown."

`backend/app/routers/auth_staff.py:342` verifies the current password, then hashes
whatever it was given. Nothing compares the two.

Add, after the current-password check and before the rehash:

```python
if bcrypt.checkpw(payload.new_password.encode("utf-8"),
                  staff.password_hash.encode("utf-8")):
    raise HTTPException(status_code=400,
                        detail="new password must be different from your current one")
```

Order matters: the current-password check stays first, so a wrong current
password never leaks whether the new one matches.

### ⚠ Word it WITHOUT "current" — P2 routes focus on that substring

P2 shipped `(/current/i.test(m) ? cur : nw).focus()` so a server error lands on
the field it is about. `"current password is incorrect"` matches and focuses the
current field, correctly. **The message above also contains "current", so it
would focus the current field too — and it is about the new one.**

Use wording that does not collide:

```python
detail="new password must be different from your previous one"
```

The substring test is fragile either way; hardening it is P3's job, since P3 is
the next phase touching `index.html`. Until then the wording is what keeps the
two apart, so do not reintroduce "current" here.

**Also apply it to the customer path.** Grep for the `/v1/auth/change-password`
handler and give it the same guard — the same gap almost certainly exists there,
and fixing one surface only is how the two drift.

**Tests** (`backend/tests/integration/`): same password → 400 with that detail ·
different password → 200 · wrong current password + same new password → the
current-password error, not the reuse error.

---

## P2 — The change-password modal

> Items 1 and 3: add a confirm field, and a strength indicator.

`pwModal()` / `_doChangePw()` (`foxy-adminpage/index.html:3503-3517`) is a modal
with two bare inputs and a click handler.

**The Dashboard already solved this.** Port the pattern from
`foxy-dashboard/foxy-audit-premium.html:2160-2176` (markup) and `4658-4676`
(`window.changePassword`). It has the confirm field, `.pw-wrap`/`.pw-eye` show/hide
toggles, an `aria-live` status line, and per-field focus on error.

### ⚠ The defect neither item names, and it is the worst one here

The Dashboard's version is wrapped in a real `<form>` and calls
`navigator.credentials.store(new PasswordCredential(...))` on success. The admin
modal is **not a form**. That is exactly the bug recorded in the
`password-lockout-root-cause` memory: with no form, the browser's password
manager never learns the new credential, so the saved one goes stale and the next
sign-in fails with a password the user believes is correct.

**Changing an admin password today can lock you out of the admin console.** Fix it
in this phase: make the modal body a `<form>` with a submit button, and call
`_storeCredential` after a successful change.

### The strength meter (item 3)

Write it in-file, no dependency — zxcvbn is ~400 KB and this surface ships zero
runtime dependencies by discipline. Score on length and character-class variety,
show four states (weak / fair / good / strong) as a labelled bar.

**It advises, it does not block.** The only hard requirement stays the existing
8-character minimum, enforced server-side. A meter that refuses to submit turns a
heuristic into a policy, and this one is a heuristic.

---

## P3 — Chips, the hero info button, and Recent Activity

### Items 4 + 6 — one chip, solid fill

Current definition at `foxy-adminpage/index.html:552-562`: tinted background,
`color-mix` border at 30%, uppercase mono. Replace with a **solid fill and no
border**, using the existing status pairs that were designed together —
`--safe-bg`/`--safe-tx`, `--breach-bg`/`--breach-tx`, `--warn-bg`/`--warn-tx`.

Solid fill is what fixes item 4: an opaque chip reads on a decorative hero face,
where a translucent tint does not. One change, both symptoms.

Keep `.chip.dim` quiet — it marks absence (`never`, `not_applicable`) and should
not shout in a status colour.

**Measure both contexts.** Every chip must clear 4.5:1 for its own ink, on the
page surface *and* on each of the nine card faces it can appear over
(`.k-azure` … `.k-teal`). The faces are listed in
`docs/done/admin-console-punchlist.md`.

**✅ Measured at `c31fb57` — and "a solid chip makes the face irrelevant" was
half wrong.** Solid fill fixes the *text* and breaks the *edge*.

| | worst | where |
|---|---|---|
| old translucent ink over a face | **1.55:1** | `.chip.safe` over `.k-teal` — item 4, quantified |
| new ink on its own fill | **4.78:1** | light `.safe` — every chip clears 4.5 |
| new **fill against the face behind it** | **1.01:1** | `#3ddc84` on `.k-teal`'s light stop |

At 1.01 the word is perfectly legible while the pill dissolves into the card and
reads as loose text. The owner ruled out a border, so the boundary comes from a
1px drop shadow: `border:0` still holds, and a shadow is this surface's own
language rather than the `color-mix` outline that was removed. One dark shadow
covers all eighteen face stops because every face is bright in both themes, and
it is invisible on the page surface where the fill already carries 4.2–11.2:1.

Both worst-case figures were reproduced independently before the merge and
matched exactly. **Measuring only the ink would have passed this and shipped a
dissolving pill** — the fill needs measuring against its background too.

### Item 5 — the info button

Owner's choice: **the (i) sits top-right and swaps in over the glyph on
hover.** `.kpi .gly` is at `index.html:513` (38px, `top:11px right:13px`).

That choice is hover-first, so it needs two things it does not get for free:

- **Keyboard.** The (i) must be a real `<button>` in the tab order, and
  `:focus-visible` must reveal it exactly as hover does. A control reachable only
  by pointer is not reachable.
- **Touch.** There is no hover on a phone. Under `@media (hover: none)` the (i)
  is always visible and the glyph steps aside permanently.

For the content, **reuse `.datatip`** — the console's existing delayed
hover/focus tooltip: one shared node, auto-flips near edges, reduced-motion
aware, already used at 23 `data-tip` sites. Do not build a second tooltip.

Write real copy per card — what the number is and where it comes from. "Shows the
overall status" is not worth a button.

### Also in P3 — the anti-drift guard is weaker than its name

`test_reuse_message_does_not_say_current` asserts `"current" not in
REUSE_DETAIL`, but `REUSE_DETAIL` is a **constant defined in the test file**, not
imported from the app. It compares a literal to itself and cannot fail when a
handler changes.

Drift *is* caught — by `test_staff_same_password_rejected` and its customer twin,
which assert `response.detail == REUSE_DETAIL`. Verified by reverting the wording
in a worktree: those two failed, the one named for the job passed.

The protection is real, the attribution is not, and a guard that reads stronger
than it is will be trusted for more than it does. Point it at the live response
or import the string from the app.

### Also in P3 — harden the error-focus test

`_doChangePw` routes focus with `/current/i.test(m)`, a loose substring match
over the server's message. It works today only because P1 was worded around it.
Replace it with something that cannot be broken by copy — match the specific
known error, or have the server return a code the client switches on. Leave a
comment saying why, or the next person will "simplify" it back.

### Item 9 — remove Recent Activity

Delete the Settings card at `index.html:1771-1772` (`#setActivity`) and its
loader `loadSelfActivity()` at `3495-3502`.

**Leave `#mActivity` alone** (`index.html:3457`) — that is the *staff member*
activity list inside the staff drill-down modal, a different feature that
superadmins use to audit someone else. Only the self-activity card on Settings
goes. Check whether `/admin/v1/auth/activity` still has another caller before
assuming the endpoint is dead; if it has none, leave the endpoint and note it.

---

## P4 — Credit via a shareable link

> "Choose how much credit to give → enter/confirm → complete 2FA → a link is
> generated. Anyone who uses that link gets the credits auto-added."

### What already exists — reuse all of it

| Piece | Where |
|---|---|
| `EvaluationCampaign` — `credits`, `duration_days`, `max_redemptions`, `status`, `starts_at`/`ends_at`, `code_hash` | `backend/app/models.py:109` |
| `_claim_database_campaign` — advisory lock, `FOR UPDATE`, window checks, per-email cap, capacity | `backend/app/routers/billing.py:88` |
| `_normalise_offer_code`, `_offer_code_hash`, `_offer_email_hash` | `billing.py:70-81` |
| `EvaluationRedemption` — the audit + capacity row | `models.py:84` |
| Campaign create + revoke, and the "shown once" code UI | `admin_campaigns.py`, Campaigns page |

**No migration.** The schema already carries everything.

### ⚠ Read this before writing the redeem endpoint

Redeeming does **not** simply add credits. Setting `evaluation_offer_id` switches
the org onto a *capped, expiring* regime — `backend/app/routers/logs.py:168-186`
refuses ingestion with 402 `evaluation_credits_exhausted` once
`evaluation_credits_used` passes the limit, and 402 `evaluation_expired` after
`evaluation_ends_at`.

So handing an existing org an evaluation offer can **reduce** its access and then
break its ingestion when the offer lapses. On a free-tier org that is an upgrade;
on anything else it is a downgrade wearing a gift's clothes.

**Guards — refuse rather than guess. All four are assumptions; overrule any of
them and say so:**

1. Refuse if the org has an active Stripe subscription or `plan_tier` is not
   `free`. Never let a gift silently replace a paid plan.
2. Refuse if `evaluation_ends_at` is in the future — one offer at a time, no
   stacking.
3. Refuse if this org already redeemed this `offer_id`.
4. Do **not** touch `plan_tier`, `monthly_log_quota` or `trial_ends_at` on an
   existing org. Set only the four evaluation fields. Signup sets the others
   because there is no plan yet; here there is.

### The build

**Backend — `POST /v1/billing/redeem`**, authenticated, `require_step_up_user`.
Body `{code}`. Call `_claim_database_campaign(db, user.email, code)` — it already
returns `{offer_id, email_hash, credits, expires_at}` and has done every capacity
and window check. Then apply the four fields under `SELECT … FOR UPDATE` on the
org, write the `EvaluationRedemption` row exactly as signup does
(`billing.py:255`), and return remaining/expiry for the UI.

**Backend — step-up on campaign create.** `create_campaign`
(`admin_campaigns.py:91`) is superadmin-only but not step-up gated; item 7 asks
for 2FA. Add `dependencies=[Depends(require_step_up_dep)]`, the same gate the
other 23 danger endpoints use.

**Admin UI — the link.** On successful create, alongside the plaintext code shown
once, render `<dashboard_url>/redeem?offer=<code>` with a copy button. Same
one-time disclosure rule: it is the code in a URL, so it is exactly as secret as
the code. Say so in the UI.

**Dashboard UI — the landing.** `/redeem?offer=…`:
- signed out → route to signup with the code pre-filled (the existing path)
- signed in → show what the offer grants, require the step-up, then `POST /v1/billing/redeem`
- refused → show the actual reason from the guard list, not a generic failure

**Tests:** each guard refuses with its own message · a clean redeem sets exactly
the four fields and writes one redemption row · `max_redemptions` and the
per-email cap still hold on this path · two concurrent redeems of the last slot
leave one winner (the advisory lock already covers this — prove it) · the step-up
is enforced on both the create and the redeem.

---

## Verification

Per phase, before merge:

```bash
# both inline <script> blocks must parse
node --check <extracted-1.js> && node --check <extracted-2.js>

# <style> braces — count EVERY block, not the first. Checking only block 1
# reads 7/7 and looks clean while block 2 is broken.
python -c "import re,io; s=io.open('foxy-adminpage/index.html',encoding='utf-8').read(); \
  [print(b.count('{'), b.count('}')) for b in re.findall(r'<style[^>]*>(.*?)</style>', s, re.S)]"

# the console's guards (98 today) — extend them each phase
python -m pytest foxy-adminpage/test_admin_shell.py -q

# backend — P1 and P4. DATABASE_URL must be set explicitly: conftest defaults to
# port 5432 and Postgres here listens on 5433 (Worth Noting #33), and without it
# every test errors inside `alembic upgrade head` looking like a migration fault.
cd backend && DATABASE_URL=postgresql+psycopg://foxy:foxy@localhost:5433/foxy_pytest \
  python -m pytest tests/integration -q
```

**Screenshots, both themes, and actually look at them.** A bare `file://` open
shows only the login gate, so build a harness that lifts the real `<style>`,
`<nav class="dock">` and `<header class="topbar">` out of the file. Add
`--force-prefers-reduced-motion` or infinite animations eat the virtual-time
budget and the capture never settles. `--screenshot` needs an absolute path.
Never `taskkill` chrome by name — it closes the owner's browser.

**Contrast:** P3 must measure every chip against the page surface *and* all nine
card faces. That measurement not being done is what caused item 4.

---

## After each merge

1. `git push origin <sha>:refs/heads/main`. Pushing to `main` deploys.
2. Watch the CD run to green. If no run appears at all, check the commit message
   for the skip marker before suspecting GitHub.
3. Update `Admin Console\CLAUDE.md` — affected section, `updated:`,
   `verified-against:`.
4. Dated entry in `Devlogs\2026-08-02.md`.
5. Noticed-but-not-fixed → `Worth Noting — Issues`. Got it wrong →
   `Where Claude Was Wrong`.
