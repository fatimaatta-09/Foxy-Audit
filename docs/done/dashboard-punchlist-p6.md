# Dashboard punch-list — P6 (planner ↔ executor run book)

> ## ✅ COMPLETE — all six phases merged, 2026-07-31
>
> The owner's 14-issue punch-list is closed. Merged in this order:
>
> | Phase | Items | Commit(s) |
> |---|---|---|
> | P6a | tour size + re-trigger, top bar, two removed lines, login card, PDF label | `acc594c` |
> | P6b | verified-button persistence, two card moves, clickable alerts | `84beb1b` |
> | P6d | hero icons, then coloured tiles, then an icon swap | `2837875` · `f5fd877` · `b7c7598` |
> | P6c | profile picture upload (migration `0057`) | `12d268c` · `8d2a2d9` |
> | P6e | passport running header + the owner's struck seal | `6d06781` · `0bfe3ee` |
> | P6f | per-org judge model + verdict provenance (migration `0058`) | `b3a9cf7` |
>
> **Two owner decisions changed mid-flight** and the plan text below still reflects
> the originals — read the 2026-07-31 devlog for what actually shipped:
> the hero icons became **licensed icons8 3D Fluency assets**, not hand-drawn SVG;
> and the passport seal became the **owner's own artwork**, gold for a verified
> chain and tinted line art for the other two states.
>
> **What did NOT ship, deliberately:** no QR code, no public passport-verification
> endpoint, no PDF signature. The seal still reports one fact.
>
> Follow-ups live in the vault's `Worth Noting — Issues` — **#27 is the one that
> matters**: the passport tells a reader to run a verifier they have no way to
> obtain.

## Context

The owner's punch-list is at
`G:\My Drive\Life\03 Projects\Foxy Audit\Dashboard\New issues and the ones not fixed.md`
(2026-07-31) — 14 issues found while using the live dashboard, each anchored to a
screenshot. Every anchor below (file, line, function) was verified against the
live code, not inferred.

The customer dashboard is **one file** — `foxy-dashboard/foxy-audit-premium.html`,
5,998 lines — so most phases edit the same file. That is why the phase-stacking
rule below is not optional.

Owner decisions taken before planning:

| # | Decision |
|---|---|
| Icons (5) | Hand-draw gradient inline SVG in the reference style — no licensed raster art |
| Passport (11) | **Redesign the stamp only** — no QR, no public passport-verify endpoint, no PDF signature |
| Avatar (4) | **Real multipart upload** — `python-multipart` + Pillow → mounted disk volume |
| Model select (10) | Per-org model version **and** record the resolved model ID in the verdict |

### The honest answer owed on item 11

> *"Is there any actual validity/authority behind these stamps?"*

**No, and there cannot be.** The seal reports one fact: Foxy recomputed the hash
chain over the period and it came back intact. No accreditor, no registry, no
regulator. A rendered stamp also cannot be made un-recreatable — anyone can copy
an image. The only thing that would give an outsider independent confidence is a
QR/short-code resolving to a page that recomputes the chain, and that is
**deliberately out of scope** per the owner's decision. So P6e is a *visual*
upgrade, and the disclaimer caption stays byte-identical.

---

## How this runs (the loop, per phase)

1. **MAIN** pastes the phase's executor prompt (below) to the executor chat.
2. **EXECUTOR** builds on its own branch off fresh `origin/main`, runs its fast
   checks, pushes the branch. It never touches `main`.
3. **MAIN** runs the gate for that phase: fetch the branch, read the diff,
   `node --check` every touched inline `<script>`, run the named tests, render
   and screenshot the UI, `code-review` skill, scope grep, no-fake-data grep,
   no-secret grep, single alembic head.
4. **MAIN** merges by direct SHA push: `git push origin <sha>:refs/heads/main`.
   ⚠ CD is currently blocked on GitHub billing — after merging, either trigger
   `deploy.yml` or hand the owner the manual deploy commands.
5. **MAIN** updates the vault: `Dashboard\CLAUDE.md` (affected section,
   `updated:`, `verified-against:`) + `Devlogs\2026-MM-DD.md`. Anything noticed
   and not fixed → `Worth Noting — Issues.md`.
6. **MAIN** hands the owner the next phase's prompt.

**Before anything else:** MAIN commits this plan to
`docs/plans/dashboard-punchlist-p6.md` so the executor can read it.

### Standing rules every phase inherits

- `ui-ux-pro-max` **first**, then `frontend-design`, on any phase touching pixels.
- **Phase-stacking:** P6a/P6b/P6d edit the same file — each branches off **fresh
  `origin/main`** and rebases before push, or it reverts merged work.
- **`node --check` every inline `<script>` touched** (there are 28).
- **Never `el.value = x`** — use `window.setFieldValue(el, v)`.
  `test_p2_structure.py` enforces it. `polJudgeProvider` is a named exemption; a
  new select is not.
- **Single alembic head.** P6c = `0057`, P6f = `0058`, in that order.
- **No fake data** — applies to the cached "verified" state (P6b) and the alert
  detail copy (P6b).
- Motion gated on `prefers-reduced-motion: no-preference`, never disabled on `reduce`.
- Never commit secrets; never serialise `password_hash` / `key_hash` / `*_key_enc`.

### Sequencing

```
P6a ─┐
P6b ─┼─ same file · fresh branch + rebase each time
P6d ─┘
P6c (migration 0057) ──▶ P6f (migration 0058)   ← must merge in this order
P6e  independent (backend template only)
```

P6a first: highest owner-visible payoff per unit of risk, and it deletes the
read-only model label that P6f replaces.

---

# P6a — Chrome, copy, and the tour  *(items 1, 2, 6, 7, 12)*

**Branch:** `fix/dash-p6a-chrome`
**Touches:** `foxy-dashboard/foxy-audit-premium.html`,
`backend/tests/integration/test_first_run_tutorial.py`

### Scope

**1 · Tutorial text too small + re-appears every refresh (item 1).**
Tour lives at lines 5726-5996. Bump `.tour-b` `11.5px → 13px` (line ~5754),
`.tour-t` 14 → 15, `.tour-count` 9 → 10; re-check the card `max-width` so long
steps don't outgrow the spotlight.
The re-trigger is one line: `foxTourMaybeOffer` (line ~5990) gates on
`!t.completed` only. The server **already stores `skipped`**
(`backend/app/routers/account.py:531-534`) and the client ignores it →
`if(t.enabled && !t.completed && !t.skipped) open();`
Also fix the abort path: `render()` calls `finish(false)` when a step target
vanishes (line ~5884), recording *skipped* — under the new gate that would
silently kill the tour for someone who never saw it. Make that path close
**without** recording either flag. "Take the product tour" (line 1270) stays.
`backend/tests/integration/test_first_run_tutorial.py:93` codifies the old
behaviour — rewrite it to assert the opposite.

**2 · Top bar (item 2).** Replace the green `.livedot` (line 1230) with a
fox-orange full stop **after** the title — `Overview.` — per `img 6.png`.
Keep `id="foxLiveDot"`: the connection toggle at line ~5003 writes
`livedot on|off`; map that to the period's colour (`--fox` live, `--muted2`
offline) and keep the `title=` tooltip, so the signal is restyled, not lost.
Add the logo before the "Foxy Audit" wordmark (line 1227) — **do not paste a
second base64 blob**; the existing marks are 8.3 KB (line 1167) and 21.8 KB
(line 2308). Add an empty `<img class="crumb-logo" alt="" aria-hidden="true">`
and set `src` at init from `document.querySelector('.dock-mark img').src`. 16px;
add it to the `max-width:520px` hide rule alongside `.crumb-brand` (line ~316).

**3 · Remove two lines (item 6).** Line 1811 (billing eyebrow `Plan &
consumption · #bilPlan`) and line 1609 (policy eyebrow `#judgeModelLabel`).
Both have JS writers — 1609's are at lines 3020 and 3027 — remove the writers
too; do not leave a dangling `getElementById`.

**4 · Login card too small (item 7).** All four gate cards are inline
`width:340px` (lines 2306, 2336, 2347, 2358) → `380px`, moved into a shared
`.authcard` class so they cannot drift. `test_login_card.py` asserts no width.

**5 · Export label (item 12).** Line 1706 `Compliance passport · HTML` → `· PDF`.
The endpoint has been PDF-only for a while (`passport.py:325-342`) and the help
line at 1708 already says PDF.

### Executor prompt (paste as-is)

```
Repo: c:\Users\PC\Downloads\Foxy-Audit  ·  Branch off FRESH origin/main as: fix/dash-p6a-chrome

Read docs/plans/dashboard-punchlist-p6.md and implement PHASE P6a ONLY. Do not start
any other phase. Invoke the ui-ux-pro-max skill FIRST (this is UI work), then
frontend-design. Do not push to main — push your branch only.

Everything is in foxy-dashboard/foxy-audit-premium.html (one 5,998-line file) except
one backend test. Five changes:

1. TOUR (lines 5726-5996). Text is too small to read: .tour-b 11.5px -> 13px (~line
   5754), .tour-t 14 -> 15, .tour-count 9 -> 10. Re-check the card max-width so a
   6-line step still fits the spotlight.
   BUG: the tour re-appears on EVERY page refresh. foxTourMaybeOffer (~line 5990)
   gates on !t.completed only; the server already persists `skipped`
   (backend/app/routers/account.py:531-534) and the client ignores it. Gate on
   `t.enabled && !t.completed && !t.skipped`.
   Also: render() calls finish(false) when a step's target is missing (~line 5884),
   which records *skipped*. Under the new gate that would kill the tour for a user
   who never saw it — make that path close WITHOUT recording either flag.
   Then rewrite backend/tests/integration/test_first_run_tutorial.py:93
   (test_a_skipped_tutorial_is_still_offered_on_the_next_login) to assert the
   opposite, with a docstring noting the owner reported the re-offer as a bug.

2. TOP BAR. Replace the green .livedot (line 1230) with a fox-orange full stop
   placed AFTER the page title, so it reads `Overview.` — the sale-page hero
   treatment. KEEP id="foxLiveDot": the connection toggle at ~line 5003 writes
   class 'livedot on|off' to it. Map that to the period's colour (var(--fox) when
   live, var(--muted2) when offline) and keep the title= tooltip. The connection
   signal must be restyled, not deleted.
   Add the Foxy logo BEFORE the "Foxy Audit" wordmark (line 1227). DO NOT paste
   another base64 data URI — the existing marks are 8.3 KB (line 1167) and 21.8 KB
   (line 2308). Add <img class="crumb-logo" alt="" aria-hidden="true"> with no src,
   and set its src at init from document.querySelector('.dock-mark img').src.
   16px. Add .crumb-logo to the @media(max-width:520px) hide rule next to
   .crumb-brand (~line 316).

3. REMOVE two header lines the owner does not want:
   - line 1811, billing eyebrow: "Plan & consumption · <span id="bilPlan">"
   - line 1609, policy eyebrow: id="judgeModelLabel"
   Delete their JS writers too (judgeModelLabel is written at lines 3020 and 3027;
   find bilPlan's). No dangling getElementById calls.

4. LOGIN CARD is too small. Four auth cards are sized inline width:340px at lines
   2306, 2336, 2347, 2358 -> 380px, and move the sizing into one shared .authcard
   class so the four cannot drift apart.

5. EXPORT LABEL. Line 1706: "Compliance passport · HTML" -> "Compliance passport ·
   PDF". The endpoint is PDF-only (backend/app/routers/passport.py:325-342).

RULES (non-negotiable):
- Never assign el.value = x in JS. Use window.setFieldValue(el, v).
- Motion only inside @media (prefers-reduced-motion: no-preference).
- No fake/placeholder data. No secrets.
- Both themes: check every visual change in light AND dark.

CHECKS before you push:
- node --check every inline <script> you touched.
- python -m pytest foxy-dashboard -q            (run from the repo root)
- cd backend && python -m pytest tests/integration/test_first_run_tutorial.py -q
- Render the page and screenshot the top bar and the login card, light + dark, at
  1440px and 375px. Paste what you saw.

Then push fix/dash-p6a-chrome and report: files touched, line ranges, check output,
and anything you noticed but did not fix.
```

### MAIN's gate

Diff review · `node --check` all touched scripts · `pytest foxy-dashboard -q` ·
`test_first_run_tutorial.py` · **screenshot the top bar** (the period must
inherit the live/offline state) and the login card, both themes ·
`code-review` skill · scope grep (nothing outside the dashboard file + that one
test) · merge by SHA · vault: `Dashboard\CLAUDE.md` (top bar + tour sections) +
devlog.

---

# P6b — Persistence, moves, clickable alerts  *(items 8, 9, 13, 14)*

**Branch:** `fix/dash-p6b-interactions` · **Touches:** the dashboard file only.

### Scope

**1 · Verified button loses state on refresh (item 8).** `verifyRecord()`
(line 3132) stores its result *only* as the button's `textContent`/`className` —
lost on reload **and** on any filter or page change, because `loadLogs()`
(line 3163) rewrites `body.innerHTML`. Persist to `localStorage`
(`foxy_verified_records`: `chain_hash → {verified, at}`, LRU ~200, expire at 7
days); `ledgerRow()` (~3110) reads it when rendering the detail row.
**No-fake-data guard:** a cached verdict is about *then*, not now — render
`✓ verified — untampered · checked 2h ago` with a `re-check` affordance, never a
bare present-tense claim. Animation: check-mark stroke draw-in + a brief `--safe`
glow, inside `prefers-reduced-motion: no-preference`.

**2 · Move the fingerprint explainer, Verify → Settings (item 9).** The
`<details class="clay pad">` "How the fingerprint works · advanced" is
**lines 1579-1588**, static markup with **zero JS dependencies**. Land it in
Settings near the Trust badge card (1981-1987), with the other proof content.
After removal, drop the `margin-top:14px` on the next Verify sibling (line 1589)
or the column gains a phantom gap.

**3 · Move the Connect SDK card, Access → Settings (item 13).**
**Lines 1791-1805.** Its only JS dependency is `window.checkSdkConnection`
(3764-3771), which resolves `#sdkCheckOut` by ID at call time — the function can
stay put and keep working. Place it after the Desktop-app card (1996-2002).
Leave a one-line pointer on Access so a new user isn't dead-ended.

**4 · Clickable alerts (item 14).** `notifRow()` (4839) already emits
`role="button" tabindex="0"` + `onclick`, but `onNotifClick` (4857) only
marks-read and navigates — and for kinds other than `breach`/`quota` it does
nothing visible. Open a detail dialog with the **existing** `foxConfirm()`
(markup 5423-5433, impl 5450+). Body = the alert's own `body`/`level`/`kind`/full
timestamp + a **static per-kind explainer**; that is product copy, not fabricated
data — invent nothing the API doesn't return. Keep navigation as the dialog's
confirm action. **Fix the keyboard hole:** those rows have `role="button"` and no
`onkeydown`, so Enter/Space do nothing — copy the ledger-row pattern at 3115-3118.

### Executor prompt (paste as-is)

```
Repo: c:\Users\PC\Downloads\Foxy-Audit  ·  Branch off FRESH origin/main as:
fix/dash-p6b-interactions
(P6a may already be merged — rebase on fresh origin/main before you push, or you
will revert it. This is a hard rule in this repo: phases share one file.)

Read docs/plans/dashboard-punchlist-p6.md and implement PHASE P6b ONLY. Invoke
ui-ux-pro-max FIRST, then frontend-design. Push your branch; never main.
All changes are in foxy-dashboard/foxy-audit-premium.html.

1. LEDGER "verified" BUTTON DOES NOT PERSIST. verifyRecord() at line 3132 keeps its
   result only in the button's textContent/className, so it is lost on refresh and
   also on any filter/pagination (loadLogs() at line 3163 rewrites body.innerHTML).
   Persist to localStorage key foxy_verified_records: a map chain_hash ->
   {verified:bool, at:epochMillis}, LRU-capped ~200 entries, entries older than 7
   days discarded. ledgerRow() (~line 3110) reads the map and paints the remembered
   state.
   HARD RULE — no fake data: a cached result describes the past, not the present.
   Render it as "✓ verified — untampered · checked 2h ago" with a "re-check"
   affordance. Never render a bare present-tense claim from cache.
   Also add animation so it feels alive: check-mark stroke draw-in
   (stroke-dashoffset) plus a brief --safe glow, all inside
   @media (prefers-reduced-motion: no-preference).

2. MOVE the <details class="clay pad"> "How the fingerprint works · advanced" from
   the Verify page (lines 1579-1588) to the Settings page, next to the Trust badge
   card (lines 1981-1987). It is static markup with zero JS dependencies. After
   removing it, the next sibling on Verify (line 1589) has margin-top:14px that now
   creates a phantom gap — drop it.

3. MOVE the "Connect the SDK" card from the Access page (lines 1791-1805) to
   Settings, after the Desktop-app card (lines 1996-2002). Its only JS dependency is
   window.checkSdkConnection (lines 3764-3771), which looks #sdkCheckOut up by ID at
   call time — leave that function where it is, it will keep working. Leave one line
   on the Access page pointing to Settings so a new user is not dead-ended.

4. ALERTS MUST BE CLICKABLE AND SHOW DETAIL. notifRow() (line 4839) already emits
   role="button" tabindex="0" and an onclick, but onNotifClick (line 4857) only
   marks read and navigates — for kinds other than 'breach'/'quota' nothing visible
   happens. Open a detail dialog using the EXISTING foxConfirm() (markup 5423-5433,
   impl 5450+). Do not build a second modal system.
   Dialog body: the alert's own title, body, level, kind and full timestamp, PLUS a
   short static per-kind explainer ("what this means" / "what to do"). That explainer
   is product copy. Do NOT invent per-alert detail the API does not return.
   Keep the existing navigation as the dialog's confirm action (Open in Threats /
   Open billing) so nothing that works today is lost.
   Also fix the keyboard hole: those rows have role="button" with NO onkeydown, so
   Enter/Space do nothing. Copy the pattern already applied to ledger rows at lines
   3115-3118.

RULES: never el.value = x (use window.setFieldValue); motion only inside
prefers-reduced-motion: no-preference; no fake data; both themes.

CHECKS: node --check every inline <script> you touched; python -m pytest
foxy-dashboard -q from the repo root; render and screenshot (a) an expanded ledger
row before AND after a refresh, (b) an open alert dialog, light + dark.

Push fix/dash-p6b-interactions and report files, line ranges, check output, and
anything noticed but not fixed.
```

### MAIN's gate

Same as P6a, plus: **reload the page** and confirm the verified pill returns with
its age string · confirm the alert dialog invents no data · confirm Verify and
Access have no leftover gaps. Vault: `Dashboard\CLAUDE.md` page table (the Verify
and Access sections changed) + devlog.

---

# P6c — Profile picture + the password card  *(items 3, 4)*

**Branch:** `feat/dash-p6c-avatar` · **The security-sensitive phase.**

The backend has **no blob storage of any kind** today — no `UploadFile`, no
`multipart`, no S3, no `StaticFiles`, no binary column. This builds that surface
for the first time.

### Scope

**Backend** — `requirements.txt`: add `python-multipart` + `Pillow`, exact-pinned.
Migration **`0057_user_avatar.py`**: `users.avatar_path` `String(255)` nullable,
`users.avatar_updated_at` timestamptz nullable (users model at
`models.py:326-369`). New routes beside `PUT /v1/account/profile`
(`routers/account.py:631-640`):
- `POST /v1/account/avatar` — `UploadFile`; reject >5 MB **before** reading;
  validate by **decoding with Pillow**, never by extension or the client's
  `Content-Type`; PNG/JPEG/WebP in; re-encode to a square 256×256 **PNG**,
  stripping EXIF (normalises format and destroys any embedded payload); write
  `{avatar_dir}/{user_id}.png`. Rate-limit it — decode is CPU work.
- `DELETE /v1/account/avatar` — unlink + null the columns.
- `GET /v1/account/avatar` — `FileResponse`, session-authed, own file only,
  `Cache-Control: private, max-age=60`.
- Audit both writes (`account.avatar_set` / `account.avatar_clear`).
- `MeResponse` (`auth_human.py:49-61`): add `has_avatar: bool` — a **boolean, not
  bytes**. Do not add `org_id`; its absence is deliberate (comment 52-58).
- `config.py`: `avatar_dir: str = "/data/avatars"`.

**Deploy** — named volume `foxy_avatars:/data/avatars` on `foxy-api` (compose
line ~106) **and** `foxy-worker`, plus the `volumes:` block at line 208 (rw, not
`:ro`). ⚠ **`deploy/backup.sh` only dumps Postgres** — add a `tar` of the avatar
dir under the same retention. This is the first prod state living outside the DB.

**Dashboard** — Settings → *Account & identity* card (1937-1973), above
`Change password`: avatar block with current picture or initial fallback, and
**Upload photo** / **Remove photo** (item 3's third bullet + item 4). Crop and
resize on `<canvas>` before upload so a 12 MP phone photo never hits the wire;
POST `FormData` through the patched `fetch` (the CSRF patch at line 2415 sets a
header, it does not touch the body — a bare `<form action=>` post would bypass
**both** fetch patches, don't).
`window.foxAvatar` (2959-2972) writes an initial into every `[data-avatar]`;
extend it so `has_avatar` renders `<img src="/v1/account/avatar?v={updated_at}">`
into the same mounts (`.topuser-av` 1256, `#dockUser` 1213), initial kept as
`onerror` fallback. The `?v=` buster is what makes a fresh upload appear at once.
**Eye toggle (item 3):** `.pw-eye` exists and is auto-wired by the IIFE at
2374-2410 — wrap `#setCurPw` (1967) in `.pw-wrap` + add the button; do
`#setNewPw`/`#setNewPw2` too. Mirror the ARIA contract `test_login_card.py:85-100`
asserts, and make `foxHidePasswords()` cover the new fields.
**Forgot password in Settings (item 3):** POST the session user's email to the
existing `POST /v1/auth/forgot-password` (`auth_human.py:232-245`) — the whole
reset flow is already built front and back, so this is a call site, not a
feature. Show the same enumeration-safe "check your email" message.

### Executor prompt (paste as-is)

```
Repo: c:\Users\PC\Downloads\Foxy-Audit  ·  Branch off FRESH origin/main as:
feat/dash-p6c-avatar

Read docs/plans/dashboard-punchlist-p6.md and implement PHASE P6c ONLY. Invoke
ui-ux-pro-max FIRST for the UI half. Push your branch; never main.
This phase adds the FIRST file-upload surface in the backend — treat validation as
security work, not plumbing.

BACKEND
1. backend/requirements.txt: add python-multipart and Pillow, exact-pinned (the file
   is deliberately pinned; follow the existing style and comment).
2. New migration backend/migrations/versions/0057_user_avatar.py (current head is
   0056_sdk_enforcement — keep ONE head):
   users.avatar_path String(255) nullable, users.avatar_updated_at timestamptz
   nullable. Add both to the User model (backend/app/models.py:326-369).
3. backend/app/config.py: avatar_dir: str = "/data/avatars".
4. New routes in backend/app/routers/account.py, beside PUT /v1/account/profile
   (lines 631-640), same auth/audit patterns:
   - POST /v1/account/avatar (UploadFile). Reject >5 MB BEFORE reading the body.
     Validate by DECODING WITH PILLOW — never trust the extension or the client's
     Content-Type. Accept PNG/JPEG/WebP. Re-encode to a square 256x256 PNG with EXIF
     stripped (this normalises the format and destroys any embedded payload). Write
     to {avatar_dir}/{user_id}.png and store the path + updated_at. Rate-limit it
     with slowapi (already wired) — image decode is CPU work.
   - DELETE /v1/account/avatar — unlink the file and null both columns.
   - GET /v1/account/avatar — FileResponse of the CURRENT USER'S OWN file only,
     session-authed, Cache-Control: private, max-age=60.
   - Audit both writes: account.avatar_set / account.avatar_clear.
5. MeResponse (backend/app/routers/auth_human.py:49-61): add has_avatar: bool.
   A BOOLEAN, not the bytes. Do NOT add org_id — its absence is deliberate, see the
   comment at lines 52-58 and test_p3_orgid_stepup.py.

DEPLOY
6. deploy/docker-compose.prod.yml: named volume foxy_avatars:/data/avatars on BOTH
   foxy-api (volumes block ~line 106) and foxy-worker, read-write (the existing site
   mounts are :ro — this one is not), plus an entry in the top-level volumes: block
   at line 208.
7. deploy/backup.sh currently dumps ONLY Postgres. Add a tar of the avatar directory
   beside the SQL dump under the same RETENTION_DAYS pruning. This is the first prod
   state that lives outside the database — say so in a comment.

DASHBOARD (foxy-dashboard/foxy-audit-premium.html)
8. Settings -> "Account & identity" card (lines 1937-1973), ABOVE "Change password":
   an avatar block showing the current photo (or the initial fallback) with
   "Upload photo" and "Remove photo" buttons. Crop/resize on a <canvas> to 256x256
   BEFORE upload so a 12 MP phone photo never hits the wire, then POST FormData via
   fetch. Do NOT use a bare <form action=> post — this SPA patches window.fetch
   twice (CSRF at line 2415, step-up retry at line 4816) and a form post bypasses
   both. FormData through fetch is fine; the CSRF patch sets a header, not a body.
9. window.foxAvatar (lines 2959-2972) writes an initial into every [data-avatar].
   Extend it: when has_avatar is true render <img src="/v1/account/avatar?v={the
   avatar_updated_at value}"> into the same mounts (.topuser-av line 1256, #dockUser
   line 1213), keeping the initial as the onerror fallback and for users with no
   photo. The ?v= cache-buster is what makes a fresh upload show up immediately.
10. EYE TOGGLE on the password fields. The .pw-eye component already exists and is
    auto-wired by the IIFE at lines 2374-2410. Wrap #setCurPw (line 1967) in a
    .pw-wrap and add <button class="pw-eye" type="button" data-pw-toggle="setCurPw"
    ...>; do the same for #setNewPw and #setNewPw2. Match the ARIA contract that
    test_login_card.py:85-100 asserts (type="button", aria-pressed, aria-label,
    aria-controls) and make sure foxHidePasswords() clears the new fields too.
11. "Forgot password?" in Settings, under the current-password field, for users who
    don't know their current password. POST the signed-in user's email to the
    EXISTING POST /v1/auth/forgot-password (backend/app/routers/auth_human.py:
    232-245). The whole reset flow is already built front and back — this is a call
    site, not a new feature. Show the same enumeration-safe "check your email"
    message the sign-in gate shows. Copy: "Don't know your current password? We'll
    email you a reset link."

RULES: never el.value = x (window.setFieldValue); never serialise password_hash /
key_hash / *_key_enc; no secrets in the diff; ONE alembic head; both themes.

CHECKS: node --check every inline <script> touched; python -m pytest foxy-dashboard
-q from the repo root; cd backend && python -m pytest
tests/integration/test_profile_prefs.py -q and
tests/integration/test_pref_switches_are_real.py -q; alembic heads shows exactly one.
Add tests: the avatar endpoint rejects a non-image, a 6 MB file, and a PNG sent with
a lying Content-Type; MeResponse returns has_avatar and still no org_id; all three
settings password fields carry a data-pw-toggle.
Render and screenshot: the Settings account card, and the top bar with a photo
uploaded, light + dark.

Push feat/dash-p6c-avatar and report files, migration number, check output, and
anything noticed but not fixed.
```

### MAIN's gate

Extra scrutiny on upload validation (magic-byte/decode path, size cap **before**
read, path traversal on `{user_id}.png`, no serving another user's file) ·
`alembic heads` = 1 · confirm the compose volume is on **both** services ·
confirm `backup.sh` covers it · `security-review` skill in addition to
`code-review`. **Deploy note:** this changes `requirements.txt` *and* compose —
it needs a real `docker compose up --build` on the VM, not a file swap. Vault:
`Dashboard\CLAUDE.md`, `Database\CLAUDE.md` (new columns), `Backend\CLAUDE.md`
(new routes), `Deploy & CI\CLAUDE.md` (new volume + backup), devlog.

---

# P6d — Hero card icons  *(item 5)*

**Branch:** `feat/dash-p6d-hero-icons` · **Touches:** the dashboard file only.

Reference: `img 3.png`, `img 12.png`, `image 13.png` — glossy, saturated,
per-tile hue (orange / blue / pink / gold), soft inner highlight, chunky forms.
Owner decision: **hand-drawn SVG in that style**, not licensed raster art.

The right mechanism already exists: an inline `<symbol>` sprite in `<defs>` at
**lines 1134-1160** (24 duotone glyphs), used via `<use href="#dg-…">`. Extend
it; do not add a second icon system.

### Executor prompt (paste as-is)

```
Repo: c:\Users\PC\Downloads\Foxy-Audit  ·  Branch off FRESH origin/main as:
feat/dash-p6d-hero-icons
(Other phases may have merged since — rebase on fresh origin/main before pushing.)

Read docs/plans/dashboard-punchlist-p6.md and implement PHASE P6d ONLY. Invoke
ui-ux-pro-max FIRST, then frontend-design. Push your branch; never main.
File: foxy-dashboard/foxy-audit-premium.html

The owner wants the hero card icons replaced with better ones, in the style of the
vault reference images (G:\My Drive\Life\img 3.png, img 12.png, image 13.png):
glossy, saturated, per-tile hue — orange, blue, pink, gold — soft inner highlight,
chunky friendly forms. DECIDED: hand-draw them as inline SVG. Do NOT download or
embed licensed raster art from iconscout/icons8 — it is a licensing problem and each
one would add 50-150 KB of base64 to a file that is already 589 KB.

1. The file already has the right mechanism: an inline <symbol> sprite inside <defs>
   at lines 1134-1160 (24 duotone glyphs, used as <use href="#dg-...">). EXTEND it —
   do not introduce a second icon system.
   Add a parallel #hi-* (hero-icon) set with <linearGradient> fills, one per hero
   tile, each on its own hue. Gradient IDs inside a <symbol> must be namespaced
   (e.g. hi-shield-g1) or they collide with the existing duotone set.
2. Swap the four hero KPI tiles on Overview (#homeKpis, lines 1320-1335):
   line 1322 #dg-shield, 1325 #dg-threats, 1329 #dg-chart, 1332 #dg-verify.
3. .dglyph (line 1044) bleeds the glyph off the card corner at 98px with opacity:.46
   and color:currentColor — a FULL-COLOUR icon must not inherit that. Add a sibling
   .hglyph class: foreground placement, full opacity, ~44px, positioned as a real
   icon rather than a watermark. Check it against the mobile shrink rule at line 1054.
4. Leave every #dg-* symbol in place — other pages use them (lines 1674, 1767-1776,
   1816-1819, 1934).

BOTH THEMES. A gradient tuned on the dark --surf can vanish on light paper. Check
each tile in light mode and adjust the stops; do not just accept the dark pass.

CHECKS: node --check every inline <script> touched; python -m pytest foxy-dashboard
-q from the repo root. Add a guard test that every <use href="#hi-..."> resolves to a
<symbol> that actually exists (mirror what test_p3_tour.py does for tour selectors —
the failure mode is silent).
Render and screenshot all four hero tiles, light AND dark, at 1440px and 375px.

Push feat/dash-p6d-hero-icons and report.
```

### MAIN's gate

**This one is judged by eye** — screenshots in both themes are the review. Check
the icons don't fight `--fox` for attention, and that the dark-mode gradients
survive light mode. Vault: `Dashboard\CLAUDE.md` design-system section + devlog.

---

# P6e — Compliance Passport: header and seal  *(item 11, cosmetic scope)*

**Branch:** `feat/passport-p6e-seal` · **Touches:**
`backend/app/templates/compliance_passport.html` + its two tests. Independent of
every other phase.

### Scope

**Logo → running header.** The mark appears **once**, on the cover, at 34×34
(lines 203-204 — a 59 KB base64 PNG of a 256×256 source, ~97% of the template
file). The running header is text-only (`@page { @top-left }`, lines 44-63).
Shrink the cover mark; put the logo in `@top-left` via `content: url("data:
image/svg+xml;…") "  Foxy Audit — Compliance Passport"`. **Use a compact SVG data
URI, not the PNG** — a margin box repeats on every page and 59 KB of base64 in a
CSS `content` is slow and fragile in weasyprint. `foxy-sale-page/favicon.svg` is
an 8-line fox mark to adapt; monochrome for print. `@page :first` (66-70)
suppresses the margin boxes so the cover is unaffected. **Verify page 2+ in a
real render** — weasyprint's margin-box `url()` support is the risk; if it
misbehaves, fall back to a fixed-position header element.
`test_passport_render.py:137-149` asserts all four margin boxes and the page
counter — extend, don't break.

**Gold seal.** Lines 229-261: three concentric circles + flat Georgia text in
`#107435`. Redesign as a proper gold stamp — metallic gradient ramp (pale gold →
deep bronze → highlight), rope/guilloché ring instead of the plain dashed circle,
embossed inner disc, slight rotation so it reads as *applied* not *drawn*.
**No text on a path** (line 156: weasyprint renders it unreliably).
**Keep all three states honest:** gold only for `verified`; distinct oxidised-red
for `failed` and amber for no-events. There must never be a gold stamp on an
unverified document — that branch (232-238) is the point. Keep the caption at
256-260 **byte-identical**; the owner's authority question makes it more
important, not less. `print-color-adjust:exact` is already set (line 76).

### Executor prompt (paste as-is)

```
Repo: c:\Users\PC\Downloads\Foxy-Audit  ·  Branch off FRESH origin/main as:
feat/passport-p6e-seal

Read docs/plans/dashboard-punchlist-p6.md and implement PHASE P6e ONLY. Invoke
ui-ux-pro-max FIRST. Push your branch; never main.
File: backend/app/templates/compliance_passport.html (server-rendered to PDF by
weasyprint via backend/app/routers/passport.py:325-337), plus its two tests.

SCOPE NOTE: the owner decided this phase is a VISUAL upgrade only. Do NOT add a QR
code, a public passport-verification endpoint, or a PDF signature. And do not add
copy implying the seal carries authority it does not have — it does not.

1. LOGO INTO THE RUNNING HEADER. Today the mark appears once, on the cover, at 34x34
   (lines 203-204: a 59 KB base64 PNG of a 256x256 source — about 97% of this file).
   The running header is text-only (@page { @top-left ... }, lines 44-63).
   - Make the cover mark smaller (a 24x24 render is sharper at print DPI than a
     256px source scaled to 34).
   - Put the logo into @top-left:  content: url("data:image/svg+xml;...") "  Foxy
     Audit — Compliance Passport".
   - USE A COMPACT SVG DATA URI, NOT THE PNG. A margin box repeats on every page and
     59 KB of base64 inside a CSS content value is slow and fragile in weasyprint.
     foxy-sale-page/favicon.svg is an 8-line fox mark you can adapt; keep it
     monochrome for print.
   - @page :first (lines 66-70) suppresses all four margin boxes, so the cover is
     unaffected — leave that.
   - RENDER A REAL MULTI-PAGE PDF AND LOOK AT PAGE 2. weasyprint's support for
     url() in a margin box is the actual risk here. If it misbehaves, fall back to a
     fixed-position header element and say so in your report.
   - test_passport_render.py:137-149 asserts all four margin boxes and the page
     counter — extend those tests, do not break them.

2. THE "VERIFIED" STAMP LOOKS BAD AND AI-GENERATED (owner's words). Lines 229-261:
   three concentric circles and flat Georgia text in #107435. Redesign it as a
   proper GOLD stamp:
   - metallic ramp via <linearGradient>/<radialGradient> (pale gold -> deep bronze ->
     highlight), a rope or guilloche detail ring in place of the plain dashed circle,
     an embossed inner disc, and a slight rotation so it reads as APPLIED rather than
     drawn.
   - NO TEXT ON A PATH. The comment at line 156 says weasyprint renders it
     unreliably. Keep the straight-line text layout.
   - KEEP ALL THREE STATES HONEST. The Jinja branch at lines 232-238 picks the seal
     from chain_verification: verified / failed / no-events. Gold is ONLY for
     verified. Give failed an oxidised red and no-events an amber, both visually
     distinct. There must never be a gold stamp on an unverified document — that
     branch is the entire point of the seal.
   - KEEP THE CAPTION AT LINES 256-260 BYTE-IDENTICAL. It is the sentence that stops
     the seal overclaiming.
   - print-color-adjust:exact is already set at line 76 — required for gradients to
     survive into the PDF.
   - Tests at test_passport_document.py:192-217 cover the seal; update them.

CHECKS: cd backend && python -m pytest tests/integration/test_passport_render.py
tests/integration/test_passport_document.py -q. Then GENERATE AN ACTUAL PDF with a
multi-page date range and attach/describe: the cover, page 2's header, and the seal
in all three chain states (verified / failed / no events). Screenshots of HTML are
not enough — weasyprint is the renderer that matters.

Push feat/passport-p6e-seal and report.
```

### MAIN's gate

**Open the PDF.** Cover, page 2 header, and all three seal states. Confirm the
disclaimer caption is unchanged and no gold appears on an unverified doc. Vault:
`Demo & Docs\CLAUDE.md` / the passport section + devlog.

---

# P6f — Judge model selection + model provenance  *(item 10)*

**Branch:** `feat/judge-p6f-model-select` · **Deepest phase** — DB → routing →
both judges → worker → API → UI. Merge **after** P6c (alembic order).

### Scope

1. **Migration `0058_per_org_judge_model.py`** — `org_policies.gemini_judge_model`
   and `.openai_judge_model`, `String(64)`, **nullable**. Nullable = "inherit the
   deployment default"; a `NOT NULL` default would freeze every existing tenant
   onto today's model ID. No CHECK constraint (it would need a migration per model
   release) — validate in code.
2. **Allow-list** in `judge_routing.py` beside `PROVIDERS` (39) and
   `PLATFORM_KEY_TIERS` (37), with `settings.gemini_model` / `.openai_model`
   (`config.py:24, 30`) as defaults. Unknown stored value → fall back to the
   default rather than failing the grade (same posture as lines 123-124).
3. **`resolve_judge_routing`** (111-143): carry the resolved model on the frozen
   `JudgeRouting` dataclass (51-85) alongside the key.
4. **Judges**: `gemini.evaluate` / `openai_judge.evaluate` take a `model` param,
   used at `gemini.py:148` and `openai_judge.py:137` instead of reading settings;
   `worker.py:164-180` passes it through.
5. **Provenance** (owner's addition): `Verdict` (`schemas.py:64-72`) carries no
   provider and no model, so the ledger cannot say who graded what. Record the
   resolved provider + model ID in the verdict/audit payload at
   `worker.py:223-231`. Two hard constraints — a model ID is not a secret and may
   be recorded, but a **provider key must never reach the chain**
   (`judge_routing.py:1-8`); and routing lives on the live policy row, never in
   the policy snapshot. If adding it would change the hashed payload shape and
   break existing chain verification, it goes in non-hashed metadata instead.
6. **API** (`routers/policies.py`): writable `judge_gemini_model` /
   `judge_openai_model` on `PolicyConfig` (29-77); persist in `update_policies`
   (~197); project in `_to_config` (80-102). `judge_models` (100-101) must report
   the **effective** model (org override else default) or the UI lies. Add a
   response-only `judge_models_available` map so the dropdown is server-driven.
   Add the fields to the audit detail dict (204-210). `PUT` stays admin-only.
7. **Dashboard**: a second `<select>` beside `#polJudgeProvider` (1636), populated
   from `judge_models_available`, shown/hidden by `renderJudgeKeyFields()`
   (3626-3650); add the keys to the `savePolicy()` body (3672-3691). Delete the
   read-only `#polJudgeModels` line (1645) and its comment (1641-1644) — it exists
   only because there was nothing to choose.
8. `desktop/dashboard.py:3177` reads `/v1/policies`; additive fields are safe, but
   grep before shipping. Record the parity gap, don't fix desktop here.

### Executor prompt (paste as-is)

```
Repo: c:\Users\PC\Downloads\Foxy-Audit  ·  Branch off FRESH origin/main as:
feat/judge-p6f-model-select
(P6c must already be merged — it owns migration 0057. Yours is 0058. Confirm
`alembic heads` shows exactly one head before you push.)

Read docs/plans/dashboard-punchlist-p6.md and implement PHASE P6f ONLY. Push your
branch; never main. This one reaches the judge pipeline and the evidence chain —
read backend/app/judge_routing.py lines 1-8 before you start.

The owner wants to choose WHICH MODEL grades their events (e.g. pick Gemini, then
pick the Gemini version), and — decided — the resolved model must be recorded so the
ledger can say which model graded each event.

1. Migration backend/migrations/versions/0058_per_org_judge_model.py:
   org_policies.gemini_judge_model and .openai_judge_model, String(64), NULLABLE.
   Nullable means "inherit the deployment default" — a NOT NULL default would freeze
   every existing tenant onto today's model ID. No CHECK constraint (it would need a
   migration per model release); validate in code instead. Add both to OrgPolicy in
   backend/app/models.py (lines 281-323).
2. backend/app/judge_routing.py: add a model allow-list constant beside PROVIDERS
   (line 39) and PLATFORM_KEY_TIERS (line 37), with settings.gemini_model /
   settings.openai_model (backend/app/config.py lines 24 and 30) as the defaults. An
   unknown stored value must FALL BACK to the deployment default, not fail the grade
   — same defensive posture as lines 123-124.
3. resolve_judge_routing (lines 111-143): carry the resolved model on the frozen
   JudgeRouting dataclass (lines 51-85) alongside the key.
4. backend/app/gemini.py and openai_judge.py: evaluate() takes a `model` parameter
   and uses it at gemini.py:148 and openai_judge.py:137 instead of reading settings.
   backend/app/worker.py:164-180 passes it through.
5. PROVENANCE. Verdict (backend/app/schemas.py:64-72) carries no provider and no
   model, so the chain cannot say who graded what. Record the resolved provider +
   model ID in the verdict/audit payload written at worker.py:223-231.
   TWO HARD CONSTRAINTS: a model ID is not a secret and may be recorded, but a
   PROVIDER KEY MUST NEVER REACH THE CHAIN (judge_routing.py:1-8, models.py:310-313);
   and routing lives on the live policy row, never in the policy snapshot.
   If adding the field would change the HASHED payload shape and break verification
   of existing chains, put it in non-hashed metadata instead — and say which you did.
6. backend/app/routers/policies.py: add writable judge_gemini_model /
   judge_openai_model to PolicyConfig (lines 29-77); persist in update_policies
   (~line 197); project in _to_config (lines 80-102). judge_models (lines 100-101)
   must now report the EFFECTIVE model (org override, else deployment default) or the
   dashboard will lie. Add a response-only judge_models_available map so the dropdown
   is server-driven rather than hardcoded in HTML. Add the fields to the audit detail
   dict (lines 204-210). PUT stays admin-only (lines 152-157).
7. foxy-dashboard/foxy-audit-premium.html: add a second <select> beside
   #polJudgeProvider (line 1636), populated from judge_models_available, shown or
   hidden by renderJudgeKeyFields() (lines 3626-3650) to match the chosen provider.
   Add the new keys to the savePolicy() body (lines 3672-3691).
   Delete the read-only #polJudgeModels line (1645) and its comment (1641-1644) — it
   only existed because there was nothing to choose.
   NOTE: polJudgeProvider is a NAMED exemption from the setFieldValue rule. Your new
   select is not — use window.setFieldValue(el, v).
8. desktop/dashboard.py:3177 also reads /v1/policies. Additive response fields are
   safe, but grep desktop/ before you ship and REPORT the parity gap. Do not change
   desktop in this phase.

CHECKS: alembic heads shows exactly ONE head; node --check every inline <script>
touched; cd backend && python -m pytest tests/integration -q per-file if the full
suite hits the TRUNCATE deadlock (run the judge/worker/policy files at minimum);
python -m pytest foxy-dashboard -q from the repo root.
Add tests: an unknown stored model falls back to the deployment default instead of
failing the grade; no provider key appears anywhere in the chain payload;
judge_models reports the effective model, not the settings value.
Render and screenshot the Policy page provider + model selects, light and dark.

Push feat/judge-p6f-model-select and report files, migration number, whether the
model went into the hashed payload or metadata and why, check output, and the desktop
parity gap.
```

### MAIN's gate

Hardest review of the set: confirm **no key material** reaches the chain payload ·
confirm existing chains still verify (`python verifier/foxy_verify.py logs.json`
against a fresh export) · `alembic heads` = 1 · confirm `judge_models` reports the
effective model. Vault: `Backend\CLAUDE.md` (judge pipeline), `Database\CLAUDE.md`
(new columns), `Dashboard\CLAUDE.md` (Policy page), `Desktop\CLAUDE.md` (parity
gap), devlog.

---

## Cross-phase verification reference

```bash
node --check <each extracted inline script>
python -m pytest foxy-dashboard -q                  # from the repo root
cd backend && python -m pytest tests/integration/<file> -q -p no:randomly
python verifier/foxy_verify.py logs.json            # after P6f, on a fresh export
```

**Render it.** The dashboard note records three bugs found *only* by opening the
page, none visible in the diff. P6a, P6b, P6d and P6e are not reviewable as text.
Use the headless-Chrome recipe in the `admin-fe-verification` memory; for P6e,
open the actual PDF.

**Deploy:** CD is blocked on GitHub billing (`skip-ci-blocks-deploy` + the
2026-07-30 incident). P6c changes `requirements.txt` *and* compose, so it needs a
real `docker compose up --build` on the VM. MAIN hands the owner the manual
commands and confirms `/health/ready` after.
