# Foxy Audit — Customer Dashboard redesign (build spec + UI/UX brief)

## Context
The customer dashboard (`foxy-dashboard/foxy-audit-premium.html`, served at `/dashboard`) shipped its full P0–P3
feature set but the **UI/UX is weak**: the login screen looks poor and has **no persistent login**; the "Get set
up" onboarding is uninviting; Capture-coverage, Threats, Ledger, Verify, Policy, Export, Access, Billing and
Settings each feel thin, dead, or cramped; charts are ad-hoc CSS bars; Settings has **no sidebar icon** (only the
hardcoded "F" chip reaches it); sensitive values (org id, chain head, anchor roots) are always visible with no
mask/reveal; there are no delayed hover tooltips, no skeletons, no notifications/activity, and the avatar is a
static "F". The owner wants a **full redesign** — deep, chart-rich, intentional (NOT "vibe-coded"), lively,
production-ready — covering **FE + BE + DB**, with real data everywhere.

**Decisions locked:** (1) persistent login via **DB-backed device sessions** (remember-me + "active devices" +
log-out-everywhere); (2) **every sensitive action requires an emailed one-time code** (extend the existing
key-regeneration step-up pattern); (3) add a **`users.full_name`** column — identity defaults to email, uses the
name once set; (4) **backend-heavy, all-in** (new tables + aggregation endpoints; phased, one PR per phase).
**Themes ARE in scope** — **2 themes (light + dark) × 3 skins (Original + Skeuomorphism + Liquid Glass) = 6
combos**, token-driven & switchable, reusing the admin technique in `docs/ADMIN_LIQUID_GLASS_REDESIGN.md`. **Keep
the existing dark palette byte-for-byte** (the owner's dark colors are perfect), **add a new light palette**, and
**keep the Original dashboard style as a first-class skin** (its current *surface look*). **The font is uniform
across all 6 combos: the admin's Poppins + JetBrains Mono pairing** (replaces Unbounded/Space Mono everywhere — not
per-skin). Also **add a top bar** (like the admin console's). This is a **customer-surface** change (dashboard +
`/v1/*`), never the admin console or marketing site.

## How the executing agent should work
**First action: `Skill: ui-ux-pro-max`** and keep it in the loop for EVERY page (query `product`, `style`, `ux`,
`layout`, `typography`, `color`, `chart`). Read the actual code before writing any — never guess an endpoint,
table, or helper. Build **phase by phase**, one PR per phase; keep the backend suite green. Everything inline &
CSP-safe (no CDN/library) — **charts = inline SVG/Canvas**. This is a **single-file SPA** — extend
`foxy-dashboard/foxy-audit-premium.html`.

## System primer (verified by recon)
- **FE:** single file (~2318 lines): one `<style>` (15–453), shell + all page markup (455–1069), then **8 inline
  `<script>` IIFEs**. Router `go(page,navEl)` (1186) is a pure view-switch; **per-page loaders are attached via a
  decorator chain** — each later IIFE does `const _go=window.go; window.go=(p,e)=>{ _go(p,e); if(p===…)loadX() }`
  (5 wrappers). Dashboard's own data loads once on auth via `loadAll()` (1555), not through `go()`. **Helpers are
  re-declared per IIFE** (`$`, `api()`, `esc()`, `money()`, `showToast`); `revealSecret(title,value)` (1677) is the
  one-time-secret modal. **No chart library, no shared chart helper** — all viz is CSS `.gtrack/.gfill` bars +
  `.spark` with per-callsite math. **No `[data-theme]`/skin system**; tokens are dark-clay only with `#000`
  borders/shadows hardcoded everywhere. **No tooltip system, no skeletons.** Command palette (Cmd/Ctrl-K) exists
  (1090). Login gate `#authGate` (1129) is session-cookie only; localStorage used only for `foxy_breach_seen`.
- **BE:** customer FastAPI (`/v1/*`, cookie `session`). Guards in `backend/app/auth.py`: **`require_user`**
  (session), **`require_role("admin")`**, **`resolve_org`** (accepts Bearer key OR session — used by most reads),
  **`require_org`** (Bearer-only ingest). **CSRF** = double-submit (`middleware/csrf.py`, cookie `foxy_csrf` →
  `X-CSRF-Token`); logged-in state changes are protected; login/mfa/forgot/reset/handoff/google are exempt.
  Session is a **purely signed cookie, 12h fixed** (`config.py:54`), **no server-side store, no remember-me, no
  per-session revocation**. **MFA = email-OTP** (6-digit, 5-min, `mfa.py`), NOT TOTP. The **email-code step-up
  pattern already exists** for key regen: `POST /v1/keys/regenerate/request` + `/confirm` (`keys.py:203-249`,
  reuses `mfa.new_otp/hash_code/code_valid/clear_code`) — the template to generalize.
- **DB:** migration head **0041**. RLS pattern for per-org tables = `ENABLE + FORCE ROW LEVEL SECURITY` +
  `CREATE POLICY org_isolation … USING/WITH CHECK (org_id = current_setting('app.current_org',true)::uuid)`.

## Non-negotiable rules
- **No fake/placeholder data — ever.** Every tile/chart/table wired to a real `/v1` endpoint; honest empty states
  ("No … yet"). Charts with **no data source today** either get a **new aggregation endpoint** or show an honest
  empty state — never a fabricated series. (`clean_rate` can be `null` → render `—`.)
- **Reuse, don't rebuild:** gate reads with `resolve_org`/`require_user`, admin writes with `require_role("admin")`;
  audit customer actions via the existing **`account_actions`** trail; reuse `revealSecret`, the CSRF monkey-patch,
  `mfa.new_otp/hash_code/code_valid` for step-up codes, the `usage_daily` rollup and `/v1/usage` (90-day series).
  New per-org tables follow the **org_isolation RLS** pattern.
- **Never expose secrets** (keys shown-once via `revealSecret`; never serialize `password_hash`/`key_hash`/
  `mfa_code_hash`/session `token_hash`).
- **Charts = inline SVG/Canvas** (CSP blocks CDNs). **Themes in scope** — first tokenize the shell (colors +
  borders/shadows/blur + font into a token contract, keeping the dark palette byte-for-byte), then add **2 themes
  (light + dark) × 3 skins (Original + Skeuomorphism + Liquid Glass) = 6 combos**. Dark = the existing perfect
  palette; light = a new palette; the **font is uniform Poppins + JetBrains Mono across all 6** (embedded woff2);
  skins remap surface treatment only, never colors or font.
- **Not "vibe-coded":** design on an explicit grid + spacing scale; one clear primary element per page; vary card
  size/density by importance; tables first-class (compact, tabular-nums, right-aligned numerics, sticky headers);
  ONE reused button/chip/table/card/chart system across every page; elevation signals importance. Use
  ui-ux-pro-max `layout`/`ux`/`product`.
- FE: extend the single file; add new pages as `#page-x` markup + a dock item + a `_go` wrapper loader. Replace
  ad-hoc bar math with the shared chart helper. Accessibility per ui-ux-pro-max (contrast ≥4.5:1, keyboard nav,
  aria, focus rings, 44px targets, reduced-motion).

---

## Phased build — granular, one branch/PR each

### P0 — Foundations (FE infra; mostly no BE)
Shared systems every later phase reuses.
- **Shared inline-SVG chart helper** `chart(el,{type,series,…})` — line/area/bar/stacked/donut/sparkline/gauge,
  reads CSS tokens, legend + hover tooltip, non-color-only encoding, reserved height, reduced-motion + draw-in.
  Replaces the per-callsite `.gtrack/.spark` math (loadStats 1367, loadThreats 2231, loadBilling 2052).
- **Skeleton shimmer** component (`.skel`) shown by every loader while fetching (replaces "loading…").
- **Delayed hover-tooltip system** — `data-tip="…"`, ~450ms delay, positioned, dismiss on blur/scroll/Escape,
  keyboard/focus accessible. Apply to dock/top-bar buttons, KPIs, sensitive fields, chart points.
- **Sensitive mask/reveal + copy** control — masked value + reveal + copy; org id, chain head, anchor roots;
  masked-by-default when the `hide_sensitive_metadata` pref (P14) is on.
- **Dynamic avatar** — derive the initial from `full_name` (once P14 lands) else the email localpart.
- **Tokenize the shell** — turn the hardcoded `#000` borders + hard `--clay*` shadows into a **token contract**
  (`--border`, `--surf*`, `--shadow*`, `--blur/rim`) **keeping every existing color byte-for-byte**, so P2 adds
  skins with no recolor. **Spacing scale** (`--s-1..--s-8`) at grid gaps / rhythm / padding.
- **Command-palette upgrade** (keep Cmd/Ctrl-K) + unified **status-badge / empty-state** components.

### P1 — App shell: top bar + dock upgrade *(NEW)*
The dashboard has only a left `.dock` (brand, nav, a hardcoded "F" chip, injected logout) + a Threats breach pip;
**no top bar**. Add a persistent **top bar** above main (shell = dock + top bar + main), token-driven, responsive,
mirroring the admin `.topbar`/`.topuser` (`foxy-adminpage/index.html` ~L889-919):
- **Left:** current-page context (section title / light breadcrumb); Foxy wordmark on small screens.
- **Right cluster:** a visible **search / Cmd-K palette** trigger (surfaces the existing palette IIFE ~L1090); a
  **notifications bell** + unread pip (data wired in P16); the **breach/threat indicator** moved/mirrored from the
  nav (`loadBreachBadge` → `/v1/logs/breaches`) so it shows on every page; a **theme toggle** (light/dark) + a
  **skin picker** (Original / Skeuomorphism / Liquid Glass — 3-way), both activated by P2; and the **user chip** —
  dynamic avatar initial + name + plan/role opening a menu (Settings,
  Devices, Log out), replacing the static "F".
- **Announcement / status banner** beneath the bar — **dismissible**, real signals only (active announcements,
  trial-ending / quota-warning from `/v1/billing/plan`, breach alerts; feed joined in P16), dismissal persisted to
  localStorage. Mirrors the admin `#annBanner`.
- **Responsive:** top bar stays; dock collapses to the bottom nav; cluster condenses to icons; no horizontal
  scroll. Delayed tooltips (P0) on the icon buttons; reduced-motion. FE-only.

### P2 — Theming: 2 themes × 3 skins = 6 combos (incl. the original) *(NEW)*
Full theme system, orthogonal & token-driven like the admin (`docs/ADMIN_LIQUID_GLASS_REDESIGN.md`) — but the
dashboard keeps its **Original** style as a first-class skin, so it's **2 themes (light + dark) × 3 skins
(original + Skeuomorphism + Liquid Glass) = 6 combinations**. **Bake in the admin's shipped corrections from day
one.**
- **Two axes on `<html>`, orthogonal:** `data-theme="light"|"dark"` (color palette) × `data-skin="original"|"clay"
  |"glass"` (surface *treatment* only — remaps elevation/blur/rim/specular, never the theme colors and never the
  font). No component special-cases a theme or skin — everything reads tokens.
- **Themes:** **Dark = the dashboard's existing palette, KEPT byte-for-byte** (the owner's dark colors are
  perfect). **Light = NEW** — derive a light palette (light surfaces, dark text, fox-orange brand + semantics
  retuned), validated ≥4.5:1 (mirror the admin's light `:root`). This is the only *new color* work; skins add no
  color.
- **Font — UNIFORM across all 6 combos (not per-skin):** replace the dashboard's Unbounded/Space Mono with the
  **admin's Poppins + JetBrains Mono** pairing everywhere. Embed **Poppins (400/500/600/700) + JetBrains Mono** as
  self-hosted woff2 (reuse the admin's already-embedded faces — CSP-safe, no CDN); set the global `--disp`/`--mono`
  tokens once (the skin/theme never changes them). Drop the Google Fonts link.
- **Skins (surface *treatment* only, same font throughout):**
  - **`original`** (default) = the dashboard's **current surface look preserved** — its claymorphism shadows — now
    rendered in Poppins like the rest. Nothing else regresses for existing users.
  - **`clay` (Skeuomorphism)** = a refined tactile treatment (layered bevel, glossy top highlight, matte, genuine
    press).
  - **`glass` (Liquid Glass)** = frosted `backdrop-filter`, specular edge, one inline `#foxGlassRefract` SVG filter
    (**static, seeded — NEVER animated**), `@supports` gate + clean frost fallback, crisp data-tier (no `url()` on
    tables). **Corrections baked in:** **NO moving background** (no drifting `liquidSheen`), calm/no ambient drift
    — a **still** glass surface; no over-hot active nav.
- **Pickers:** a **theme toggle** (light/dark) AND a **skin picker** (original / Skeuomorphism / Liquid Glass — a
  3-way segmented control, not a 2-state toggle) in the P1 top bar + P14 Settings; persist `foxy_dash_theme` +
  `foxy_dash_skin`; **default theme from `prefers-color-scheme`, default skin = `original`**; apply **pre-paint**
  (no FOUC). Verify all **6 combos** (light/dark × original/clay/glass) render cleanly across **every** page;
  contrast ≥4.5:1 in both themes; reduced-motion.

### P3 — Login + persistent DB device sessions
**DB (migration `user_sessions`):** `id, user_id, org_id, token_hash, user_agent, ip, created_at, last_seen_at,
expires_at, revoked_at` (org_isolation RLS).
**BE:** `remember_me` on `LoginRequest` (`auth_human.py`) → mint a `user_sessions` row + set cookie max-age (30d
vs 12h); validate/refresh + reject revoked sessions in `require_user` (`auth.py`); `GET /v1/auth/sessions`,
`POST …/{id}/revoke`, `POST /v1/auth/logout-all`.
**FE:** rebuild `#authGate` — cleaner centered card, better inputs + inline validation + feedback states,
**"remember me"** checkbox, subtle background; keep Google/SSO/MFA/forgot/reset.

### P4 — Onboarding stepper
**DB (migration `onboarding_state`):** `users.onboarding_state JSONB` (persist checklist/dismissal).
**BE:** `GET/PUT /v1/onboarding` (state; checklist otherwise computed from keys/stats/users as in `loadFirstRun`).
**FE:** rebuild "Get set up" (`#firstRun`) into an **inviting stepper** — progress tracker, guidance cards, clear
CTAs, celebratory done-state; persist dismissal.

### P5 — Home dashboard (charts)
**BE:** reuse `GET /v1/usage` (90-day logs/tokens/breaches + quota), `GET /v1/stats` (tiles, grading donut,
7-day activity). **FE:** hero usage/breaches trend (area/line + metric switcher) + KPI sparklines/deltas +
grading-status donut. All via the P0 chart helper.

### P6 — Capture coverage (charts)
**BE:** `GET /v1/coverage`; add `GET /v1/coverage/timeseries` (coverage %/day) **or** an honest empty state until
a daily rollup exists. **FE:** coverage-% gauge, per-client continuity table, gap indicators, coverage-over-time
line.

### P7 — Threats (charts, depth)
**BE:** `GET /v1/analytics/timeseries` (breaches/day × risk band — new; analytics groups only by policy_tag today)
+ `GET /v1/analytics/by-agent`; reuse `top_policies`, `recent_high_risk`, `/v1/logs/breaches`. **FE:** severity/
timeline charts, top-policies bar, avg-risk gauge, recent-high-risk table, filters, alert indicators — kill the
"dead" feel.

### P8 — Ledger (charts, depth)
**FE (reuse `/v1/logs` filterable + `usage_daily`):** volume-over-time chart, summary stats, richer filters/search,
expandable rows, verdict distribution.

### P9 — Verify
**FE:** guided step flow (paste hash → check → status/feedback), clear status indicators; whole-ledger verify +
anchor actions (exist).

### P10 — Policy
**FE:** sectioned/collapsible layout, search within policies, severity chips, clearer save feedback (reuse
`GET/PUT /v1/policies`).

### P11 — Export (history/jobs)
**DB (migration `export_jobs`):** `org_id, requested_by, type, params JSONB, status, file_ref, created_at,
completed_at` (org_isolation RLS). **BE:** `POST /v1/exports` (queue), `GET /v1/exports` (history), `GET
/v1/exports/{id}` — reuse `/v1/logs/export` + `/v1/passport` producers (sync is fine, but record a job row).
**FE:** export history table + progress, file-type options, chain-metadata card with P0 mask/reveal.

### P12 — Access (API keys made prominent)
**FE (reuse `GET /v1/keys` + `last_used_at`):** restructure so **API-key management is the primary element**
(today "+ new key" is buried top-right); key table with status/last-used, SDK connect guide, key-usage context.

### P13 — Billing (graphs)
**FE (reuse `/v1/usage.days`, `/v1/invoices`, `/v1/billing/plan`):** usage trend, quota gauge, invoice/cost-over-
time bar, plan summary/comparison. **Honest note:** only invoice totals exist — no fabricated line-item breakdown.

### P14 — Settings rebuild (+ identity & prefs)
**DB (migration `users.full_name` + `users.preferences JSONB`):** `full_name` (nullable; identity defaults to
email, uses the name once set); `preferences` (`hide_sensitive_metadata`, notification prefs). **BE:** `PUT
/v1/account/profile` (name) + `GET/PUT /v1/account/preferences`; extend `/v1/auth/me`. **FE:** de-cramp into
grouped, **icon-headed** sections (Account & identity / Security & 2FA / Access control / Notifications / Devices &
sessions / Data & privacy / Danger zone / Team & audit (admin)); a **name** field feeds the avatar/greeting; the
**hide-sensitive-metadata** toggle drives P0 mask/reveal; **wire the skin picker** here too; the Devices section
lists `user_sessions` (P3) with revoke. Settings now has its **own dock icon** + top-bar menu entry.

### P15 — Security step-up (email code on EVERY sensitive action)
**DB (migration `verification_codes`):** `user_id, purpose, code_hash, expires_at, consumed_at` (generalizes the
key-regen OTP). **BE:** generic `POST /v1/auth/step-up/request` + `/confirm` (emails a code, verifies, mints a
short-lived **grant** so a burst doesn't re-prompt per click) + a `require_step_up` dep gating **every** sensitive
write: change-password, disable-2FA, IP-allowlist, delete-workspace, key revoke/regen (exists), plan
cancel/downgrade, admin role add/remove, badge revoke. Reuse `mfa.new_otp/hash_code/code_valid`; audit via
`account_actions`. **FE:** a reusable step-up modal wrapping the danger flows.

### P16 — Notifications center
**DB (migration `notifications`):** `org_id, user_id?, kind, title, body, read_at, created_at` (org_isolation
RLS). **BE:** generate from **real events**; `GET /v1/notifications` (+unread), `POST …/{id}/read`, `/read-all`.
**FE:** wire the **P1 top-bar bell** (unread pip, list, mark-read) + the announcement/status **banner feed**. Real
data only; honest empty state.

### P17 — Branded transactional emails (backend; all 14; no DB)
All 14 emails are bare inline f-strings through one helper `send_email(*, to, subject, html, text=None)`
(`backend/app/email.py:21`, Brevo API, from `no-reply@foxyaudit.tech`, dev no-op). Make them a professional
Foxy-branded system. **Locked design: light body + dark branded header. Brand all 14; the 3 internal ops alerts
get a compact variant.**
- **Shared render helper** (new `backend/app/email_templates.py`; keep the `send_email` keyword signature stable —
  ~11 tests monkeypatch it): `layout(*, title, preheader, blocks, cta=None, surface="customer"|"staff",
  variant="full"|"compact")` → `(html, text)`. Dark fox-orange (`#ff7a2e`) header band (logo + wordmark) → white
  body → footer (company, link, legal). **Table-based, inline-styled, ~600px, Gmail/Outlook-safe**, bulletproof
  VML button, hidden **preheader**, matching **plaintext** every time. Components: `heading/paragraph/code_block`
  (big OTP)/`button`/`callout`/`info_rows`/`divider`/`footer`.
- **Escape ALL user/staff content** (inbox-reply, broadcast, lead body) — preserve `leads.py`/`worker.py`
  escaping. **Logo:** hosted `https://foxyaudit.tech/logo.png` (Caddy TLS) + wordmark/alt fallback; **not a
  `data:` URI** (Gmail strips those); optional CID inline via a `send_email(inline_images=)` enhancement.
- **Full template:** customer sign-in code (`mfa.py:37`), reset + invite/set-password (`password_reset.py:48-62` —
  **split** customer/staff copy + `dashboard_url` vs `admin_url`), key-regen (`keys.py:223`), breach
  (`worker.py:214`), contact confirm (`leads.py:99`), support reply (`admin_inbox.py:173`), staff sign-in code
  (`auth_staff.py:79` — **distinct subject**), staff invite/reset (`admin_staff.py:110`/`auth_staff.py:168`),
  broadcast (`admin_config.py:114`), checkout welcome (`billing.py:98`,`:318`). **Compact variant:** grading
  dead-letter (`usage.py:151`), anchor-health (`anchor.py:359`), priority-lead (`leads.py:96`). Disambiguate the
  shared subjects; keep all real dynamic content; **no fake data**.
- **Deliverability:** optional `send_email(..., reply_to=)` → `support@foxyaudit.tech` on the human-reply emails.
- **Tests:** keep the signature so the ~11 monkeypatch tests pass (adjust only disambiguated-subject asserts); add
  tests for code/link presence + HTML-escaping of injected content + non-empty plaintext; add a dev render script
  writing all 14 to `.html` to eyeball in a browser + real Gmail/Outlook. (Backend surfaces beyond the dashboard,
  but done here since all 14 share the one helper.)

### P18 — Global liveliness & responsive polish
**Global activity feed** (reuse `/v1/account/audit` + `login_events`); **live indicators/status badges**;
**keyboard-shortcuts** help overlay (extend the palette); **responsive** breakpoints (dock → drawer, tables →
stacked cards, no horizontal scroll); skeletons + empty-state illustrations everywhere; motion 150–300ms behind
`prefers-reduced-motion`.

---

## New backend surface (summary)
- **Auth/sessions:** `remember_me` on login; `GET /v1/auth/sessions`, `POST …/{id}/revoke`, `POST
  /v1/auth/logout-all`; session validation/revocation in `require_user`.
- **Step-up:** generic `POST /v1/auth/step-up/request` + `/confirm` (email code) gating every sensitive write.
- **Profile/prefs:** `PUT /v1/account/profile` (full_name), `GET/PUT /v1/account/preferences`.
- **Onboarding:** `GET/PUT /v1/onboarding`.
- **Notifications:** `GET /v1/notifications`, `POST …/{id}/read`, `POST …/read-all`.
- **Exports:** `POST /v1/exports`, `GET /v1/exports`, `GET /v1/exports/{id}`.
- **Analytics aggregations (new):** `GET /v1/analytics/timeseries` (breaches/day × risk band), `GET
  /v1/analytics/by-agent`, `GET /v1/coverage/timeseries` — each real or an honest empty state.
- Register nothing new in `main.py` beyond routers you add; most extend existing routers (`auth_human`, `account`,
  `analytics`, `coverage`, `keys`).

## New DB migrations (numbered after the current head; per-org tables follow org_isolation RLS)
One per data-owning phase, in order: `user_sessions` (P3) · `users.onboarding_state` (P4) · `export_jobs` (P11) ·
`users.full_name + users.preferences` (P14) · `verification_codes` (P15) · `notifications` (P16). Check the live
head before numbering. No changes to existing tables beyond the two `users` columns. Optional: a materialized
`audit_logs.severity` only if the threat timeline can't read `gemini_verdict` JSONB performantly. **The theming
(P2, all 6 combos + fonts) and the emails (P17) need no DB.**

## Where to work
- **FE:** the single file `foxy-dashboard/foxy-audit-premium.html` — new/rebuilt `#page-*` markup, dock items + `_go`
  loader wrappers, the shared chart/skeleton/tooltip helpers, mask/reveal, the auth gate, Settings.
- **BE:** `backend/app/routers/{auth_human,account,analytics,coverage,keys}.py` (extend) + any new router you add
  (register in `main.py:113`), `backend/app/auth.py` (session validation), `backend/app/mfa.py` (reuse), one or
  more Alembic migrations, reuse `account_actions`/`email.py`.
- **Do NOT touch:** the admin console (`foxy-adminpage/`, `admin_*` routers), the customer `/v1` ingest contract
  (`POST /v1/logs/batch`), or the marketing site (`foxy-sale-page/`).

## Testing
- **Backend** (mirror `backend/tests/integration/test_*`): per new endpoint — auth-gating (session vs Bearer vs
  anon), org isolation/RLS, an `account_actions`/audit row on each mutation, step-up code required + verified, the
  effect is correct, **no secret leaks** (session `token_hash`, `mfa_code_hash`, `key_hash` never returned). Keep
  the suite green: `cd backend && DATABASE_URL=postgresql+psycopg://foxy:foxy@localhost:5433/foxy
  API_KEY_PEPPER=testpepper .venv/Scripts/python.exe -m pytest tests/integration -q` (do NOT export
  SESSION_SECRET/STAFF_SESSION_SECRET — conftest sets them).
- **FE:** `node --check` every inline `<script>` block. Manual smoke as a seeded admin: walk every page, confirm
  charts render from real data or show honest empty states, remember-me survives a browser restart, device revoke
  works, every danger action demands the email code, mask/reveal + dynamic avatar work, no CSP violations.
- **Not-vibe-coded gate:** each page reads as "a designer laid it out on a grid" — screenshot every page.

## Branch & merge — ALWAYS branch off (and rebase onto) the LATEST origin/main
The phases stack: each one builds on everything already merged (earlier phases **and** any hotfixes). So:
1. **Before starting a phase:** `git fetch origin` then branch off the **freshest** `origin/main` —
   `git checkout -b feat/dash-<phase> origin/main`. **Never** branch off a stale local `main`, a sibling feature
   branch, or an old base. (Phases share the same single file — a stale base silently reverts merged work.)
2. **Before finishing / handing off:** `git fetch origin` again and **`git rebase origin/main`** your branch;
   resolve conflicts so you **PRESERVE already-merged work** — never re-introduce something a prior fix removed
   (e.g. don't bring back a removed animation). Then confirm it's fast-forwardable:
   `git merge-base --is-ancestor origin/main HEAD` must succeed, and spot-check the file still contains the
   **prior phases' features** (grep for a marker from the last phase).
3. One phase per branch; commit only the files that phase touches (diff stays in the customer surface — P17 emails
   also touch shared `backend/app/email*` + staff call sites, which is expected). Tests with every endpoint; suite
   green; PR to `main`. On merge CI/CD redeploys `app.foxyaudit.tech/dashboard` — hard-refresh. (SSH may be needed
   for push.) Branch names: `feat/dash-foundations`, `feat/dash-topbar`, `feat/dash-skins`, `feat/dash-auth-
   sessions`, …, `feat/branded-emails`.

---

## Paste-ready prompt for the executing Claude
```
You're in the Foxy Audit repo. Redesign the CUSTOMER DASHBOARD (the single file
foxy-dashboard/foxy-audit-premium.html + the /v1/* API) following docs/DASHBOARD_REDESIGN.md — read that file
first; it's the source of truth (system primer, guards/CSRF, the phased build, new endpoints/tables, testing,
branch/merge). This is CUSTOMER-surface only: do NOT touch the admin console (foxy-adminpage/, admin_* routers),
the /v1 ingest contract (POST /v1/logs/batch), or the marketing site (foxy-sale-page/).

FIRST ACTION: Skill: ui-ux-pro-max — keep it in the loop for EVERY page (query product, style, ux, layout,
typography, color, chart). Stack = plain HTML/CSS/vanilla JS; apply its guidance conceptually with inline styles +
the existing CSS vars. You have real repo access — READ the code before writing (the single HTML file;
backend/app/routers/auth_human.py, account.py, analytics.py, coverage.py, keys.py, mfa.py; backend/app/auth.py
guards; backend/app/models.py; main.py). Never guess an endpoint, table, or helper.

LOCKED DECISIONS: (1) persistent login = DB-backed device sessions (remember-me + active-devices list +
log-out-everywhere). (2) EVERY sensitive action requires an emailed one-time code — generalize the existing
keys/regenerate/{request,confirm} step-up template (reuses mfa.new_otp/hash_code/code_valid). (3) add users.full_name
— identity defaults to email, uses the name once set (avatar initial + greeting). (4) backend-heavy, all-in: new
tables + aggregation endpoints, phased, one PR per phase. (5) ADD A TOP BAR like the admin console's (search/Cmd-K
trigger, notifications bell, breach pip, skin toggle, user-chip menu + a dismissible announcement banner). (6)
FULL THEME MATRIX IS IN SCOPE (not deferred): 2 THEMES (light + dark) × 3 SKINS (Original + Skeuomorphism + Liquid
Glass) = 6 COMBOS, token-driven + switchable, reusing the admin technique in docs/ADMIN_LIQUID_GLASS_REDESIGN.md AND
its shipped corrections (static seeded refraction — NEVER animate; NO moving background/drift; no over-hot active
nav). KEEP the existing DARK palette byte-for-byte (it's perfect) and ADD a NEW LIGHT palette (light surfaces/dark
text, brand+semantics retuned, contrast ≥4.5:1). KEEP the ORIGINAL dashboard surface look as a first-class skin
(default). THE FONT IS UNIFORM ACROSS ALL 6 COMBOS: replace Unbounded/Space Mono with the admin's Poppins +
JetBrains Mono pairing everywhere (embed self-hosted woff2, reuse the admin faces, CSP-safe, drop the Google Fonts
link) — set global --disp/--mono once; skin/theme never change the font. data-theme (light/dark) × data-skin
(original/clay/glass) on <html>, orthogonal (skins remap surface treatment ONLY — never colors, never font); a
theme toggle + a 3-way skin picker in the top bar + Settings; default theme from prefers-color-scheme, default skin
= original; persist foxy_dash_theme + foxy_dash_skin; apply pre-paint. First tokenize the shell (colors + #000
borders/shadows + font) into a token contract keeping the dark palette unchanged, THEN add the light palette + the
Poppins font + the two new skins. Verify all 6 combos on every page. (7) BRAND ALL 14
TRANSACTIONAL EMAILS (light body + dark header) via one shared render helper — keep the send_email(*, to, subject,
html, text) signature stable, escape all user/staff content, hosted logo (not a data: URI), ops alerts get a
compact variant, real data only.

HARD RULES:
- NO fake/placeholder data — ever. Every tile/chart/table wired to a real /v1 endpoint or an HONEST empty state.
  Charts with no data source today get a new aggregation endpoint OR show an empty state — never a fabricated
  series (e.g. billing has invoice totals only, no line-item breakdown — don't invent one).
- Reuse, don't rebuild: gate reads with resolve_org/require_user, admin writes with require_role("admin"); audit
  mutations via account_actions; reuse revealSecret, the CSRF fetch monkey-patch, usage_daily/GET /v1/usage (90d),
  the email-OTP helpers. New per-org tables follow the org_isolation RLS pattern (ENABLE+FORCE + policy on
  current_setting('app.current_org')). Never serialize a secret (session token_hash, mfa_code_hash, key_hash,
  password_hash).
- Charts = inline SVG/Canvas only (CSP blocks CDNs). Build ONE shared chart helper (line/area/bar/stacked/donut/
  sparkline/gauge) + ONE skeleton system + ONE delayed-tooltip system + ONE mask/reveal control, and reuse them
  everywhere. Replace the ad-hoc CSS-bar math.
- Layout must NOT look "vibe-coded": explicit grid + spacing scale, one clear primary element per page, tables
  first-class (compact/tabular-nums/right-aligned/sticky headers), ONE reused button/chip/table/card system,
  elevation signals importance. Accessibility per ui-ux-pro-max (contrast ≥4.5:1, keyboard, aria, focus rings,
  44px targets, reduced-motion).

Build PHASE BY PHASE from the doc (P0–P18, one branch/PR each): P0 Foundations (chart/skeleton/tooltip/mask-reveal
helpers, tokenize the shell keeping colors, spacing scale, dynamic avatar, palette upgrade); P1 App shell (top bar
+ dock upgrade); P2 Theming (Liquid Glass + Skeuomorphism skins, colors unchanged); P3 Login + DB device sessions;
P4 Onboarding stepper; P5 Home charts; P6 Capture coverage; P7 Threats; P8 Ledger; P9 Verify; P10 Policy; P11
Export history/jobs; P12 Access (API-key mgmt prominent); P13 Billing graphs; P14 Settings rebuild (+ full_name /
preferences / hide-sensitive-metadata / skin picker); P15 Security step-up (email code on every sensitive action);
P16 Notifications center (+ wire the top-bar bell & banner); P17 Branded transactional emails (all 14); P18 global
liveliness + responsive polish. Do ONE phase per branch/PR; ask me before the next.

VERIFY (don't just claim it): backend tests per new endpoint (auth-gating session/Bearer/anon, org isolation, an
audit row per mutation, step-up code required+verified, correct effect, no secret leaks) — mirror
tests/integration/test_*; keep the suite green: cd backend && DATABASE_URL=postgresql+psycopg://foxy:foxy@localhost:5433/foxy
API_KEY_PEPPER=testpepper .venv/Scripts/python.exe -m pytest tests/integration -q  (do NOT export
SESSION_SECRET/STAFF_SESSION_SECRET). node --check every inline <script> block. Run locally as a seeded admin and
screenshot every page (charts real or honest-empty; remember-me survives restart; device revoke works; every
danger action demands the email code; mask/reveal + dynamic avatar work; zero CSP violations) before the PR.

WORKFLOW (critical — phases stack on one single file): ALWAYS `git fetch origin` and branch each phase off the
LATEST origin/main (`git checkout -b feat/dash-<phase> origin/main`) — NEVER off a stale local main or a sibling
branch. Before finishing a phase, `git fetch origin` again and `git rebase origin/main`, resolving conflicts so you
PRESERVE already-merged work (never revert a prior fix, e.g. don't re-add a removed animation); confirm
`git merge-base --is-ancestor origin/main HEAD` succeeds and the file still has the prior phases' features. One
phase per branch, tests with every endpoint, suite green, PR to main. SSH may be needed for push. Start by reading
docs/DASHBOARD_REDESIGN.md + the files above, then invoke ui-ux-pro-max and give me your P0+P1 plan (the shared FE
helpers + the top bar + the auth/session/onboarding endpoints and DB migrations) before coding.
```

---

## Autonomous overnight run (hands-off pipeline)
Two agents coordinate through GitHub, no human in the loop:
- **Builder** (a fresh chat): builds phases **P0→P18 in order**, one at a time, and pushes each to a branch named
  **`feat/dash-p<N>-<slug>`** (e.g. `feat/dash-p0-foundations`). It does NOT touch `main`. After pushing, it polls
  `origin/main` until its phase is merged (deployed), then starts the next phase. Never waits for human approval.
- **Committer** (this chat): polls GitHub for new **`feat/dash-*`** branches (ONLY that namespace — everything
  else is ignored), verifies each with the full checklist, fast-forwards `main`, pushes, confirms the CD deploy,
  and repeats. **On ANY failure it STOPS** and leaves `main` at the last-good phase.
- **Coordination signal:** GitHub. Builder pushes `feat/dash-*` → committer detects (poll ~every few min) →
  merges to `main` → builder detects `main` advanced (its commit is now an ancestor) → builds the next phase off
  the fresh `main`.

**Committer verification (must pass before merge — same rigor as the manual runs):** branch is `feat/dash-*` and
FF-safe over `origin/main` (if not FF-safe, attempt a rebase; **if the rebase conflicts, STOP** — never
hand-guess conflicts unattended); diff stays in the customer surface (+ shared `email*`/staff call sites for P17);
**no fake data**; **no secret serialization**; `node --check` every inline `<script>`; single alembic head; and
for any backend phase the **full integration suite is green**. Merge only if ALL pass; else STOP + report.

**Safety net:** a backup branch **`backup/pre-overnight`** is pinned at the pre-run `main`
(`a727b41`). Every phase is a separate FF commit, so `main` can be reset to any good point. Rollback the whole
night: `git branch -f main backup/pre-overnight && git push --force-with-lease origin main` (then redeploy).
**STOP-on-failure** means the worst case is a partial-but-clean `main`, never a broken one.

## Autonomous builder prompt (paste into the NEW chat)
```
AUTONOMOUS OVERNIGHT MODE. Build the Foxy CUSTOMER DASHBOARD per docs/DASHBOARD_REDESIGN.md — phases P0→P18 IN
ORDER, WITHOUT waiting for human approval between phases. A separate "committer" agent watches GitHub, merges each
phase to main, and deploys; your job is build → push → wait for the merge → next phase. Read
docs/DASHBOARD_REDESIGN.md first (it's the source of truth). FIRST ACTION: Skill: ui-ux-pro-max. Customer surface
only (never the admin console / marketing site).

PER PHASE, exactly:
1. git fetch origin && git checkout -b feat/dash-p<N>-<slug> origin/main   — ALWAYS branch off the FRESHEST
   origin/main (the committer keeps advancing it). The branch name MUST start with feat/dash-p<N>- (e.g.
   feat/dash-p0-foundations); the committer ONLY merges feat/dash-* branches.
2. Build ONLY that phase per the doc. HARD RULES: no fake data (real /v1 endpoint or honest empty state); reuse
   guards (resolve_org/require_user/require_role) + account_actions audit + mfa/email helpers; never serialize a
   secret; charts = inline SVG; keep the send_email(*, to, subject, html, text) signature + escape all user/staff
   content (P17); not "vibe-coded". Theming (P2) = 6 combos (light/dark × original/skeuo/glass), UNIFORM Poppins +
   JetBrains Mono, keep the dark palette byte-for-byte, admin corrections baked in (static seeded refraction —
   NEVER animate; no moving background; no over-hot active nav; and DISABLE the url() refraction lens like the
   admin's latest polish so there's no ghost overlay).
3. VERIFY LOCALLY before pushing: node --check every inline <script>; for backend phases run the FULL suite green
   (cd backend && DATABASE_URL=postgresql+psycopg://foxy:foxy@localhost:5433/foxy API_KEY_PEPPER=testpepper
   .venv/Scripts/python.exe -m pytest tests/integration -q — do NOT export SESSION_SECRET/STAFF_SESSION_SECRET);
   single alembic head; and grep the file to confirm PRIOR phases' features are still present.
4. git fetch origin && git rebase origin/main — PRESERVE already-merged work, NEVER revert a prior fix. If a
   rebase CONFLICTS and you can't cleanly keep both sides, STOP and summarize (don't guess). Confirm
   git merge-base --is-ancestor origin/main HEAD succeeds.
5. git push -u origin feat/dash-p<N>-<slug>. Do NOT touch main — the committer merges it.
6. WAIT for merge: every ~4 min, git fetch origin and test `git merge-base --is-ancestor HEAD origin/main`. TRUE =
   merged + deployed → go to step 1 for phase N+1. If main hasn't advanced in ~30 min, the committer likely
   STOPPED on a problem — STOP and summarize; do not stack more phases on a stuck main.

STOP (summarize, don't force) on: an unresolvable rebase conflict; a suite that won't go green; or main not
advancing for >30 min. One phase per branch. SSH may be needed for push. Start: read docs/DASHBOARD_REDESIGN.md,
invoke ui-ux-pro-max, then build P0 and push feat/dash-p0-foundations.
```
