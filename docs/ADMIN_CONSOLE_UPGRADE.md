# Foxy Audit — Ops (Admin) Console upgrade (build spec + UI/UX brief)

## Context
The staff/ops console (`admin.foxyaudit.tech/admin/`, single file `foxy-adminpage/index.html`) has had a strong
visual pass — top bar, a richer Overview (system-health summary, recent-orgs, open-alerts panels), a "Traffic
feed". But several pages are still **shallow or empty**: the **Orgs** drill-down shows only 5 scalar counts,
**Staff** does only create/disable, **Inbox** needs a real redesign, and **Settings is literally a dark-mode
toggle + read-only identity**. The owner wants a **backend-heavy, "go all in"** upgrade that makes every page
deep and adds four new sidebar sections — powered by **real endpoints only, no fake data or placeholders**, with
the UI built to a high bar using the **`ui-ux-pro-max`** skill.

## How the executing agent should use this doc
You (the executing Claude) have real, persistent repo access — **read the actual code before writing any**;
never guess an API, table, or helper you can verify. **First action: invoke the `ui-ux-pro-max` skill**
(`Skill: ui-ux-pro-max`) and keep it in the loop for every page — this is a UI-quality build, not just plumbing.
Build **phase by phase**, smallest correct change first; open one PR per phase; keep the backend suite green.

## System primer
Three-tier product (PyQt6 desktop pet `desktop/`, `@foxy.audit` SDK `sdk/`, FastAPI+Postgres `backend/`). The
ops console is a **single-file SPA** `foxy-adminpage/index.html` (~1650 lines: one `<style>`, one main
`<script>` at ~L940, a `<script>` at ~L943 that monkey-patches `fetch` to add the `X-CSRF-Token` header). It
talks to the **admin sub-app** mounted at `/admin` in `backend/app/main.py` (routes `/admin/v1/*`), which has
its own staff session cookie (`foxy_staff_session`) and CSRF. Admin routers: `auth_staff, admin_orgs,
admin_staff, admin_stats, admin_data, admin_inbox, admin_health, admin_grading, admin_anchors, admin_alerts`,
registered in the admin loop in `main.py:160`.

**Staff roles** (`backend/app/auth.py:184`): `_PLATFORM_ROLES = {viewer:0, operator:1, superadmin:2}`.
- `require_staff` — any logged-in staff; reads cross-org (staff intentionally bypass RLS).
- `require_platform_role("viewer"|"operator"|"superadmin")` — gate; superadmin passes everything.
- `set_org_scope_for_staff(db, org_id)` (auth.py:219) — deliberate single-org RLS drill-down for scoped reads.
- Audit: `record_admin_action(db, staff, action, *, target_org_id=None, target_type=None, target_id=None,
  detail=None, ip=None)` at `backend/app/admin_audit.py:26` — stages an `admin_actions` row; **caller commits**
  in the same transaction as the mutation. `client_ip(request)` helper at admin_audit.py:17.

## Current admin surface (from code + screenshots — don't rebuild what's done)
Nav: **Overview · OPS · Orgs · Traffic · Staff · Data · Inbox · Settings** (OPS fronts 4 sub-pages
health/deadletter/anchors/alerts). FE architecture: `api(p,o)` fetch wrapper (~L945), `go(page,el)` router with a
literal **page→loader map** (~L1225), `can(min)` role gate (`ROLE_RANK`, ~L948), custom `fauxselect` dropdowns
(~L951), pure **CSS-bar** funnels (`funnelRow`, no chart lib), `.clay` claymorphism cards + a CSS-variable token
system (fox-orange, glassmorphism). Per-loader → endpoint map is in the repo; reuse it.
- **Fully built / endpoint-backed (enrich, don't rebuild):** OPS (all 4 tabs → `/health`, `/grading/deadletter`
  (+requeue), `/anchors` (+re-anchor), `/alerts` (+ack)), Traffic feed (`/traffic`, client-side filter), Data
  browser (`/data/*` generic CRUD), Inbox (`/inbox` full: read/claim/release/reply + unread poll), Overview
  (KPIs + funnels + system-health summary + recent-orgs + open-alerts).
- **Thin / target for this build:** **Orgs** (list + drawer of 5 counts + prompt-based suspend/enable/plan),
  **Staff** (create/disable only), **Inbox** (works but needs redesign), **Settings** (dark-mode toggle +
  read-only identity — empty).

## Non-negotiable rules
- **No fake/placeholder data — ever.** Every tile/table/chart/list is wired to a real endpoint; honest empty
  states ("No … yet"). Never invent counts, names, revenue, or rows.
- **Reuse, don't rebuild:** gate with `require_platform_role`; scope per-org reads with `set_org_scope_for_staff`;
  **audit every staff mutation** via `record_admin_action` (committed in the same txn); reuse org-scoped copies of
  `verify.py` / `logs.py` / `anchor.py` / `keys.py` / `billing.py` / `mfa.py` / `email.py` / `password_reset.py`.
- **Never expose secrets** (keep the `password_hash`/`key_hash` stripping; keys shown-once only).
- **Charts = inline SVG/Canvas** (the admin CSP blocks JS/font CDNs — no libraries). **Preserve the existing
  token system** and fox-orange glass/clay look — enhance it, don't replace it.
- FE: extend the single file; reuse `api()`/`go()` (add loaders to the page→loader map)/`can()`/`fauxselect`.
  Replace `prompt()`/`confirm()` action flows with real modals/forms. Keyboard + a11y per `ui-ux-pro-max`.

## Using `ui-ux-pro-max` to its fullest (do this on every page)
1. `Skill: ui-ux-pro-max` first; skim its priority table and read `references/quick-reference.md` +
   `references/pro-rules.md` (the pre-delivery checklist). Run its search script (full path inside the skill dir).
2. **Stack = plain HTML/CSS/vanilla JS** (no Tailwind/framework) — apply the `html`/`html-tailwind` stack rules
   conceptually with inline styles + the existing CSS vars.
3. Follow **priority 1→10**: Accessibility (contrast ≥4.5:1 on the dark theme, keyboard nav, aria-labels,
   focus rings) → Touch (44px targets) → Performance (reserve space, no layout shift) → Style/product (admin
   dashboard product type; query `product`+`style`) → Layout/responsive → Typography/Color (query `typography`,
   `color` — but keep the fox tokens) → Animation (150–300ms, reduced-motion) → Forms/feedback (real labels,
   inline errors, modals not `prompt()`) → Navigation → **Charts** (query the `chart` domain, 25 types — pick per
   data; build inline SVG/Canvas).
4. Product-type reasoning: this is a **data-dense internal ops console** — favor legible tables, scannable KPI
   tiles with deltas/sparklines, drill-downs, empty/loading/skeleton states, and status semantics that don't rely
   on color alone.

## Design system — fonts, color palette, TWO theme variants, charts (cross-cutting; built in Phase 0)

**Typography — pair a font with Poppins.** Primary display/UI = **Poppins** (already the console's font — match the
CURRENT load mechanism exactly; the admin CSP blocks external CDNs, so fonts stay self-hosted/embedded woff2 like
Poppins is today — verify how it's loaded before adding any face). Pair Poppins with:
- a **monospace** for data/hashes/IDs/timestamps (ledger, audit, traffic, receipts) — e.g. **JetBrains Mono** or
  **IBM Plex Mono** (embed woff2, CSP-safe);
- optionally a neutral **body/reading** face for long copy (inbox messages) — e.g. **Inter** — only if embeddable
  CSP-safe, else Poppins for body.
Query `ui-ux-pro-max` **`typography`** (74 pairings) to finalize the pairing. Enforce base 16px, line-height ~1.5,
a clear scale (display/H1/H2/label/body/mono), and **tabular-nums** on numeric columns.

**Color palette — orange as the main color.** Build a full, accessible palette (query `ui-ux-pro-max` **`color`**,
192 palettes; choose a warm-orange primary and **map it onto the existing token names** so nothing else breaks):
- **Primary/brand:** a fox-orange ramp (50→900) → `--fox`, `--fox2`, `--fox3`, `--foxdeep`, `--fox-glow`.
- **Neutral surface ramp** for light + dark (bg, surface, surface-solid, elevated, hairlines `--line`/`--line2`).
- **Semantic:** success/safe, warn, breach/danger, info (`--safe-bg`/`--warn-bg`/`--breach-bg`/`--info-bg`) — tuned
  to ≥4.5:1 contrast on dark; never color-only (always pair an icon/label).
- **One accent** (`--blue`) kept for chart-series differentiation. Run the skill's palette **validator**; verify
  contrast on both themes and both variants.

**TWO theme variants (the headline ask) — deliver both, switchable.** Same palette + fonts, different surface
treatment, so the owner can pick. Ship **Variant A first, then Variant B**:
- **Variant A — Glassmorphism (build FIRST):** translucent surfaces + `backdrop-filter` blur, thin luminous
  hairlines, soft glow, a subtle gradient base, frosted cards, low-opacity fills. Formalize the console's existing
  `--glass`/blur hints into a *complete* glass token set.
- **Variant B — Skeuomorphism / Claymorphism (the ORIGINAL look):** soft **extruded, tactile** surfaces — layered
  inner+outer box-shadows, rounded "clay" cards, glossy top rim (`--rim`), matte fills, raised/pressed interaction
  states. This is what the console shipped with; restore + refine it as a *complete* token set.
Implement as a **token-driven theme system** (mirror the customer dashboard's approach in
`foxy-dashboard/foxy-audit-premium.html`): each variant is ONE fully-populated token dict exposing **identical
keys**; a **theme/skin picker** (top bar + Settings) flips `data-theme` (light/dark) and `data-skin` (glass/clay)
on `<html>` and every `.clay`-style panel re-skins from tokens — **no component special-cases a variant**. Persist
the choice in localStorage (extend the existing dark-mode toggle at ~L1353). **Phase 0 is done only when BOTH
skins render cleanly across every existing page.**

**Charts — use where data is temporal or categorical.** Inline **SVG/Canvas only** (CSP — no libraries). Query
`ui-ux-pro-max` **`chart`** (25 types) and pick per data set:
- **Overview:** area/line time-series (interactions · breaches · signups · revenue, 7/30-day) · KPI **sparklines +
  deltas** vs prior period · **plan-mix donut** · top-orgs bar.
- **System health:** grading-queue + worker-heartbeat trend (line) · anchor-wallet balance trend.
- **Org 360:** per-org usage area + breach-rate. **Revenue:** MRR line + revenue-by-plan stacked bar.
- **Security:** failed-logins-over-time line + top-offender bar. **Leads:** funnel (new→trial→converted→churned).
Every chart: legend, tooltip/label, **non-color-only** encoding, reserved space (no layout shift), reduced-motion.
Build a tiny reusable inline-SVG chart helper (line/area/bar/donut/sparkline) that reads theme tokens for colors.

**Motion.** 150–300ms ease; spatial continuity on drill-downs/tab changes; respect `prefers-reduced-motion`.

## Layout must NOT look "vibe-coded" (intentional design, not AI card-soup)
The single biggest failure mode here is a generic, auto-generated look: a wall of identical rounded cards, every
element the same weight, centered everything, evenly-spaced-everything, default gaps, no rhythm. **Avoid it
deliberately.** Rules the executing agent must hold to on every page:
- **Design on a grid.** Adopt an explicit 12-column (or 8pt) grid and a **spacing scale** (4/8/12/16/24/32/48) —
  no arbitrary one-off pixel values. Align every panel edge to the grid; things that relate line up, things that
  don't are clearly separated. Asymmetry is fine and often better than a centered wall.
- **Real visual hierarchy.** One clear primary element per view (the hero KPI row or the main table), supporting
  panels visually subordinate (smaller, lighter, lower elevation). Vary card **size and density** by importance —
  do NOT render every metric as an identical equal-sized tile. Use the type scale to separate title / metric /
  label / caption; don't let everything sit at one size.
- **Dense where it should be dense.** This is an ops console, not a marketing page — tables are first-class:
  compact rows, tabular-nums, right-aligned numerics, sticky headers, zebra/hover, column alignment. Resist the
  urge to wrap every datum in a big padded card. Reserve big cards for genuine summary; use tight lists/tables for
  detail.
- **Purposeful whitespace & rhythm.** Consistent vertical rhythm between sections; generous padding inside a
  primary card, tighter inside dense tables. Whitespace should feel *composed*, not just a default margin
  everywhere.
- **Restraint in decoration.** Don't over-apply glow/blur/shadow to everything — elevation should signal
  importance, so if everything glows, nothing does. Borders/hairlines to separate, elevation only for things that
  truly float (modals, menus, the hero row). No gratuitous gradients on every surface.
- **Considered empty/loading states** (skeletons that match the real layout), aligned icon+label status chips, and
  section headers with a one-line purpose caption — these are what separate a designed console from a scaffold.
- **Consistency is the tell.** One button system, one chip system, one table system, one card system reused
  everywhere. Divergent spacing/paddings/corner-radii across pages is the #1 signal of vibe-coded UI — enforce the
  tokens.
Use `ui-ux-pro-max`'s `layout`, `ux`, and `product` domains to ground these choices; the exit bar for each page is
"does this look like a designer laid it out on a grid," not "did every field render."

## The build — phased

### Phase 0 — Design system & TWO theme variants (do this FIRST; FE-only, no data change)
Establish the design foundation before deepening any page, so every later phase inherits it.
- **Fonts:** confirm how Poppins is loaded today (CSP-safe), then pair it per the Design-system section (Poppins +
  a CSP-safe monospace for data, optional Inter for body). Wire a type scale + tabular-nums.
- **Palette:** derive a validated warm-orange palette (ui-ux-pro-max `color`) and map it onto the existing token
  names; fix any contrast below 4.5:1 on dark.
- **Two skins:** refactor the CSS into a **token-driven theme system** with two fully-populated skins — **Variant A
  Glassmorphism (build first)** then **Variant B Skeuomorphism/Clay (the original)** — each exposing identical
  token keys; add a **skin picker** (top bar + Settings) that flips `data-skin`/`data-theme` on `<html>` and
  persists to localStorage. Refit every existing panel (`.clay`, `.kpi`, `.chip`, `.tbl`, dock, funnels) to read
  tokens only — no per-variant special-casing.
- **Chart helper:** add the reusable inline-SVG helper (line/area/bar/donut/sparkline) that reads theme tokens.
- **Exit criteria:** both skins × light/dark render cleanly across ALL current pages; nothing hardcodes a color;
  `node --check` clean. Ship as its own PR (`feat/admin-design-system`) so the owner can eyeball both variants
  before feature work lands on top.

### Phase 1 — Deep dives on existing pages (mostly new *read* endpoints + rich FE)
**A. Org 360 (Orgs page).** Turn the 5-count drawer into a full tenant view (rich drawer or dedicated sub-page).
- New (staff, `set_org_scope_for_staff`): `GET /admin/v1/organizations/{id}/overview` (profile + usage timeseries
  from `usage_daily` + ledger height + last-verify status + breach count + plan/quota/trial + IP-allowlist),
  `…/{id}/users`, `…/{id}/keys`, `…/{id}/logs` (staff-scoped ledger — reuse `logs.py`), `…/{id}/verify` (reuse
  `verify.py`), `…/{id}/breaches` (over `audit_logs.gemini_verdict`).
- FE: tabbed Org 360 (Overview/Users/Keys/Ledger/Breaches/Policy) with a usage sparkline/area chart, real user &
  key lists, ledger drill + one-click verify. `admin_orgs.py`, new loaders.
**B. Overview trends + Ops trends.** `GET /admin/v1/stats/timeseries` (interactions/breaches/signups/revenue per
day over `usage_daily`/`invoices`/`traffic_events`) → real area/line charts + KPI deltas/sparklines vs prior
period; plan-mix donut; top-orgs-by-usage. Enrich **System health** with worker/queue trend + anchor-wallet trend
+ persisted alert history (from `admin_actions` `alert.ack`). All inline SVG/Canvas.
**C. Inbox redesign (FE-mostly).** Filters (unread/priority/status/source), per-filter unread counts, search,
canned replies, clearer claim/lock + assignment, better reply composer, empty/loading states. Endpoints exist;
optionally log inbox actions to `admin_actions` (small BE).

### Phase 2 — Org lifecycle + Staff management (new *write* endpoints, all audited)
**D. Org lifecycle actions** (replace `prompt()` with modals): per-org **API-key revoke** `POST
…/{id}/keys/{key_id}/revoke`; **IP-allowlist** `GET/PUT …/{id}/ip-allowlist`; **comp/trial extend** `POST
…/{id}/trial`; **offboard/soft-delete** `POST …/{id}/offboard`; keep suspend/enable/plan. `admin_orgs.py`.
**E. Staff management** (`admin_staff.py`, superadmin): **role change** `POST /admin/v1/staff/{id}/role`
(promote/demote `platform_role`), **re-enable** `…/{id}/enable`, **MFA reset** `…/{id}/mfa/reset`, **activity**
`GET /admin/v1/staff/{id}/activity` (join `login_events` + `admin_actions`), and **log staff login/MFA/logout**
into `admin_actions` (add `record_admin_action` calls in `auth_staff.py`). FE: per-row role dropdown, enable,
reset-MFA, last-login column, activity drawer.

### Phase 3 — Real Settings (new tables + endpoints)
**F. Platform config & feature flags** — migration `platform_config` (key, value JSONB, updated_by, updated_at) +
`GET/PUT /admin/v1/config` (superadmin, audited): editable anchor cadences, default quotas, alert thresholds,
feature flags. New router `admin_config.py`. **G. Broadcast/announcements** — `POST /admin/v1/broadcast`
(superadmin): banner (store in `platform_config`/a small `platform_announcements` table) + optional bulk email
(reuse `email.py`). **H. Staff self-service** — `POST /admin/v1/auth/change-password`, self MFA enrol/reset
(reuse `mfa.py`), recent sessions/activity (from `login_events`). **I. Light** — staff prefs (theme, density,
notification prefs) persisted locally (localStorage) + surfaced in Settings. FE: rebuild `#page-settings` from a
one-toggle stub into real cards.

### Phase 4 — Four new sidebar sections (new endpoints over zero-surface data)
Add nav items + pages (reuse the dock + `go()` + `can()` patterns). All read-heavy, mutations audited.
- **J. Revenue / Billing** — new `admin_billing.py`: `GET /admin/v1/revenue` (MRR + revenue by plan + active/
  trial/churned from `invoices.amount_cents` + `subscription_status`), `GET /admin/v1/billing/stripe-events` +
  `POST …/{id}/replay` (failed-webhook replay — `stripe_events` is hard-locked in the browser today).
- **K. Security (login monitor)** — new `admin_security.py`: `GET /admin/v1/security/logins` over `login_events`
  (**zero surface today**): failed-login watchlist by IP/email, per-org login history, lockout signals.
- **L. Audit-log viewer** — new `admin_audit_view.py`: `GET /admin/v1/audit` (filter `admin_actions` by
  actor/action/org/date + pagination + export) — a purpose-built viewer beyond the raw Data browser.
- **M. Leads pipeline** — new `admin_leads.py`: `GET /admin/v1/leads` (kanban new/trial/converted/churned, UTM,
  `converted_org_id` linkage) + `POST …/{id}/status` transition, over `marketing_leads`.

## New backend surface (summary)
- **New routers:** `admin_config.py`, `admin_billing.py`, `admin_security.py`, `admin_audit_view.py`,
  `admin_leads.py`; **extend:** `admin_orgs.py` (Org 360 + lifecycle), `admin_staff.py` (role/enable/MFA/activity),
  `admin_stats.py`/`admin_health.py` (timeseries), `auth_staff.py` (self-service + login audit), `admin_inbox.py`
  (optional action audit). Register every new router in the admin loop in `main.py:160`.
- **Migrations (numbered after the current head — check `backend/migrations/versions/`):** `platform_config`
  (Phase 3); optional `platform_announcements` (Phase 3). Everything else reuses existing tables
  (`login_events, admin_actions, invoices, stripe_events, marketing_leads, usage_daily, api_keys, users,
  org_policies, chain_anchors`). Enable+FORCE RLS with an org-scope policy on any new per-org table (mirror the
  pattern in recent migrations).

## Where to work
- **Backend:** `backend/app/routers/admin_*.py` (extend + new files), `backend/app/routers/auth_staff.py`, one or
  two Alembic migrations, reuse `admin_audit.py`, `auth.py` guards, and org-scoped `verify.py`/`logs.py`/
  `anchor.py`/`keys.py`/`billing.py`/`mfa.py`/`email.py`.
- **Frontend:** the single file `foxy-adminpage/index.html` — new dock items, new `<section class="page">` panels,
  new loaders added to the `go()` page→loader map, real modals replacing `prompt()`, inline SVG/Canvas charts.
- **Do NOT touch:** the customer dashboard (`foxy-dashboard/`), the `/v1/*` customer API, or the marketing site
  (`foxy-sale-page/`). Keep the diff inside the admin surface.

## Testing (per feature / phase)
Backend TDD mirroring `backend/tests/integration/test_admin_*.py` (e.g. `test_admin_inbox.py`,
`test_admin_guard.py`): (1) **role-gating** — viewer/operator/superadmin get the right 200/403; (2) **per-org
scoping** — a scoped read/action can't leak or hit the wrong tenant; (3) an **`admin_actions` row is written** on
each mutation; (4) the **effect** is correct; (5) **no secret** is ever returned. Keep the whole suite green
(`cd backend && DATABASE_URL=postgresql+psycopg://foxy:foxy@localhost:5433/foxy API_KEY_PEPPER=testpepper
.venv/Scripts/python.exe -m pytest tests/integration -q`; **do NOT export SESSION_SECRET/STAFF_SESSION_SECRET** —
conftest sets them via setdefault). FE: syntax-check every inline `<script>` block with `node --check`. Manual
smoke: log in as a superadmin staff row, walk each page, exercise every new action, confirm real data + honest
empty states + audit rows.

## Branch & merge workflow
Cut a branch off `origin/main` **one phase per branch/PR** (e.g. `feat/admin-design-system`, `feat/admin-org360`,
`feat/admin-staff-mgmt`, `feat/admin-settings`, `feat/admin-new-sections`). Keep the diff in the admin surface, add tests with every
endpoint, keep the suite green, open a PR against `main`. On merge, CI+CD redeploy; hard-refresh
`admin.foxyaudit.tech/admin/`. Git note: this machine may require SSH (`git@github.com:…`) for pushes.

---

## Full change inventory (FE · BE · DB, per phase)
> Verify every table/column/helper against `backend/app/models.py` and the existing routers **before coding** —
> the names below are the plan of record, not a guarantee they match the schema verbatim. Reuse first; add only
> what's missing. Every endpoint that mutates state MUST call `record_admin_action(...)` and commit in the same
> transaction, and MUST be gated by `require_platform_role(...)`. Every per-org read MUST go through
> `set_org_scope_for_staff(db, org_id)`.

### Phase 0 — Design system (FE + DB: none)
**FE (`foxy-adminpage/index.html`) only. No BE, no DB.**
- **CSS tokens:** refactor `:root` into a complete token contract — surface ramp (`--bg --surf --surf2 --elev
  --line --line2`), brand ramp (`--fox --fox2 --fox3 --foxdeep --fox-glow`), semantic (`--safe/-bg --warn/-bg
  --breach/-bg --info/-bg`), accent (`--blue`), text (`--ink --muted --faint`), radii/space/shadow/blur/rim.
- **Two skins × two themes:** `[data-skin="glass"]` / `[data-skin="clay"]` × `[data-theme="light|dark"]` on
  `<html>`, each overriding the SAME token keys. No component reads a skin name.
- **Fonts:** confirm Poppins load path; add CSP-safe monospace (data/hashes) + optional Inter (body) as embedded
  woff2. Type-scale utility classes + `font-variant-numeric: tabular-nums` on numeric cells.
- **Skin/theme picker:** control in top bar **and** Settings; persist `foxy_admin_skin` + `foxy_admin_theme` to
  localStorage; apply on boot before first paint (no flash).
- **Chart helper:** one module `chart(el, {type, series, ...})` supporting line/area/bar/stacked-bar/donut/
  sparkline, reading theme tokens for colors, with legend + hover label + reduced-motion + reserved height.
- **Refit:** every existing panel (`.clay .kpi .chip .tbl`, dock, funnels, drawers, modals) to tokens only.

### Phase 1 — Deep dives
**DB:** none (reads over existing `usage_daily`, `api_keys`, `users`, `audit_logs`, `org_policies`, `invoices`,
`traffic_events`, `admin_actions`).
**BE — new/extended endpoints** (all `require_platform_role("viewer")` for reads; org-scoped):
- `admin_orgs.py`: `GET /organizations/{id}/overview` · `/users` · `/keys` · `/logs` · `/verify` · `/breaches`.
- `admin_stats.py`: `GET /stats/timeseries?metric=&days=` (interactions/breaches/signups/revenue) · plan-mix ·
  top-orgs-by-usage.
- `admin_health.py`: extend with queue/worker trend + anchor-wallet-balance trend series.
- `admin_inbox.py` (optional): log claim/reply to `admin_actions`.
**FE:**
- **Org 360:** replace the 5-count drawer with a tabbed view (Overview/Users/Keys/Ledger/Breaches/Policy); usage
  area chart + KPI sparklines; real user & key tables; ledger drill + one-click verify button.
- **Overview:** area/line trend charts, KPI deltas + sparklines vs prior period, plan-mix donut, top-orgs bar.
- **System health:** queue/worker + anchor-wallet trend charts; alert history list.
- **Inbox redesign:** filter rail (unread/priority/status/source) with per-filter counts, search, canned replies,
  clearer claim/lock + assignment, better composer, skeleton/empty states.
- New loaders registered in the `go()` page→loader map.

### Phase 2 — Org lifecycle + Staff management (writes, audited)
**DB:** none if `api_keys`/`orgs` already carry the needed columns (revoked_at, ip_allowlist, trial_ends_at,
offboarded_at) — **verify in models.py**; add a narrow migration only for any genuinely missing column.
**BE (all `require_platform_role("operator"|"superadmin")`, audited):**
- `admin_orgs.py`: `POST /organizations/{id}/keys/{key_id}/revoke` · `GET/PUT /organizations/{id}/ip-allowlist` ·
  `POST /organizations/{id}/trial` · `POST /organizations/{id}/offboard` (soft-delete).
- `admin_staff.py` (superadmin): `POST /staff/{id}/role` · `POST /staff/{id}/enable` · `POST /staff/{id}/mfa/reset`
  · `GET /staff/{id}/activity` (join `login_events` + `admin_actions`).
- `auth_staff.py`: add `record_admin_action` on staff login / MFA / logout.
**FE:** replace every `prompt()`/`confirm()` with real modals/forms — org action menu (revoke key, edit
IP-allowlist, extend trial, offboard); staff table with per-row role dropdown, enable, reset-MFA, last-login
column, activity drawer.

### Phase 3 — Real Settings
**DB — migrations (numbered after current head in `backend/migrations/versions/`):**
- `platform_config(key TEXT PK, value JSONB, updated_by, updated_at)`.
- optional `platform_announcements(id, title, body, level, active, starts_at, ends_at, created_by, created_at)`.
- No per-org RLS needed (platform-global); still FORCE RLS with a staff-only policy per the recent-migration pattern.
**BE (superadmin, audited):**
- `admin_config.py`: `GET /config` · `PUT /config` (anchor cadence, default quotas, alert thresholds, feature flags).
- `admin_config.py` or `admin_broadcast`: `POST /broadcast` (banner + optional bulk email via `email.py`).
- `auth_staff.py`: `POST /auth/change-password` · self MFA enrol/reset (reuse `mfa.py`) · `GET /auth/sessions`
  (from `login_events`).
**FE:** rebuild `#page-settings` from the one-toggle stub into cards — Platform config & flags · Broadcast composer
· Staff self-service (password/MFA/sessions) · Local prefs (theme/skin/density/notifications in localStorage).

### Phase 4 — New sidebar sections
**DB:** none (reads over `invoices`, `stripe_events`, `login_events`, `admin_actions`, `marketing_leads`); `POST`
lead-status transition writes `marketing_leads.status` + an `admin_actions` row.
**BE — new routers (register each in `main.py:160`):**
- `admin_billing.py`: `GET /revenue` (MRR, revenue-by-plan, active/trial/churned) · `GET /billing/stripe-events` ·
  `POST /billing/stripe-events/{id}/replay`.
- `admin_security.py`: `GET /security/logins` (failed-login watchlist by IP/email, per-org history, lockout signals).
- `admin_audit_view.py`: `GET /audit` (filter `admin_actions` by actor/action/org/date + pagination + export).
- `admin_leads.py`: `GET /leads` (kanban buckets, UTM, `converted_org_id`) · `POST /leads/{id}/status`.
**FE:** four new dock items + pages (reuse dock/`go()`/`can()`): Revenue (MRR line + revenue-by-plan stacked bar) ·
Security (failed-login line + top-offender bar + tables) · Audit viewer (filterable table + export) · Leads
(kanban with funnel chart + drag/transition).

---

## Paste-ready prompt for the executing Claude
(Copy the block below into the other Claude, in the repo root.)

```
You're working in the Foxy Audit repo. Build out the STAFF/OPS CONSOLE (admin.foxyaudit.tech, the single file
foxy-adminpage/index.html + the /admin/v1/* API) following docs/ADMIN_CONSOLE_UPGRADE.md — read that file first;
it's the source of truth (system primer, current surface, roles, reuse points, conventions, the phased build, the
new endpoints/tables, testing, and the branch/merge workflow).

FIRST ACTION: invoke the ui-ux-pro-max skill (Skill: ui-ux-pro-max) and keep it in the loop for EVERY page — this
is a UI-quality build, not just plumbing. Skim its priority table, read references/quick-reference.md and
references/pro-rules.md, and run its search script (full path inside the skill dir) for the `product`, `style`,
`ux`, `typography`, `color`, and `chart` domains as you design each page. Stack = plain HTML/CSS/vanilla JS
(no framework) — apply its html/html-tailwind guidance conceptually with inline styles + the existing CSS vars.

DESIGN (Phase 0, do FIRST): (1) pair a font with Poppins — Poppins + a CSP-safe monospace for data/hashes/IDs,
optional Inter for body; verify how Poppins is currently loaded and keep it CSP-safe (no external CDN). (2) Build
an accessible ORANGE-primary color palette (query ui-ux-pro-max `color`; validate contrast ≥4.5:1) and map it onto
the existing token names. (3) Deliver TWO switchable, token-driven theme variants — Variant A GLASSMORPHISM (build
FIRST), Variant B SKEUOMORPHISM/CLAYMORPHISM (the ORIGINAL look) — each a fully-populated token set with identical
keys, via a data-skin + data-theme picker (top bar + Settings) persisted to localStorage; every panel re-skins
from tokens with NO per-variant special-casing. (4) Add a reusable inline-SVG chart helper
(line/area/bar/donut/sparkline) that reads theme tokens, and use charts wherever data is temporal/categorical
(Overview trends+sparklines+plan-mix, Health queue/anchor trends, Org 360 usage, Revenue MRR, Security logins,
Leads funnel). Both skins × light/dark must render cleanly across every existing page before any feature phase.

You have real repo access — READ the actual code before writing any (foxy-adminpage/index.html;
backend/app/routers/admin_*.py + auth_staff.py; backend/app/auth.py guards; backend/app/admin_audit.py;
backend/app/main.py admin loop ~L160; backend/app/models.py). Never guess an endpoint, table, or helper.

Build PHASE BY PHASE from the doc — Phase 0 (DESIGN SYSTEM: font pairing with Poppins + orange palette + TWO
switchable theme variants, Glassmorphism first then the original Skeuomorphism/clay, + the inline-SVG chart
helper), Phase 1 (Org 360 + Overview/Ops trends + Inbox redesign), Phase 2 (org lifecycle + staff management),
Phase 3 (real Settings: config/flags + broadcast + staff self-service), Phase 4 (new sidebar sections:
Revenue/Billing, Security/login monitor, Audit-log viewer, Leads pipeline). Do ONE phase per branch/PR; ask me
before jumping to the next.

HARD RULES (do not break):
- NO fake/placeholder data — ever. Every tile/table/chart/list is wired to a real /admin/v1 endpoint; honest
  empty states. Never invent counts, names, revenue, or rows.
- Reuse, don't rebuild: gate with require_platform_role; scope per-org reads with set_org_scope_for_staff; AUDIT
  every staff mutation via record_admin_action (committed in the same transaction); reuse org-scoped
  verify.py/logs.py/anchor.py/keys.py/billing.py/mfa.py/email.py. Never expose secrets (keep password_hash/
  key_hash stripping; keys shown once).
- Charts = inline SVG/Canvas only (admin CSP blocks CDNs/libraries). PRESERVE the existing fox-orange glass/clay
  token system and the api()/go()/can()/fauxselect patterns — enhance the look, don't replace it. Replace
  prompt()/confirm() flows with real modals/forms. Accessibility per ui-ux-pro-max (contrast ≥4.5:1 on dark,
  keyboard nav, aria, focus rings, 44px targets, reduced-motion).
- The layout must NOT look "vibe-coded" — no wall of identical equal-sized rounded cards, no centered-everything,
  no default-gap-everywhere. Design on an explicit grid + spacing scale (4/8/12/16/24/32/48), give each page ONE
  clear primary element with subordinate supporting panels (vary card size/density by importance), treat tables as
  first-class (compact, tabular-nums, right-aligned numerics, sticky headers), use elevation/glow only to signal
  importance, and keep ONE reused button/chip/table/card system across every page. Read the "Layout must NOT look
  'vibe-coded'" section of the doc and use ui-ux-pro-max's `layout`/`ux`/`product` domains. The bar per page is
  "does this look like a designer laid it out on a grid," not "did every field render."
- Follow the "Full change inventory (FE · BE · DB, per phase)" section of the doc for the exact endpoints, tables,
  migrations, loaders, and components each phase must deliver.
- Touch ONLY the admin surface — new backend/app/routers/admin_*.py (register each in the main.py admin loop) and
  foxy-adminpage/index.html. Do NOT touch the customer dashboard (foxy-dashboard/), the customer /v1/* API, or the
  marketing site (foxy-sale-page/).

VERIFY (don't just claim it):
- Backend tests per new endpoint (role-gating 200/403, per-org isolation, an admin_actions row is written, the
  effect is correct, no secret leaks) — mirror tests/integration/test_admin_inbox.py / test_admin_guard.py. Keep
  the suite green: cd backend && DATABASE_URL=postgresql+psycopg://foxy:foxy@localhost:5433/foxy
  API_KEY_PEPPER=testpepper .venv/Scripts/python.exe -m pytest tests/integration -q  (do NOT export
  SESSION_SECRET/STAFF_SESSION_SECRET — conftest sets them). node --check every inline <script> block.
- Run it locally and screenshot/verify each page against a real superadmin staff login before opening the PR.

WORKFLOW: one phase per branch off origin/main (e.g. feat/admin-org360), tests with every endpoint, suite green,
PR against main. This machine may need SSH for git push (git@github.com:fatimaatta-09/Foxy-Audit.git).

Start by reading docs/ADMIN_CONSOLE_UPGRADE.md + the files above, then invoke ui-ux-pro-max and give me your
Phase 1 plan (the new endpoints + the Org 360 / Overview-charts / Inbox-redesign FE) before coding.
```
