# Handoff — Foxy Audit customer dashboard UI/UX polish

**For a fresh chat with no history.** Read this top-to-bottom, then start.

## 0. First action
**Invoke the `ui-ux-pro-max` skill** (`Skill: ui-ux-pro-max`) — it's installed at `~/.claude/skills/ui-ux-pro-max/` and is a searchable UI/UX design database (84 styles, 192 palettes, 74 font pairings, 98 UX guidelines, motion + chart presets, per-stack rules). Follow its priority order **1→10 (Accessibility first)**. Its search script + `references/quick-reference.md` + `references/pro-rules.md` live inside the skill dir — invoke them by full path. Stack here is **HTML/CSS (vanilla, no framework)**, so use its `html-tailwind`/`html` guidance conceptually (we don't use Tailwind — plain inline styles + CSS vars).

## 1. Your mission
Review and **polish the UI/UX of the customer dashboard** — the single-file SPA:
```
foxy-dashboard/foxy-audit-premium.html   (~2,260 lines; HTML + inline <style> + several inline <script> IIFEs)
```
It's served by the backend at `/dashboard`. The **whole feature roadmap (P0–P3) is already built, merged, and deployed** — so this is a **visual/interaction quality pass, not new features**. The owner flagged the UI as rough (a first-run-checklist render bug was just fixed; expect more spacing/alignment/contrast/responsive rough edges).

**Focus areas** (sweep every dock section): spacing & alignment, visual hierarchy, dark-theme contrast/accessibility (WCAG 4.5:1), touch targets (44px), responsive/mobile (no horizontal scroll), empty/loading states, button consistency, motion (150–300ms, respect reduced-motion), and consistency of the claymorphism look.

## 2. What the dashboard is (so you don't break intent)
- **Theme:** dark, "claymorphism" — soft cards (`.clay`), warm near-black bg, orange accent (`--fox`). All colors are **CSS variables** (`--bg --surf --ink --muted --fox --fox2 --line --safe-bg --breach-bg …`) defined in `:root`. **Never hardcode hex in components — use the tokens.**
- **Layout:** left dock nav + main. **9 sections** (`data-page` / `go(page)`): `dashboard`, `analytics` (Threats), `ledger`, `verify`, `policy`, `export`, `keys`, `billing`, `settings`. There's also a login gate overlay, a toast, and a **Cmd/Ctrl-K command palette**.
- **Hard product rules (do NOT violate):**
  - **No fake/placeholder data — ever.** Honest empty states ("No … yet"). E.g. `clean_rate` can be `null` → render `—`, never `null%` or a made-up number.
  - **CSP blocks external CDNs/fonts/scripts** → all charts/icons must be **inline SVG/Canvas**; no `<script src=cdn>`, no web-font links, no emoji as UI icons (use inline SVG).
  - Content-blind story: the product stores only hashes/commitments + verdicts, never raw prompts/responses. Don't add UI implying otherwise.

## 3. Known landmines (learned the hard way)
- **`.btn` is full-width** (`display:block; width:100%`). Inside a flex row it collapses siblings — the first-run bug was exactly this. In any flex row, a `.btn` needs `flex:0 0 auto; width:auto; white-space:nowrap`.
- The file has **multiple IIFEs** and a **decorator-chained `window.go`** (each script block wraps the previous `go` to lazy-load its page). Per-page loaders: `loadStats/loadThreats/loadLogs/loadPolicy/loadKeys/loadBilling/loadSettings/loadWebhooks/loadSso/loadAccountAudit/loadAnchors/loadFirstRun`. Don't reorder blocks blindly.
- Helpers vary per IIFE: `$`, `api()` (CSRF-aware fetch), `esc()`, `money()`, `showToast()`/`toast`, `revealSecret()` (shown-once secrets). Reuse them; check they're in scope for the block you edit.

## 4. Verify your changes (there's no build step)
- **Syntax-check every inline `<script>` block** after editing — a syntax error kills all JS:
  ```bash
  # extract each <script>…</script> body to a file and:
  node --check <file>.js
  ```
- **Preview live:** start the backend and open `http://localhost:8000/dashboard`. It requires login, so seed a test admin (Postgres runs natively on **:5433**, db/user/pass `foxy`):
  ```bash
  cd backend
  # backend/.env already has DATABASE_URL(:5433)+SESSION_SECRET; migrations at head 0040
  .venv/Scripts/python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --log-level warning
  # seed an admin, then log in at /dashboard:
  DATABASE_URL=postgresql+psycopg://foxy:foxy@localhost:5433/foxy API_KEY_PEPPER=testpepper \
    .venv/Scripts/python.exe -c "import bcrypt,uuid;from app.db import SessionLocal;from app.models import Organization,User;d=SessionLocal();o=Organization(name='UI Test',api_key_hash='ui-'+uuid.uuid4().hex,plan_tier='free');d.add(o);d.flush();d.add(User(org_id=o.id,email='ui@test.local',password_hash=bcrypt.hashpw(b'UiTest#12345',bcrypt.gensalt()).decode(),role='admin'));d.commit();print('login: ui@test.local / UiTest#12345')"
  ```
  A fresh admin (0 keys/0 logs) shows the **first-run checklist** + honest empty states — good for reviewing those.
- The file serves from disk, so **just hard-refresh** to see edits (no restart needed for HTML/JS).

## 5. Git & deploy
- Branch off **`origin/main`** (default branch): `git checkout -b feat/dashboard-ui-polish origin/main`.
- **Only touch `foxy-dashboard/foxy-audit-premium.html`** for UI polish. Do **NOT** touch the admin console (`foxy-adminpage/`, `admin_*` routers) or the marketing site (`foxy-sale-page/`).
- Open a PR to `main`; on merge, CI+CD deploy to **`app.foxyaudit.tech/dashboard`**. Keep the backend integration suite green (`cd backend && DATABASE_URL=postgresql+psycopg://foxy:foxy@localhost:5433/foxy API_KEY_PEPPER=testpepper .venv/Scripts/python.exe -m pytest tests/integration -q`, baseline **~328 passed**) — **do NOT export `SESSION_SECRET`/`STAFF_SESSION_SECRET`**, conftest sets them via `setdefault` (overriding breaks `test_login_rotates_the_session`). But pure HTML/CSS/JS changes don't affect backend tests.
- Commit trailer: `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`.

## 6. State of the project (context)
- **P0–P3 shipped & deployed**: honest stats, anchor transparency, real threat analytics, public verify page, verify UX, first-run checklist · team mgmt + MFA self-enroll · ledger search/filter + breach drill · billing portal + current-plan + invoice PDF · passport date-range + desktop download · API-key expiry + SDK helper · breach notifications + unread pip · account audit log + GDPR export · outbound webhooks · command palette · data-handling panel · **enterprise SSO (OIDC)**.
- **SSO live flow was verified end-to-end** against real Auth0 (login → callback → JIT-provision → session). Note for local dev behind a corporate TLS proxy: `pip install truststore` + inject it, else Python's HTTPS to the IdP fails cert verification (env-only; prod VM has clean egress + `--proxy-headers`).
- **VM env:** nothing new required; optionally set `OPENAI_API_KEY` in `deploy/.env` to enable the OpenAI judge alongside Gemini. Migrations 0035–0040 auto-apply via the compose `migrate` service.
- Roadmap doc: `docs/DASHBOARD_ROADMAP.md`. Sibling admin spec: `docs/ADMIN_CONSOLE_ROADMAP.md`.

## 7. Suggested first steps for the new chat
1. `Skill: ui-ux-pro-max` and skim its priority table + `references/pro-rules.md` (the pre-delivery checklist).
2. Start the local backend + seed admin (§4), open `/dashboard`, click through all 9 sections + the login gate + command palette on both desktop and a narrow viewport.
3. Make a prioritized findings list (accessibility → touch → layout → typography/color → motion → forms → nav), then fix in one focused PR, screenshotting/verifying before merge.
