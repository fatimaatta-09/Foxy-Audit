# Foxy Audit — Admin console: dashboard-parity features (build spec)

## Context
The customer-dashboard redesign (`docs/DASHBOARD_REDESIGN.md`) added cross-cutting UX + security features the
admin **ops console** lacks. This plan brings the admin console (`foxy-adminpage/index.html` + `/admin/v1/*`) to
parity so it's finally "done." Recon confirmed the admin is **missing**: a delayed hover-tooltip system (the
headline ask), a sensitive mask/reveal + copy control + a "hide sensitive metadata" preference, a command palette /
keyboard shortcuts, a notifications bell + panel, and email-code **step-up** on danger actions — and that
`staff_users` has **no `full_name`, no `last_login`, no preferences, and no revocable sessions** (it's a stateless
2h signed cookie). This is an **admin-surface, FE + staff-BE + DB** change.

**Decisions locked:** (1) staff sessions → **DB `staff_sessions`** with an active-devices list + "log out
everywhere" + `last_login`, but **keep the 2h expiry / NO remember-me** (right for a high-security ops console);
(2) **every audited staff mutation requires an emailed step-up code** (reuse the staff MFA email-OTP); (3)
notifications → a **dedicated `staff_notifications` table**; (4) add `staff_users.full_name` + `preferences`.
**Themes are DEFERRED** — the Liquid-Glass/Skeuomorphism redesign is a *separate, in-flight* effort (see
Coordination). This plan reuses its systems, doesn't duplicate them.

## Already present / coming — do NOT rebuild
- **Charts** (inline-SVG helper), **skeleton loaders**, **chart draw-in**, **tactile press**, **typed toasts**,
  **motion** → delivered by the **Liquid-Glass redesign** (`docs/ADMIN_LIQUID_GLASS_REDESIGN.md`, in progress on
  `feat/admin-liquid-glass`). Reuse them; if a phase here needs skeletons and that work hasn't landed, pull the
  `.skel` system from that doc rather than inventing a second one.
- **Dynamic avatar EXISTS** — `applyMe()` sets the initial from `me.email[0]` (`foxy-adminpage/index.html:2074`).
  Parity = upgrade it to use `full_name` when set.
- **Skin/theme/density prefs, change-password, MFA enroll/disable, `/admin/v1/auth/activity`, Audit-log viewer**
  all EXIST — leave them.

## Liquid-Glass corrections (do these FIRST — owner feedback, CSS-only, token-driven)
Two fixes on the *shipped* Liquid Glass before any parity feature. Verify both in glass+clay × light+dark.
1. **Kill the moving background behind the glass.** `.clay::before` runs a drifting specular highlight —
   `animation:liquidSheen 16s … infinite alternate` (`foxy-adminpage/index.html:463`) whose keyframes translate a
   screen-blended radial ±6% (`:465`). That translate is the distracting "ghost panel" sliding behind cards.
   **Remove the `liquidSheen` animation** and make the specular highlight **static** — keep the inset rim + a fixed,
   subtle highlight so glass still reads as *lit*, just not moving. Also **calm the ambient background drift**
   `body::before { animation:drift 28s … }` (`:281`/`@keyframes drift :289`): stop it, or cut its travel+scale so
   nothing perceptibly moves behind the glass. Keep the pointer-tracked specular (Phase E6, `pointermove` → `--mx/
   --my`, `:1756`) ONLY if it's not distracting; otherwise drop it too. **Net goal: a calm, still glass surface —
   no ambient motion.** (Don't touch the intentional, one-shot chart draw-in, skeleton shimmer, or the funnel-bar
   `sheen` `:709` — those are load-time/interactive, not ambient background drift.)
2. **Fix the over-hot active nav button.** `.dock-item.active` (`:354-359`) is a loud
   `linear-gradient(155deg,var(--fox),var(--fox3))` block + heavy `box-shadow:… var(--fox-glow)` + white label —
   "way too off". Replace with a **restrained** active state: a soft tinted surface (e.g.
   `color-mix(in srgb, var(--fox) 14%, var(--surf))`) + an orange **accent indicator** (left bar / underline) +
   orange (not white-on-bright) icon+label, with the glow **greatly reduced or removed**. Keep it token-driven so
   both skins × light/dark work. Apply the same restraint to the brand mark (`:329`) and topuser avatar (`:415`)
   if they read equally hot.
3. **Fix light-mode readability bugs.** Text is washed out / unreadable in LIGHT mode in places — confirmed on the
   **plan-mix donut center number** (the big "3" is barely visible on the pale center) and likely elsewhere. Do a
   **light-mode contrast sweep**: audit every text/label token on light surfaces (chart center total + sublabel in
   `_chartDonut`, chart axis/legend labels, KPI values/deltas, chips, muted captions, `.dock-item` labels,
   funnels) and ensure **≥4.5:1** against its actual background. The donut center number specifically must render
   in a dark ink token (not a light `--fox3`/`--muted2`/white) on the light center. The dark palette was tuned in
   the Liquid-Glass pass but **light mode wasn't re-checked** — fix the tokens (or the per-chart `_cssvar` fills)
   so nothing is low-contrast in light, without regressing dark. Run the ui-ux-pro-max contrast check on both.

## System primer (verified by recon)
- **FE** `foxy-adminpage/index.html` (~2925 lines, token-driven, two skins glass/clay): helpers `api()` (L1404),
  `go()` (L1818), `can()` (L1410), global `ME` (L1408) from `GET /admin/v1/auth/me` (L2066) applied in `applyMe()`
  (~L2074). Dock avatar `#avatar` (L880); topbar chip `#topAvatar/#topUserName/#topUserRole` (L913-919). Reusable
  `openModal()/closeModal()`; a global **Escape-closes-modal** keydown (L2598) is the ONLY non-input shortcut. Only
  the chart tooltip `_bindChartTT()` (L1701, **instant, no delay**) and native `title=` exist. Boot reads
  `foxy_admin_theme/skin/density` (L12-14).
- **BE** (`/admin/v1/*`, cookie `foxy_staff_session`): `StaffUser` (`models.py:339-371`) = **email-only identity**
  + `mfa_enabled/mfa_code_hash/mfa_code_expires_at` (email-OTP), `platform_role`, `disabled` — **no full_name /
  last_login / preferences**. Session = **signed cookie, 2h** (`config.py:59`), established in
  `_establish_staff_session` (`auth_staff.py:88`), validated by `require_staff` (`auth.py:187`), gated by
  `require_platform_role(min)` (`auth.py:205`, ladder viewer<operator<superadmin). **No DB session store, no
  revoke.** Step-up: **none** — danger endpoints use role gate + `record_admin_action` only; the customer
  `keys/regenerate/{request,confirm}` (`keys.py:212`) is the reusable email-OTP template. Audit:
  `record_admin_action` (`admin_audit.py:26`) stages a row, **caller commits in the same txn**. Notifications:
  none (Alerts are *derived*, Inbox is lead-scoped).
- **DB** head **0041**. Platform-only tables (incl. `staff_users`, `admin_actions`) use **ENABLE+FORCE RLS with NO
  policy** (staff superuser bypasses); org-scoped tables add the `org_isolation` policy.

## Non-negotiable rules
- **No fake data.** Notifications are generated from **real events** only; honest empty states.
- **Reuse:** gate with `require_platform_role`; **audit every mutation** via `record_admin_action` (same-txn
  commit); reuse `mfa.py` (`new_otp/hash_code/code_valid`) for step-up; reuse the existing modal + skin systems.
  **Never serialize a secret** (`password_hash`, `mfa_code_hash`, session `token_hash`, `key_hash`).
- **CSP-safe & inline** (no CDN). **Themes deferred** — any new tooltip/mask/palette/bell CSS must read the
  existing tokens so both skins × light/dark work; do not hardcode colors.
- **Not "vibe-coded":** one reused tooltip/mask/palette/bell/step-up component system; grid discipline; tables
  first-class. Accessibility per ui-ux-pro-max (contrast ≥4.5:1, keyboard, aria, focus rings, 44px, reduced-motion).
- FE work extends the single file. **First action for the executing agent: `Skill: ui-ux-pro-max`** (query `ux`,
  `product`, `layout`, `component`) and keep it in the loop.

---

## Build — phased (one branch/PR each)

### Phase A — FE cross-cutting systems (FE-only)
1. **Delayed hover-tooltip system** (headline) — a reusable `data-tip="…"` attribute: ~450ms show delay, positioned
   with auto-flip, dismiss on blur/scroll/Escape, **keyboard/focus accessible** (`:focus-visible` triggers it),
   reduced-motion aware, token-driven. Apply to dock items (esp. when collapsed), topbar icon buttons, KPI labels,
   truncated table cells, chart points (fold in `_bindChartTT`), sensitive fields, danger buttons.
2. **Sensitive mask/reveal + copy** — one control: masked value (`••••`), reveal toggle, copy-to-clipboard
   (`navigator.clipboard`). Apply to org ids, chain/root hashes, `key_prefix`, staff ids, Stripe event ids, config
   secrets, anchor roots. Honors the **`hide_sensitive_metadata`** pref (Phase B) — masked-by-default when on.
3. **Command palette (Cmd/Ctrl-K) + shortcuts** — fuzzy page nav + quick actions (refresh, toggle theme/skin, open
   Settings, jump to an org/hash), role-gated via `can()`; a `?` shortcuts-help overlay. Reuse `go()`.
4. **Responsive polish** — wide tables → stacked cards at narrow widths (today they only scroll inside `.twrap`);
   refine the dock→bottom-bar. (Light; skeletons come from the Liquid-Glass redesign.)

### Phase B — Staff identity & preferences (FE + small BE + DB)
- **DB (migration 0042):** `staff_users.full_name` (nullable String) + `staff_users.preferences JSONB`
  (`hide_sensitive_metadata`, notification prefs).
- **BE:** extend `StaffMeResponse` (`auth_staff.py:50`) with `full_name` + `preferences`; `PUT
  /admin/v1/auth/profile` (set name) and `GET/PUT /admin/v1/auth/preferences`; audit via `record_admin_action`.
- **FE:** avatar/topbar initial from `full_name` else email (`applyMe`); Settings → Account gets a **name** field;
  a new **Privacy** prefs group with the **hide-sensitive-metadata** toggle (drives Phase A) + notification prefs.

### Phase C — Email-code step-up on EVERY sensitive staff action (BE + FE)
- **BE:** a generic step-up — `POST /admin/v1/auth/step-up/request` emails a 6-digit code (reuse `mfa.new_otp/
  hash_code` on the `staff_users.mfa_code_*` columns) and `…/step-up/confirm` verifies it and mints a **short-lived
  step-up grant** (e.g. 10 min, stored in the session) that a new `require_step_up` dependency consumes. The grant
  window means "everything sensitive" doesn't email a code *per click* — one confirm covers a burst. Gate **every
  audited mutation** (per recon: org suspend/enable/plan/ip-allowlist/**key-revoke**/trial/**offboard**; staff
  create/disable/**role**/enable/**mfa-reset**; **broadcast**; announcement deactivate; **config PUT**; alert ack).
  Each still writes `admin_actions`. (Reusing the MFA columns is safe — step-up runs inside an established session,
  not during login; if you want a login-OTP and a step-up-OTP to coexist, add a tiny `staff_step_up_codes` table
  instead — note the option.)
- **FE:** a reusable **step-up modal** (request → enter code → proceed) wrapping the existing `openModal` danger
  flows; "code sent to {email}"; if a valid grant is active, skip straight through.

### Phase D — Notifications center (dedicated table) (BE + DB + FE)
- **DB (migration 0043):** `staff_notifications` (`id, staff_user_id` [nullable = broadcast-to-all-staff]`, kind,
  title, body, level, target_type, target_id, read_at, created_at`) — platform-only, **ENABLE+FORCE, no policy**.
- **BE:** generate rows from **real events** — a broadcast/announcement is posted (fan-out), a staff action
  **targets you** (role change / MFA reset / disable-enable), a system alert fires (deadletter / anchor-failure /
  worker), an org is offboarded/suspended by another staff. `GET /admin/v1/notifications` (mine + broadcasts,
  paginated, unread count), `POST …/{id}/read`, `POST …/read-all`. New router `admin_notifications.py` registered
  in the `main.py:160` admin loop; generation hooks added in `admin_config` (broadcast), `admin_staff` (targeted),
  `admin_alerts`/worker (system).
- **FE:** a **bell + dropdown** in the topbar (unread pip, list, mark-read, "view all"), polled like the existing
  `pollInbox()`. Real data only; honest empty state.

### Phase E — Staff device sessions + last_login + revoke (BE + DB + FE) — NO remember-me
- **DB (migration 0044):** `staff_sessions` (`id, staff_user_id, token_hash, ip, user_agent, created_at,
  last_seen_at, revoked_at`) — platform-only ENABLE+FORCE; add `staff_users.last_login_at`.
- **BE:** rework `_establish_staff_session` (`auth_staff.py:88`) to mint a `staff_sessions` row + store its id in the
  signed cookie and set `last_login_at`; `require_staff` (`auth.py:187`) validates the id (not revoked) + refreshes
  `last_seen_at`; **keep the 2h max-age (no remember-me)**; logout revokes the current row. `GET
  /admin/v1/auth/sessions`, `POST …/sessions/{id}/revoke`, `POST /admin/v1/auth/logout-all` (revoke via step-up).
- **FE:** Settings → **Devices & sessions** card: active sessions (this-device badge, ip/ua/last-seen), revoke,
  log-out-everywhere; show last-login.

## New backend surface (summary)
- `auth_staff.py`: `PUT /auth/profile`, `GET/PUT /auth/preferences`, `POST /auth/step-up/{request,confirm}` +
  `require_step_up` dep, `GET /auth/sessions` + `…/{id}/revoke` + `/auth/logout-all`.
- new `admin_notifications.py`: `GET /notifications` (+unread), `POST /{id}/read`, `/read-all` (register in
  `main.py:160`); generation hooks in `admin_config`/`admin_staff`/`admin_alerts`.
- `auth.py`: session validation in `require_staff`; `require_step_up`.

## New DB migrations (after 0041; platform tables = ENABLE+FORCE no policy)
`0042 staff_users.full_name + preferences` · `0043 staff_notifications` · `0044 staff_sessions +
staff_users.last_login_at`. Combine where sensible.

## Where to work
- **FE:** `foxy-adminpage/index.html` — the tooltip/mask-reveal/palette systems, the notifications bell, the
  step-up modal, Settings cards (name / privacy / devices), `applyMe`.
- **BE:** `backend/app/routers/{auth_staff, admin_config, admin_staff, admin_alerts}.py` + new
  `admin_notifications.py`; `backend/app/auth.py` (session validation + `require_step_up`); reuse `mfa.py`,
  `admin_audit.py`; migrations.
- **Do NOT touch:** the customer dashboard (`foxy-dashboard/`), the customer `/v1/*` API, or the marketing site.

## Coordination with the Liquid-Glass redesign
That redesign is in flight on `feat/admin-liquid-glass` and owns skeletons, the chart helper, chart draw-in,
tactile press, typed toasts, tokens. **Land this parity work rebased on / after it**, reuse its skeleton + token
systems, and make every new component (tooltip/mask/palette/bell/step-up) token-driven so both skins × light/dark
render. Don't duplicate its motion/skeleton systems.

## Testing
- **BE** (mirror `tests/integration/test_admin_*`): role-gating; **step-up required+verified** on every gated
  mutation (missing/invalid/expired code → 401/403; valid grant → effect + `admin_actions` row); session
  validate/revoke/logout-all; notifications generated from real events + mark-read + unread count; **no secret
  leaks** (`token_hash`/`mfa_code_hash`/`password_hash` never returned). Keep the suite green: `cd backend &&
  DATABASE_URL=postgresql+psycopg://foxy:foxy@localhost:5433/foxy API_KEY_PEPPER=testpepper
  .venv/Scripts/python.exe -m pytest tests/integration -q` (do NOT export SESSION_SECRET/STAFF_SESSION_SECRET).
- **FE:** `node --check` both inline `<script>` blocks. Manual: tooltip delay + keyboard + dismiss; mask/reveal +
  copy honoring the pref; palette nav + role-gated actions; bell shows real data + mark-read; **every danger
  action demands the code** (then the grant window); device revoke + logout-all; name-based initial. Both skins ×
  light/dark, every page.

## Branch & merge
One phase per branch off `origin/main` (`feat/admin-parity-ux`, `-identity`, `-stepup`, `-notifications`,
`-sessions`); admin surface only; tests with every endpoint; suite green; PR to `main`; CI/CD redeploys
`admin.foxyaudit.tech/admin/`. (SSH may be needed for push.)

---

## Paste-ready prompt for the executing Claude
```
You're in the Foxy Audit repo. Add DASHBOARD-PARITY features to the STAFF/OPS CONSOLE (the single file
foxy-adminpage/index.html + the /admin/v1/* API) following docs/ADMIN_PARITY.md — read that file first; it's the
source of truth (system primer, guards, phased build, new endpoints/tables, testing, branch/merge). ADMIN-surface
only: do NOT touch the customer dashboard (foxy-dashboard/), the /v1/* customer API, or the marketing site.

FIRST ACTION: Skill: ui-ux-pro-max — keep it in the loop (query ux, product, layout, component). READ the code
before writing (foxy-adminpage/index.html: api()/go()/can()/ME/applyMe, dock+topbar avatar; backend/app/routers/
auth_staff.py, admin_config.py, admin_staff.py, admin_alerts.py; backend/app/auth.py require_staff/require_platform_role;
backend/app/mfa.py; backend/app/admin_audit.py; backend/app/models.py StaffUser; main.py admin loop ~L160). Never
guess an endpoint, table, or helper.

LOCKED DECISIONS: (1) staff sessions = DB staff_sessions table with active-devices list + log-out-everywhere +
last_login, but KEEP the 2h cookie — NO remember-me. (2) EVERY audited staff mutation requires an emailed step-up
code — reuse the staff MFA email-OTP (mfa.new_otp/hash_code/code_valid on staff_users.mfa_code_*); a confirmed
step-up mints a short-lived (~10min) grant so bursts don't re-prompt per click. (3) notifications = a NEW dedicated
staff_notifications table (platform-only, ENABLE+FORCE, no policy) generated from REAL events. (4) add
staff_users.full_name + preferences (hide_sensitive_metadata + notif prefs); avatar initial uses the name when set.
THEMES ARE DEFERRED — the Liquid-Glass redesign (docs/ADMIN_LIQUID_GLASS_REDESIGN.md) is separate and in flight;
REBASE this on it, REUSE its skeleton + chart + token systems, and make every new component token-driven (works in
both skins × light/dark). Do NOT build themes or duplicate its skeleton/motion here.

HARD RULES:
- NO fake data — notifications come from real events (broadcasts, staff actions targeting you, system alerts);
  honest empty states. Reuse: require_platform_role gating, record_admin_action audit (commit in the SAME txn),
  mfa.py OTP helpers, the existing modal + skin systems. Never serialize a secret (password_hash, mfa_code_hash,
  session token_hash, key_hash).
- Build ONE reusable system each: delayed data-tip tooltip (~450ms, keyboard/focus accessible, auto-flip,
  reduced-motion), sensitive mask/reveal+copy (honors hide_sensitive_metadata), command palette (Cmd/Ctrl-K, role-
  gated) + shortcuts help, notifications bell/panel, step-up modal. Everything CSP-safe/inline and token-driven
  (no hardcoded colors — themes are applied later). Not "vibe-coded": grid discipline, tables first-class, one
  component system. Accessibility per ui-ux-pro-max (contrast ≥4.5:1, keyboard, aria, focus rings, 44px,
  reduced-motion).

FIRST do the three LIQUID-GLASS CORRECTIONS at the top of the doc (owner feedback, CSS-only, quick): (1) kill the
moving background — remove the liquidSheen drift on .clay::before + calm the ambient body::before drift so the
glass is still; (2) tone down the over-hot .dock-item.active button — soft tint + accent indicator + orange (not
white-on-bright), glow removed; (3) fix light-mode readability — the plan-mix donut center number is unreadable on
light; sweep all light-mode text/label tokens to ≥4.5:1 without regressing dark. Verify in both skins × light+dark,
then commit those as their own small PR.

Then build PHASE BY PHASE from the doc: A FE systems (delayed tooltips, mask/reveal+copy, command palette+shortcuts,
responsive polish); B staff full_name + preferences (+ avatar-from-name, Privacy prefs); C email-code step-up on
EVERY sensitive action (generic request/confirm + require_step_up grant window); D notifications center (dedicated
staff_notifications table + bell + generation hooks); E staff device sessions + last_login + revoke + logout-all
(no remember-me, keep 2h). ONE phase per branch/PR; ask me before the next.

VERIFY (don't just claim it): BE tests per endpoint (role-gating; step-up required+verified on every gated
mutation — missing/invalid/expired→401/403, valid grant→effect+admin_actions row; session validate/revoke/logout-
all; notifications from real events + mark-read + unread; no secret leaks) — mirror tests/integration/test_admin_*.
Keep the suite green: cd backend && DATABASE_URL=postgresql+psycopg://foxy:foxy@localhost:5433/foxy
API_KEY_PEPPER=testpepper .venv/Scripts/python.exe -m pytest tests/integration -q  (do NOT export
SESSION_SECRET/STAFF_SESSION_SECRET). node --check both inline <script> blocks. Manually verify tooltips, mask/
reveal+copy, palette, bell, step-up-on-every-danger-action, device revoke, name initial — in both skins × light/
dark — before the PR.

WORKFLOW: one phase per branch off origin/main (e.g. feat/admin-parity-ux), tests with every endpoint, suite
green, PR to main. This machine may need SSH for git push. Start by reading docs/ADMIN_PARITY.md + the files
above, then invoke ui-ux-pro-max and give me your Phase A+B plan before coding.
```
