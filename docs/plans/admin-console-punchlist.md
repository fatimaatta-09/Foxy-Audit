# Admin Console punch-list — the plan

Source: `G:\My Drive\Life\03 Projects\Foxy Audit\Admin Console\changes Admin website.md`
(filed 2026-07-31, 13 items, 8 screenshots). Planned 2026-08-01.
Interactive mock of the result: https://claude.ai/code/artifact/95899398-74be-4a83-990a-9ffc63f1722c

**This file is the single source of truth.** If scope changes, it changes here
first and the executor prompt is re-issued.

---

## Context

The complaint underneath the 13 items is consistent: the staff console
(`foxy-adminpage/index.html`, one file, 3,574 lines / 436 KB) has drifted away
from the customer Dashboard. Three competing skins, the page name printed twice,
dead one-line blurbs, scroll-forever tables, flat colourless pages, and layouts
that show different content at different widths.

**Most of this is a port, not a design exercise.** The Dashboard already solved
every one of these and the solutions are reusable code in
`foxy-dashboard/foxy-audit-premium.html`: it collapsed 6 skin×theme combinations
to one soft-UI skin (P1 §2), it has a page wordmark and corner bloom (P1 §5/§6),
a working `foxPager`, a top-bar crumb, and decorative hero cards. The console
gets those, adapted. It does **not** get a new design language invented for it.

### Owner decisions — taken before planning, do not re-litigate

1. **Full token port.** The Dashboard's palettes win in both themes, replacing
   the owner-picked "Burnt Orange (Bold)" dark. The skin axis goes away
   entirely. This deliberately overrides the "do not re-litigate" note in
   `Admin Console\CLAUDE.md` — it is an owner decision dated 2026-08-01, not
   drift. Record it as such when updating that note.
2. **Page heads lose all three lines** — eyebrow, `<h1>` and `.sub` come out of
   all 17 `.pagehead` blocks. Page identity moves to the top bar and the
   background wordmark.
3. **Hero card icons are drawn in-house**, not licensed. The icons8 3D Fluency
   assets were exported, tried and rejected as toy-like against this console —
   this reverses the standing "licensed over hand-drawn" preference *for this
   surface*. See P4.
4. **Card faces: nine of them, and no two in a row alike.** The category biases
   the choice (warm = can go wrong, cool = a count), but uniqueness within the
   row wins. Supersedes both the Dashboard's icon-derived rule and a five-face
   semantic scheme, each of which was tried and rejected. See P4.

### Out of scope

Any change to `/admin/v1/*` response shapes · `backend/app/middleware/security.py`
(shared with the customer app) · the Dashboard itself · inventing UI controls the
console does not already have.

---

## Phases

Every phase after P0 edits the **same single file**, so they are **strictly
sequential**. Each obeys the phase-stacking rule in `Admin Console\CLAUDE.md`:

1. `git fetch origin` then `git checkout -b feat/<phase> origin/main`. Never
   branch off a stale local main or a sibling feature branch.
2. Before pushing: `git fetch origin && git rebase origin/main`. Resolve
   conflicts to **preserve already-merged work** — when unsure whose a hunk is,
   keep the prior merge's version.
3. `git merge-base --is-ancestor origin/main HEAD` must succeed, and a marker
   from the previous phase must still grep clean.

A stale base here silently reverts merged work. It has already nearly wiped this
file once.

**Give each executor its own `git worktree`.** P0 and P1 ran concurrently in the
same checkout, and P1 found P0's half-written backend files dirty in its tree. It
staged by path and nothing crossed over, but that was care rather than isolation:
`git worktree add ../wt-<phase> origin/main`.

**CI and CD are failing on GitHub Actions billing**, not on code — every job
reports "the job was not started because recent account payments have failed."
Merges land on `main` but nothing deploys. Hold the production deploy until the
phases are done rather than putting a half-finished redesign in front of staff;
`deploy/` has a manual path when it is wanted.

| Phase | Branch | Covers |
|---|---|---|
| ~~P0~~ | ~~`feat/admin-staff-device-alert`~~ | **✅ merged 2026-08-01 at `9cfe3cb`** — staff new-device sign-in email |
| ~~P1~~ | ~~`feat/admin-p1-one-skin`~~ | **✅ merged 2026-08-01 at `1bc39b9`** — one skin, Dashboard palettes |
| P2 | `feat/admin-p2-shell` | top-bar text, sidebar icons + active state, strip page heads, Settings stacking |
| P3 | `feat/admin-p3-identity` | corner glow + per-page wordmark |
| P4 | `feat/admin-p4-heroes` | hero cards, colour and life on every page |
| P5 | `feat/admin-p5-pagination` | pagination on every scrollable table, blurb sweep |
| P6 | `feat/admin-p6-responsive` | same information at every breakpoint |

Mandatory skills: `ui-ux-pro-max` **first**, then `frontend-design`, on P1–P6.
`dataviz` on P4. `code-review` before every merge.

---

## P0 — Staff new-device sign-in alert (backend only)

> "When a new device logs into an account, email the account owner an alert
> about the new login."

The whole machine already exists for **customer** users. Mirror it; do not
rebuild it.

| Piece | Where |
|---|---|
| `is_new_device(db, user, user_agent)` | `backend/app/login_history.py:13` |
| `_DEVICE_QUEUE`, `describe_device()`, `enqueue_new_device_alert()`, `drain_new_device_alerts()`, `send_new_device_alert()` | `backend/app/user_notifications.py:226-330` |
| the customer call site | `backend/app/routers/auth_human.py:134-150` |
| the drain thread | `user_notifications_loop`, `user_notifications.py:602` |

Staff logins do none of this. Build the staff equivalent:

1. **No migration needed.** `StaffSession` (`backend/app/models.py:551`) already
   stores `ip` and `user_agent`. "New device" for staff = at least one prior
   `StaffSession` row exists for this `staff_user_id`, and none of them carries
   this `user_agent`. Revoked rows still count as "seen before" — revoking a
   session does not make the browser unfamiliar.
2. Add `_STAFF_DEVICE_QUEUE` + `enqueue_staff_device_alert` /
   `drain_staff_device_alerts` / `send_staff_device_alert` beside the existing
   ones. Reuse `describe_device()` verbatim — it never invents a device, and an
   unrecognised user-agent is shown as-is so the reader can judge it.
3. Call it from `_establish_staff_session` (`backend/app/routers/auth_staff.py:97`).
   Compute "is new" **before** `db.add(StaffSession(...))`, or the row matches
   itself and no device is ever new.
4. Drain from the same `user_notifications_loop`.
5. Build the email with `email_templates` + `surface="staff"`, matching the staff
   sign-in-code mail at `auth_staff.py:85-94`. Content: device string, IP,
   timestamp, and what to do if it wasn't them (Settings → Devices & sessions →
   log out everywhere, then change password).
6. **Not preference-gated.** Same reasoning as the customer path's comment at
   `user_notifications.py:227`: a switch that silences this is a switch that
   helps an attacker stay quiet. The `user_notifications_enabled` ops kill
   switch still applies — that exists to stop the mailer, not to configure the
   product.
7. It must never raise into a login. Queue, don't send inline.

**Tests** (`backend/tests/integration/`): a new user-agent sends · a known one
does not · the first-ever staff sign-in does not · a mailer exception does not
break the login.

---

## P1 — One skin, Dashboard palettes

> "Use one consistent skeuomorphism style across the whole site and drop the
> other skins… including the same dark-mode and light-mode colour palettes."

Port `foxy-dashboard/foxy-audit-premium.html` lines **~34–200** — the `:root`
token block, `html[data-theme="light"]`, the `--raise`/`--sink` soft-UI
elevation pair, and `--bloom` — into the admin's `:root` and light theme.

**Delete:**

- `html[data-skin="clay"]` and `html[data-skin="original"]`
  (`foxy-adminpage/index.html:213-301`) and every `[data-skin=…]` selector.
- The refraction filter `<filter id="foxGlassRefract">` (line 975), its
  `@supports` gate (line 534), and `--refract` / `--glass` / `--spec*` /
  `--glass-tint`.
- The film grain `body::after` (line 344) and `--grain`.
- The skin picker: the topbar button (line 1145), the three Settings segbuttons
  (1610-1612), `setSkin()` / `toggleSkin()` (2489-2493), the boot read at line
  13, the `foxy_admin_skin` key, and the command-palette entry (line 3520).

**Keep:** the density axis (`comfortable` / `compact`). The owner asked to drop
skins, not densities.

### ⚠ The trap that will break this phase silently

The console's JS and its inline `style=""` attributes read tokens **by name at
runtime** — `_cssvar()` (line 2049), `_chartPalette()` (2053), and per-KPI
`style="--k:var(--teal)"`. A rename compiles perfectly and blanks every chart.

The exact set referenced outside the `<style>` block:

```
--blue --blue2 --breach-bg --danger --fox --fox2 --info-bg --ink --k --k2
--line --mono --muted --muted2 --ok --safe-bg --surf2 --teal --violet
--warn-bg --warn-soft --warnc
```

The Dashboard defines **none** of `--blue --blue2 --ok --teal --violet --warnc
--danger --warn-soft --info-bg`, nor the console's focus token `--accent`
(plus `--accent-soft`, `--accent-ink`). Every one of those names must survive as
an alias onto a ported value — e.g. `--blue: var(--dec1)`,
`--violet: var(--dec2)`, `--ok: var(--safe-bg)`.

Regenerate the inventory mechanically after the port and assert the set is
unchanged:

```bash
awk 'NR>968' foxy-adminpage/index.html | grep -o -- '--[a-z0-9-]*' | sort -u
```

`_chartPalette()`'s order is **CVD-validated** — keep the order, re-point only
the values.

### Also in P1: the console's first test file

`foxy-adminpage/` has **zero** guards today; the Dashboard has 8. Add
`foxy-adminpage/test_admin_shell.py`, modelled on
`foxy-dashboard/test_p1_contrast.py`, asserting: no `data-skin` string survives ·
every runtime token name above is defined · `<style>` braces balance · both
inline `<script>` blocks parse. Extend it in each later phase.

---

## P2 — The shell: top bar, sidebar, page heads

### Top bar

> The top-bar text should read: `'foxylog' Foxy Audit | 'current page name' + .`

This is exactly the Dashboard's `.crumb`. Port CSS from
`foxy-audit-premium.html:302-325` and markup from `1390-1396`:

```html
<div class="crumb">
  <img class="crumb-logo" alt="" aria-hidden="true">
  <span class="crumb-brand">Foxy Audit</span>
  <span class="crumb-sep" aria-hidden="true">│</span>
  <b id="topbarTitle">Overview</b>
  <span class="livedot on">.</span>
</div>
```

Replace the console's static `.topbar-brand` (`index.html:1114-1118` — the
frozen "Foxy Audit / internal ops console"). Drive `#topbarTitle` from a `CTX`
map keyed by page, mirroring `setTopbarContext`
(`foxy-audit-premium.html:5122-5130`), called from the console's `go()` at
`index.html:2178`. Cover all 13 pages plus `org360`, `deadletter`, `anchors`,
`alerts`. `crumb-logo`'s `src` is the base64 logo already embedded in the file —
do not add a fetch.

### Sidebar

> Change the sidebar logos/icons. · The sidebar icons and their highlight/active
> state look bad — redesign them so the icons and the way they highlight look clean.

The rail (`.dock`, CSS `index.html:370-425`, markup `1045-1107`) keeps its
geometry. What changes:

- **Icons** — adopt the Dashboard's language: 1.9 stroke-width, round caps and
  joins, `currentColor`, 24-unit grid (examples at `foxy-audit-premium.html:1334`
  and `2517`).
- **Active state** — currently a `color-mix` tint plus a hard `inset 3px 0 0`
  orange bar. Under soft UI it should read as **pressed into** the rail
  (`var(--sink-sm)` over `var(--bg)`) — the same physical logic as every other
  control, not a decoration applied on top of one.
- **`.dock-mark`** loses its glass bevel and takes the soft-UI raise. On the
  light theme the mark is a light-on-dark fox and disappears on a near-white
  plate — invert it, the way the Dashboard treats its crumb logo.

### Page heads

All **17** `.pagehead` blocks lose their eyebrow, `<h1>` and `.sub`. Four
constraints:

- **The `.pagehead` element stays.** It becomes P3's wordmark/bloom host, and
  several already carry right-side controls that must survive: `orgs`
  "show offboarded" checkbox (`1277-1282`), `org360` action bar + Verify-chain
  button (`1303-1308`), `audit` Export CSV (`1546`).
- **`org360`'s eyebrow is navigation, not a label** — it holds
  `← organizations` (`index.html:1299`). Keep the link; move it above the head
  as a back-link. Do not delete it.
- **Only `.pagehead` eyebrows go.** The ~30 `.eyebrow` elements inside panels
  ("PLATFORM TREND", "LAST 7 DAYS", "GRADING QUEUE") label content, not the
  page, and stay.
- **Resize the head for what is left.** Most heads end up holding only the
  wordmark; a few also hold their existing actions. Left at its current height
  it reads as a dead band — the mock settled on ~68px with
  `align-items:center`, not `flex-end`.

### Settings layout

Screenshot `-2`: six Settings cards side by side across a 2559px viewport, most
half-empty. **Stack them vertically, top to bottom.** Cards at
`index.html:1596-1660+`. The "Surface style" segmented control is already gone
after P1; "Table density" stays.

---

## P3 — Page identity: corner glow + wordmark

> Add the orange corner glow shown here, plus the faint "Foxy" text in the
> background — on every page. The background word should match the current page.

Port both from the Dashboard verbatim (`foxy-audit-premium.html:1077-1100`):

- `.pg-head{position:relative;overflow:hidden;isolation:isolate}` — apply to
  `.pagehead`. Its own `overflow:hidden` is what stops a 100px+ word from ever
  producing horizontal body scroll.
- `.pg-wm` — absolute, right-bleeding, vertically centred, never centred
  horizontally; `clamp()` sized, `opacity:.05` dark / `.06` light; hidden under
  620px (it is decoration, not information — the one intentional exception to P6).
- `.pg-head::before` with `background:var(--bloom)` — the corner glow. `--bloom`
  arrives with P1 and is **already re-tuned per theme** (`.16` dark, `.10`
  light). Do not use one alpha for both: the value that reads as light on
  near-black reads as a printing smudge on paper.

Feed the word from the same `CTX` map P2 built, so the top-bar title and the
wordmark can never disagree. Home reads "Foxy Audit" per the request; every other
page reads its short name.

---

## P4 — Hero cards, colour and life on every page

> The hero cards here look bad — make them better: add icons and colour. ·
> The System Health page looks bad — no colour, no liveliness. This is true of
> every page, not just this one. Fix every page.

Port the Dashboard's hero card (`foxy-audit-premium.html:1106-1209`):

- `.stat.kpi` shell + `.face` decorative gradient + the `.beam` travelling rim
  light, phase-offset per card, behind `prefers-reduced-motion`.
- The face palette `.k-fox .k-blue .k-violet .k-pink .k-teal` (lines 1136-1157).
  **Red, amber and green are deliberately absent** — they stay reserved for
  status, which is the only reason a breach pill still reads instantly beside
  five coloured cards. Do not add them.
- Dark ink on decorative faces (`--foxink`), never white: white on the light end
  of these gradients measures 2.35–2.74:1.

### The face palette — nine faces, no repeats in a row

Owner decisions, 2026-08-01, in this order. Both are recorded because the second
one only makes sense against the first.

1. **The face should say what kind of number the card carries**, not take its hue
   from its icon. *(This replaced the Dashboard's icon-derived rule, which was
   tried first and failed here: with five faces and a console full of counts, the
   hue kept landing on the same two colours, and any icon sharing its card's hue
   flattened into it.)*
2. **But no two cards in a row may look alike.** Five semantic faces meant
   Overview rendered three identical blues; the owner rejected that on sight.

So the category is a **bias**, not a rule. Warm for things that can go wrong,
cool for counts, violet for money, teal for infrastructure, rose for identity —
and then within that bias, every card in a row gets its own face.

```css
/* Four of these are the Dashboard's exact values; the rest are minted on the
   same recipe. Red, amber and green stay out entirely, so a status pill still
   reads on top of any of them. */
.k-azure   .face{background:linear-gradient(145deg,#5fb3f5,#3b8fd0)}
.k-blue    .face{background:linear-gradient(145deg,#6d99ff,#5d7bd3)}
.k-indigo  .face{background:linear-gradient(145deg,#9a96f5,#7570d4)}
.k-violet  .face{background:linear-gradient(145deg,#a89bff,#8172d1)}
.k-magenta .face{background:linear-gradient(145deg,#f28ad6,#c25aa6)}
.k-rose    .face{background:linear-gradient(145deg,#ff7fb4,#ce5586)}
.k-coral   .face{background:linear-gradient(145deg,#ff9a7a,#e0613d)}
.k-orange  .face{background:linear-gradient(145deg,#ff8b42,#dd5f18)}
.k-teal    .face{background:linear-gradient(145deg,#4fd6cd,#20a89e)}
```

**Measure the five new ones.** `--foxink` clears 4.5:1 on the four inherited
values; `azure`, `indigo`, `magenta`, `coral` and the deep stops around them have
not been verified. The `.l` label is 9.5px body text sitting in the bottom-right
corner of a 145° gradient — the darkest part of the face — so measure there, not
at the light stop. Lighten a deep stop until it passes rather than darkening the
ink.

### Row assignments

| Page | Cards, left to right |
|---|---|
| Overview | azure · blue · indigo · **orange** *(breaches)* · rose |
| System health | teal · azure · **orange** *(failed grading)* · **coral** *(anchor issues)* |
| Traffic | azure · blue · indigo · **orange** *(errors ≥400)* |
| Revenue | violet · magenta · indigo |
| Org drill-down | blue · teal · indigo · orange |

The warm card in each row is the one that can be bad. That is the only part of
the assignment that carries meaning and it must not be traded away for variety.

A risk card that is *genuinely* bad goes red through `.k-status` / `.is-raised`
(`foxy-audit-premium.html:1073`) — it is never red at rest.

### Icons — drawn in-house, not licensed

Owner decision, 2026-08-01: the icons8 3D Fluency assets read as toy-like against
this console and are dropped. **This reverses the earlier "licensed assets over
hand-drawn" preference** for this surface.

The set is 18 duotone glyphs on one 24-unit grid — a filled plate at `.2` under a
1.6px stroked line, round caps and joins, **`currentColor` only**. They ship as a
single inline `<symbol>` sprite and are used as
`<svg class="gly"><use href="#g-orgs"/></svg>`.

`currentColor` is the point, not a detail: the glyph inherits the card's ink
(`--foxink` on a decorative face), so it can never wash out against the face
behind it, and it re-themes for free. That is the failure mode the licensed
assets had — they carried their own palette and fought whichever card they
landed on.

Sprite ids: `g-orgs · g-users · g-audited · g-risk · g-staff · g-health ·
g-worker · g-failed · g-anchor · g-marketing · g-app · g-admin · g-errors ·
g-revenue · g-subscription · g-trial · g-ledger · g-apikey`.

**The drawn set is in the mock** — lift the sprite from the published artifact
rather than redrawing it: <https://claude.ai/code/artifact/95899398-74be-4a83-990a-9ffc63f1722c>

Size: roughly **7 KB** for all eighteen, against ~97 KB of base64 the licensed
route would have added. `index.html` stays near its current 436 KB.

`foxy-adminpage/icons/` (the 16 icons8 WebP exports) is now **unused**. It is
git-ignored, so nothing needs undoing — leave it or delete it.

### ⚠ The `::before` collision — changed shape after P1, read this

The console's KPI cards use `.kpi::before` **as their accent bar**, and an
element has exactly one `::before`. Before P1 the file defended this by scoping
every decorative pseudo `.clay:not(.kpi)`.

**P1 deleted those decorative pseudos along with the skins**, so
`.clay:not(.kpi)` now appears **zero** times and `.kpi::before` is the only
`::before` left in play. That is not permission to stop worrying — it removes the
guard while leaving the thing it guarded. If P4 adds any decorative `::before` to
`.clay`, `.kpi` or a shared ancestor selector, it will silently blank the accent
bars on every KPI card and nothing will error.

Use `::after`, an extra element, or scope the new rule `:not(.kpi)` explicitly.
Verify by rendering a KPI row and counting the bars, not by reading the CSS.

**The rest of the page, not just the KPI row.** Carry the face palette out to
panel accents, chart series and status chips across all 13 pages, with System
Health as the reference case. Charts must keep resolving through
`_chartPalette()` so they re-theme — P1 changed only its fallback hexes, and its
CVD-validated order is still load-bearing. `dataviz` first.

**Hard rule:** every panel stays wired to a real endpoint. Honest empty states
only — no fabricated numbers to make a page look alive, and no invented controls.

---

## P5 — Pagination on every scrollable table

> Add pagination to every scrollable table (export-history table, ledger table, etc.).

The Dashboard already has the component. Port it; do not write one.

| Piece | Where |
|---|---|
| `.pager` CSS | `foxy-audit-premium.html:996-1012` |
| `window.foxPager(host,{page,pageSize,total,onPage})` | ~line 6040 |
| `window.foxPageSlice(items,state,host,onChange,label)` | ~line 6083 |

`foxPager` renders **nothing** when there is only one page — a pager over a
single page is furniture, not information. Keep that behaviour.

The console has a cruder `_pager(off,limit,total,fn)` at `index.html:2958`, used
by `o3LoadLedger` and `o3LoadBreaches`, plus an `auPager` host on the audit page
(`1553`). Replace `_pager` with `foxPager` and keep those three working.

There are **19 `<table class="tbl">`** and 23 `.twrap` scroll containers. Pick
per table by what the endpoint supports:

- **Server-side**, where `limit`/`offset` already exist — no backend change:
  `admin_orgs` (`:436`, `:523`) · `admin_audit_view` (`:62`) · `admin_billing`
  stripe-events (`:93`) · `admin_data` (`page`/`limit`, `:196`) ·
  `admin_staff/{id}/activity` (`:251`) · `admin_health/alert-history` (`:203`).
- **Client-side `foxPageSlice`** for the capped-list endpoints that return a
  bounded array with no offset: `admin_security/logins` ·
  `admin_grading/deadletter` · `admin_anchors` · `admin_alerts` · `admin_leads` ·
  `admin_staff` · `admin_campaigns`.

Do **not** add `limit`/`offset` to those endpoints here — it changes
`/admin/v1/*` response shapes, which is out of scope and needs its own review.
Slice client-side and note it.

### Also in P5: the blurb sweep

> Remove the useless one-line info blurbs from every page.

P2 already removed the `.pagehead` `.sub` lines. Sweep the remaining in-panel
one-liners that restate the obvious — e.g. `index.html:1619`, "Saved to this
browser only — doesn't affect other staff", sitting directly under a card whose
eyebrow already reads "local · this browser".

**Keep** any `.sub` carrying information the reader cannot get elsewhere. The
Data page's read-only-tables explanation (`1435`) is a real warning about the
tamper-evident chain, not a blurb.

---

## P6 — Responsive consistency

> The site should show the same information at every screen size. Right now
> different screen sizes show different content — some views look crowded,
> others too empty.

This is a real defect, not a taste call. Current breakpoints: `1100` / `680`
(`.bento`, `index.html:484-485`), `980` (`.split`, `801`), `760` (the block at
`935-957`) and `520` (`958`).

The 760 block is where information is **lost**: `.topbar-sub` and
`.topuser-meta` are `display:none` (line 955), and `.tbl.cardify` restructures
tables into stacked cards — but only **1 of 19** tables opts in (line 1380), so
the other 18 just scroll sideways. Which behaviour you get depends on which page
you happened to open.

1. Audit every `display:none` under a media query. Reinstate the content in a
   width-appropriate form instead of hiding it. Nothing may be visible at 1440px
   and absent at 768px.
2. Adopt `.cardify` uniformly across all 19 tables, with `data-label` on every
   cell.
3. Reflow the bento and `.split` grids so the same panels appear in the same
   order at every width. "Too empty" at wide widths means panels that should
   span are not spanning.
4. `.pg-wm` hiding under 620px is the one intentional exception.

Verify at **1440 · 1024 · 768 · 375**. Take the 375px shot **inside a sized
iframe**: headless Chrome clamps a `--window-size` of 375 to roughly 500px, so a
naive "375px screenshot" is a lie. This has caught us before.

---

## Verification — per phase, before the merge gate

```bash
# 1. Both inline <script> blocks must parse. The merge gate does this; do it first.
node --check <extracted-script-1.js> && node --check <extracted-script-2.js>

# 2. <style> brace balance — an unbalanced brace in a 3,574-line single file
#    is otherwise completely silent.
python -c "s=open('foxy-adminpage/index.html',encoding='utf-8').read(); \
           b=s[s.index('<style>'):s.index('</style>')]; \
           print(b.count('{'), b.count('}'))"

# 3. The console's guards (added in P1, extended each phase)
python -m pytest foxy-adminpage/test_admin_shell.py -q

# 4. Backend suite — P0 only. Native PG on :5433, db foxy_pytest.
cd backend && python -m pytest tests/integration -q
```

### Screenshots — actually look at them

A bare `file://` open only ever shows the login gate (the app calls
`/admin/v1/auth/me` on boot). Build a preview harness in the scratchpad that
inlines the real `<style>` block plus a component sampler, with `data-theme`
read from a query string.

```
chrome --headless=new --no-sandbox --disable-gpu --hide-scrollbars \
       --force-color-profile=srgb --virtual-time-budget=2500 \
       --window-size=1300,1100 --screenshot=<ABSOLUTE Windows path> file:///<ABS>
```

- `--screenshot` **must** be an absolute path — Chrome resolves relative paths
  against its own CWD and fails with "Access is denied".
- `--virtual-time-budget` is required, or the `rise` stagger is captured
  mid-fade and every panel looks half-transparent. If the page has *infinite*
  animations, they consume virtual time and the capture never settles — add
  `--force-prefers-reduced-motion` to get the settled state (which also verifies
  the reduced-motion path).
- **Never `taskkill` chrome by name** — it closes the owner's real browser.
  Bound the render subprocess instead.

### Contrast

Re-measure after P1 and P4. The prior redesign verified ≥4.5:1 across the board;
the palette swap invalidates every one of those numbers.

### Live check (P0)

`cd backend && docker compose up --build -d`, sign in to the console from a
second browser profile, confirm the alert lands, then confirm a second sign-in
from the same browser sends nothing.

---

## After each merge — mandatory

1. Push to `main` by **direct SHA**: `git push origin <sha>:refs/heads/main`.
   Pushing to `main` deploys to production.
2. If the executor's commit carries `[skip ci]`, `deploy.yml` is skipped too —
   run `gh workflow run deploy.yml --ref main`, or prod silently stays old.
3. Update `Admin Console\CLAUDE.md`: affected section, `updated:`,
   `verified-against:`. **P1 invalidates its entire "three skins / Burnt Orange
   palette / refraction lens" section** — rewrite it rather than patching, and
   record the palette change as a deliberate owner decision dated 2026-08-01.
4. Append a dated entry to `Devlogs\2026-08-01.md`.
5. Noticed-but-not-fixed → `Worth Noting — Issues.md`. Got it wrong →
   `Where Claude Was Wrong.md`.
