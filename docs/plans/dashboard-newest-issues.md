# Customer Dashboard — newest issues

Source: `G:\My Drive\Life\03 Projects\Foxy Audit\Dashboard\NEWEST DASHBAORD ISSUES.md`
(6 items, 3 screenshots). Planned 2026-08-02 against `main` @ `5b2999d`.

## Context

Six items against `foxy-dashboard/foxy-audit-premium.html` after the admin
console's two rounds. **Two of them are already partly built, and one of those is
built in the backend and simply not wired up** — so the work is smaller than the
list looks, and mostly consists of consuming things that already exist.

### What is already done, verified on `main`

**Item 4 is two-thirds shipped.** The confirm field exists (`setNewPw2`, 4
references), the mismatch check is live at line 4666, the `.pw-eye` reveal
toggles are there, the form already calls `navigator.credentials.store`, and
**yesterday's P1 added the reuse block to the customer path too**
(`auth_human.py:608`, "must be different from your previous one"). The dashboard
is where the admin console *copied that pattern from*.

**Only the strength meter is missing** — zero `strength` references in the file.
Port `_pwMeter` from `foxy-adminpage/index.html`, where P2 built it: ~15 lines,
no dependency, four labelled states, and it **advises without gating** — the only
hard rule stays the server's 8-character minimum.

**Item 6's lock is fully built server-side and never called.** `_enforce_card_gate`
(`backend/app/auth.py:178`) raises **402 `card_required`** on every non-exempt
request, and `GET /v1/billing/access` (`billing.py:657`) exists *specifically* to
explain that 402 — its docstring says so. The dashboard references
`billing/access` **zero** times. The lock does not need building; the UI that
consumes it does.

### Owner decisions (asked and answered 2026-08-02)

1. **Item 1 — remove both**: the Recent activity card on home *and* the two
   explainer cards on Settings.
2. **Item 6 — also gate a failing subscription**, not just a missing card.
3. **Item 3 — Back always returns home and never exits the dashboard.**
4. **Item 2 — the Settings sections collapse.** Reviewed as a working render of
   the real markup before approval; see D3.

Two of those I argued against. Both are implemented as asked, with the honest
caveat recorded where it belongs rather than dropped:

- **History trapping is not enforceable.** Holding the Back button or using the
  history dropdown escapes any self-pushing loop, in every browser. It will work
  for an ordinary click and cannot be made to work for a determined one. Build
  it; do not describe it as a guarantee.
- **`past_due` locks a paying customer out while their bank retries.** That is a
  normal, recoverable Stripe state that often clears itself within days. See D1.

### The concern I raised on item 1, now resolved

"What Foxy stores" is the dashboard's plain-English content-blindness statement,
and deleting the product's core claim would be a poor trade for tidiness. It
turns out the claim survives in **five** other places — lines 1504 ("Evidence
boundary"), 1534, 1790 ("the Judge is content-blind either way — never your
prompts or responses"), 1860 and 1875. Removing the card loses nothing. No action
needed; recorded so nobody re-opens it.

---

## Phases

`D1` is backend-only and runs in parallel. `D2 → D3 → D4` all edit
`foxy-audit-premium.html` and are **strictly sequential**.

| Phase | Branch | Items |
|---|---|---|
| D1 | `feat/dash-lock-states` | 6 (backend half) — what counts as unpaid |
| D2 | `feat/dash-nav-and-cards` | 1, 3, 5 — remove cards, fix Back, scroll the dock |
| D3 | `feat/dash-settings` | 2, 4 — reorganise + redesign Settings, relabel, strength meter |
| D4 | `feat/dash-payment-lock` | 6 (UI half) — consume the gate |

Every phase: `git fetch origin && git worktree add ../wt-<phase> origin/main`.
Re-check `git merge-base --is-ancestor origin/main HEAD` **immediately before
pushing**. `ui-ux-pro-max` then `frontend-design` on D2–D4. `code-review` before
every merge. **Never spell the CI skip marker out in a commit body** — GitHub
substring-matches the whole message and silently skips the run.

---

## D1 — What counts as unpaid (backend)

Today there are two separate 402s and neither covers "pending":

| Gate | Where | Fires when |
|---|---|---|
| `card_required` | `auth.py:178` | `require_card_on_file` **and** no card. Default **off**, with a grandfather clause. |
| `subscription_inactive` | `logs.py:128` | plan not free **and** status in `{cancelled, unpaid}` — blocks *capture only*, never the dashboard. |

Add `past_due` and `incomplete` to the locked set, and surface the reason.

**`/v1/billing/access` must say which condition applies**, not just `locked:true`.
The UI has to render "add a card" differently from "your last payment failed" —
they need different actions from the user. Extend the response with a
machine-readable `reason` and keep `message` human.

**⚠ `past_due` is recoverable and common.** Stripe retries a failed charge over
days; a customer whose bank declined once is not a customer who stopped paying.
Locking them out instantly is a support ticket and a churn risk.

**Proposed, and stated as an assumption to overrule:** `past_due` locks the
dashboard but **not** capture — evidence keeps being recorded, which is the thing
they would most regret losing — and only after a grace window measured from
`current_period_end`. `incomplete` (the subscription never activated) locks
immediately, because nothing was ever paid.

**Tests:** each state maps to the right `reason` · `past_due` inside the grace
window is not locked · capture still succeeds while `past_due` · a free-tier org
is never locked by the subscription gate · the grandfather clause still exempts
old orgs from the card gate.

---

## D2 — Cards, Back, and the dock

### Item 1 — remove both (owner: both)

- **Home:** delete `injectHomeCard()` and its `#homeActivityCard` /
  `#homeActivityFeed` / `#homeActivityStamp` (around line 5861).
- **Settings:** delete the two explainer cards, "How the fingerprint works"
  (~2197) and "What Foxy stores" (~2207).

Check whether `loadAccountAudit()` and the "Account activity" card at 2300 still
have a caller once the home card is gone — that is a *different* card and stays
unless D3's reorganise removes it. Do not delete an endpoint's only consumer
without saying so.

### Item 3 — the Back button

**Root cause:** the file calls `replaceState` three times (3807, 3890, 5423) and
`pushState` **never**. In-dashboard navigation creates no history entries at all,
so the first Back leaves for whatever preceded the dashboard — usually the sales
page.

Owner's choice: **Back always lands on home and never exits.**

- `pushState` on each `go()` so pages become history entries.
- A `popstate` handler that navigates internally instead of unloading.
- From home, re-push home so Back does not leave.

Keep `cleanUrl()`'s existing behaviour of stripping tokens and `?handoff` — those
`replaceState` calls are deliberate and must not become `pushState`, or a
one-time token ends up in the history.

**Write the caveat in a comment where the loop is:** holding Back or opening the
history dropdown defeats this in every browser. It stops an accidental click,
which is what was asked; it is not a guarantee, and the next person should not
believe it is.

### Item 5 — scroll the dock

**Root cause:** `.dock` (line 258) is `position:fixed; top:0; bottom:0` with
`display:flex; flex-direction:column; gap:6px`, and **no `overflow-y`**. On a
short viewport the items have nowhere to go, so flex compresses them into the
squished rectangles in the screenshot. This is a **height** problem, not a width
one — below 820px the dock already becomes a drawer (line 783), so the bug lives
between "wide enough for the rail" and "tall enough for eleven items".

Fix: `overflow-y:auto` on `.dock`, plus `flex-shrink:0` on the items so they keep
their size and the column scrolls instead. Give it the thin themed scrollbar the
rest of the surface uses, and check the logo and the log-out control still reach.

---

## D3 — Settings: reorganise, redesign, relabel, and the meter

### Item 2 — order by how often it is used

Current order is roughly how it accreted: Data & privacy (2179), Team (2248),
Devices (2283), Account activity (2300), SSO (2305), Webhooks (2329), Judge
sensitivity (2347), After grading (2370), Before the call (2431), Breach alerts
(2470).

**Seven sections, shown to the owner as a working render of the real markup and
approved there** — the review build lifted each card's shipped `<div>` out of the
file by balancing braces rather than redrawing it, so what was approved is what
the page renders.

| # | Section | Cards, in order | Line today |
|---|---|---|---|
| 1 | **Your account** — the things you came here to change | profile + password · two-factor · devices & sessions · recent sign-ins · account activity | 2097, 2264, 2282, 2287, 2299 |
| 2 | **Team & access** — who can get in | team · access control (IP allow-list) · enterprise SSO | 2247, 2258, 2304 |
| 3 | **Policy & grading** — read often, changed rarely | judge sensitivity · before the call · after grading · where breach alerts go | 2346, 2430, 2369, 2469 |
| 4 | **Connect** — set up once per surface | connect the SDK · desktop app · outbound webhooks | 2226, 2214, 2328 |
| 5 | **Share & verify** — what other people can see | trust badge | 2185 |
| 6 | **Data & privacy** — last on purpose, you should have to travel here | danger zone (export CSV/JSON + delete workspace) | 2492 |
| 7 | **Help & support** — not a setting, a way out of the page | help & support | 2241 |

That is **18 cards**, which is the 22 on the page today minus the two explainer
cards D2 removes and minus the two bare section headers being replaced by real
sections. Every existing control survives — this is a reorganise, not a cull.
Section 7 exists because "Help & support" is a link-out that belongs to none of
the six; it gets its own quiet row rather than being dropped.

### Item 2c — the sections collapse (owner's addition)

Each section is a **`<details>` / `<summary>`**, not a JS accordion: keyboard
operation, the expanded state exposed to assistive tech, and browser find-in-page
working *inside* a collapsed section all come free, and none of it survives being
hand-rolled.

Reference CSS from the approved build — port it, restyle it if the design pass
improves it, but keep every behaviour it encodes:

```css
.sgrp{margin-bottom:12px;background:var(--bg2);border-radius:var(--r-lg);
  box-shadow:var(--raise-sm);overflow:hidden;}
.sgrp-h{display:flex;gap:11px;align-items:center;padding:13px 16px;
  cursor:pointer;list-style:none;user-select:none;}
.sgrp-h::-webkit-details-marker{display:none;}   /* Safari's own triangle */
.sgrp-h:hover{background:var(--surf);}
.sgrp-h:focus-visible{outline:2px solid var(--fox);outline-offset:-2px;}
.sgrp-n{font-family:var(--mono);font-size:11px;font-weight:800;
  color:var(--foxink);background:var(--fox);width:24px;height:24px;
  border-radius:8px;flex:none;display:grid;place-items:center;}
.sgrp-t{flex:1;min-width:0;}
.sgrp-h h2{font-size:15px;font-weight:800;color:var(--ink);letter-spacing:-.01em;}
.sgrp-h p{font-size:11.5px;color:var(--muted);margin-top:1px;}
.sgrp-c{font-family:var(--mono);font-size:9px;color:var(--muted2);flex:none;
  letter-spacing:.04em;}
.sgrp-v{width:17px;height:17px;color:var(--muted);flex:none;
  transition:transform .18s ease;}
.sgrp[open] .sgrp-v{transform:rotate(180deg);}
.sgrp-b{padding:2px 14px 12px;}
/* the cards already carry their own bottom margin; the last one would
   otherwise leave a dead gap inside the shell */
.sgrp-b > :last-child{margin-bottom:0 !important;}
@media(prefers-reduced-motion:reduce){ .sgrp-v{transition:none;} }
```

Summary row, left to right: **number chip · title · one-line subtitle · a
settings count (`5 settings` / `1 setting`) · a chevron**. Two traps worth
naming — Safari draws its disclosure triangle from `::-webkit-details-marker`
rather than `list-style`, so both need suppressing; and `:focus-visible` on the
summary is not optional, because the row *is* the button.

**Section 1 open by default.** The page should not greet you fully closed.

This is what settles the length question raised against the first draft: closed,
all seven sections and the whole page fit on one screen in both themes (verified
at 1400×900). **Do not split "Your account" into Account + Security** — that buys
a shorter open section at the cost of an eighth row and a second place to look
for the password field, and the accordion already gives the one-screen map the
split was reaching for.

### Item 2b — the relabel

`+ add auditor` (line 2248) → **"Add teammate"**. "Auditor" is the product's word
for a role; the button adds a person, and the person is not necessarily an
auditor. Check whether "auditor" appears in the invite modal and the empty state
and make those agree — a renamed button that opens a dialog still saying
"auditor" is worse than not renaming it.

### Item 4 — the strength meter

Port `_pwMeter` from `foxy-adminpage/index.html`. Everything else item 4 asks for
already exists here (see Context).

**It advises, it does not block.** No disabled submit, no refusal. The 8-character
minimum is the only hard rule and the server owns it. Hint at the lever ("length
helps most") rather than passing a verdict. It sits under the confirm field,
above `#setPwStatus`.

**Do not rebuild the confirm field or the reuse check.** Both exist. Verify the
server's reuse message renders legibly in `#setPwStatus` and stop there.

---

## D4 — The payment lock (UI)

Consume what D1 and the existing gate provide. **No new lock logic here.**

- Call `/v1/billing/access` on boot; render the locked state from `reason`.
- Catch **402** globally in the `fetch` wrapper. The dashboard has two `fetch`
  patches already (see `Dashboard\CLAUDE.md`) — extend those, do not add a third.
  A 402 must render the locked state, **not** bounce to the login screen: that
  distinction is exactly why `auth.py` chose 402 over 401/403, and its comment
  says so.
- The locked state must be **honest and actionable** — say which condition
  applies and what clears it. `/v1/billing/access` is documented to stay readable
  while locked; it is the one thing that must not break in this state.
- Leave the exempt paths reachable. Billing, sign-out and support must work while
  locked, or the user cannot fix the thing they are locked for.

**Nothing locks until `require_card_on_file` is turned on** (default `false`, and
that is a business decision, not a deploy). Test with the flag on; ship with it
as it is unless the owner says otherwise.

---

## Verification

```bash
# every <style> block, not the first — the dashboard has FOUR
python -c "import re,io; s=io.open('foxy-dashboard/foxy-audit-premium.html',encoding='utf-8').read(); \
  [print(i, b.count('{'), b.count('}')) for i,b in enumerate(re.findall(r'<style[^>]*>(.*?)</style>', s, re.S),1)]"

# every inline script parses
node --check <each extracted block>

# the dashboard's own guards, and the console's
python -m pytest foxy-dashboard/ foxy-adminpage/test_admin_shell.py -q     # 372 today

# backend — D1 only. DATABASE_URL must be explicit: conftest defaults to 5432,
# Postgres here listens on 5433, and without it every test dies inside
# `alembic upgrade head` looking exactly like a migration fault.
cd backend && DATABASE_URL=postgresql+psycopg://foxy:foxy@localhost:5433/foxy_pytest \
  python -m pytest tests/integration -q
```

**Screenshots, both themes, and look at them.** For D2's dock, capture a **short**
viewport (e.g. 1200×500) — the bug is height, not width, and a wide-and-tall
capture will not show it. For D3, capture the sections **closed** as well as open;
the closed state is the whole point of the change and a default capture hides it.
Use `--force-prefers-reduced-motion` or infinite animations eat the virtual-time
budget and the capture never settles. `--screenshot` needs an absolute path.
Never `taskkill` chrome by name.

**D3 must re-measure contrast** on anything it restyles. **Measure a fill against
its background, not just its ink** — round two shipped a chip whose text cleared
4.5:1 while the pill itself sat at 1.01:1 against the card behind it and
dissolved.

**D4 needs a live check**, not just tests: run the stack with
`REQUIRE_CARD_ON_FILE=true` against an org with no card and confirm the dashboard
renders the locked state instead of a broken page or a login bounce.

---

## After each merge

1. `git push origin <sha>:refs/heads/main`. Pushing to `main` deploys.
2. Watch CD to green. If **no run appears at all**, check the commit message for
   the skip marker before suspecting GitHub.
3. Update `Dashboard\CLAUDE.md` — affected section, `updated:`,
   `verified-against:`.
4. Dated entry in `Devlogs\2026-08-02.md`.
5. Noticed-but-not-fixed → `Worth Noting — Issues`. Got it wrong →
   `Where Claude Was Wrong`.
