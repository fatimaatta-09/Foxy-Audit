# Foxy Desktop — Full Dashboard Parity + Fox Companion

**Plan of record** · 2026-07-26 · MAIN chat is the committer; executors build per this file.

> ## ✅ COMPLETE AND RELEASED — v1.2.0, 2026-07-28
>
> **Every phase D0 → D15 is built, merged, deployed and released.** `main` = `1968388`,
> tag `v1.2.0` (the desktop app's first ever release), **754 desktop tests**, all three
> platforms building green, SDK 1.2.0 on PyPI, all three installers on the v1.2.0 release
> page, with Windows + Linux also served under stable names at `foxyaudit.tech/download/`.
> All four owner-locked decisions in §2 are delivered.
>
> **D15a (the automatable half of the QA sweep) is DONE and merged** — `5c76750`,
> +51 tests (698 → 749): a WCAG contrast audit over the token palette, accessible-name
> and focus-order guards, reduced-motion and HiDPI checks. Every guard was re-broken in
> both directions to prove it bites.
>
> **D15b is merged too (`1968388`, 754 tests). What is left is the owner-only manual sweep (§12)** — a visual check across
> 3 OSes × auth modes, not a merge gate; the release did not wait on it.
>
> **Known gaps, none blocking:**
> - ~~macOS has no download route~~ **CLOSED** (`8125db7`): `release.yml` now publishes a
>   GitHub Release with all three builds + the wheel, and v1.2.0's page is live. Note the
>   macOS build is **unsigned and not notarized** — Gatekeeper blocks first launch until the
>   user right-clicks → Open. The release notes say so. Signing needs an Apple Developer ID.
> - **`check-version` validates only 2 of 3 version stamps.** `sdk/src/foxy_audit/__init__.py`
>   carries its own `__version__` and is unchecked, so a wheel can pass the gate with correct
>   metadata while reporting the previous version at runtime. Bumped by hand for 1.2.0.
> - **exit-127 in the desktop test suite: CLOSED as won't-fix.** Test-harness only; the shipped
>   app holds its QApplication for its whole life by construction. The intuitive fix (a
>   `conftest.py` owning the app) was built, measured, and **rejected — 5 crashes/10 runs
>   against 0/12 without it**, because the app's early death was accidentally cleaning up stale
>   workers. Do not reopen. Details in the 2026-07-28 devlog.
> - **a11y, fixed** (`f9b3cb3`): `#ctaBtn`/`#verifyBtn` painted white on `#c96a2f` — 3.76:1
>   resting, 3.27:1 hovered — on the app's most-clicked controls. The one contrast finding the
>   "web wins" rule could not absorb, because `#c96a2f` appears nowhere in the site's `:root`;
>   it is the desktop's own matte accent. Took the web's own answer (`#1a0900` ink, as
>   `.btn.pri` uses), which moved desktop *toward* the web. Pressed moved `#a8551f`→`#c06529`
>   because dark ink fails on the darker shade — fixing one state alone would have swapped the
>   failure to the other. `QMenu::item:selected` had the identical miss, found by checking
>   siblings. `min_btn`/`close_btn` gained accessible names.
> - **D15b — the five reported findings are CLOSED** (`1968388`, 754 tests). The white-on-
>   `#c96a2f` family turned out to be **six** sites, not one: `#newChatBtn:hover` plus four in
>   `settings_dialog` (`#testBtn:hover`, `#foxyTestBtn:hover`, the tab `:checked` pill,
>   `#saveBtn`). Two were invisible to a `#ffffff` grep because the ink came from
>   `is_dark(acc, 140)` — a picker that classifies `#c96a2f` as "dark" and returns white.
>   `matte_tokens()` hard-codes that accent, so the branch was dead flexibility and is gone.
>   All six now use `#1a0900` (3.76 → 5.16:1). `tilePink`'s light stop `#c25c88` → `#bd4f7e`
>   (4.05 → 4.58:1); its dark stop and both tileBlue stops were measured and correctly left.
>   Spot-check fields named, four icons rasterise at the display scale, reduced motion wired.
> - **Reduced motion, product call (confirmed):** roaming stops — the one large-amplitude,
>   unprompted movement the app makes — while the sprite keeps ticking in place and settles to
>   IDLE rather than freezing mid-stride. `prefers-reduced-motion` targets vestibular triggers,
>   not an idle blink, and a mascot that holds still is not the product.
> - **Still open, reported not fixed:** `threats_page.py:135` risk-legend **dot** and
>   `charts.py:66`'s chart "mute" tone use `--muted2` (2.45:1) — non-text, adjacent labels
>   carry the meaning, and both are faithful web ports, so any fix goes on the site first.
>   **NEW:** the chat's sent-message bubble (`clay_chat_popup.py:150-153`) paints `#ffffff` on
>   a gradient measuring **1.95:1 at the top stop**, 3.05 at 0.45, 4.45 at the bottom — worse
>   than anything in the D15a ledger, and fixing it is a redesign of the companion's core
>   surface, so it needs scoping rather than a colour swap. **NEW:** `settings_dialog.py:540/566`
>   set `selection-background-color` with no `selection-color`, so selected text falls back to
>   the palette's HighlightedText; not measurable from the QSS.
>
> **Follow-up debt:** (1) **OPEN** — PRE-EXISTING `usage_daily` 48h-rolling-window rollup understates historical counts; 5 routers read it (`account.py`, `admin_data.py`, `admin_health.py`, `admin_orgs.py`, `admin_stats.py`); fix = derive from `audit_logs`, own phase. (2) ✅ **FIXED** — org-level breach notices moved out of the grading batch into `org_notifications_loop` on its own thread. (3) ✅ **RESOLVED as deliberate** — `user_notifications_enabled` intentionally does *not* gate the org-policy notice; that switch governs the per-user fan-out, and reusing it would silently disable a paid feature (reasoning now in `worker.py:361-364`). (4) ACCEPTED — per-user breach-alert queue is in-memory (≤5s email loss at deploy; in-app notification unaffected).
>
> ⚠ Stale reference: `user_notifications.py:11` still names `worker._notify_breach`, which no longer exists.
**Rule:** if scope changes, update THIS file first — it is the single source of truth.
**Source-of-truth rule (owner mandate):** past plans drifted from the shipped site. This plan's inventory was read from the **live code** (`foxy-audit-premium.html`, all 4,027 lines + `backend/app/routers/*`), NOT from the old design docs. Every executor phase MUST re-read the relevant live SPA section and router file before building, and MUST verify response shapes against the running local stack — if live code disagrees with this plan, live code wins and the executor reports the delta so MAIN updates this file.

---

## 1. Context & goal

The customer dashboard (`foxy-dashboard/foxy-audit-premium.html`, 4,027-line SPA, P0–P18 all shipped) is the complete customer surface: 9 pages, ~30 settings, 8 chart types, ~60 `/v1` calls. The PyQt6 desktop app (`desktop/`, ~7.5k lines) already ships a fox pet + a 5-page "Auditor Console" + AI chat + settings, wired to 8 endpoints via an org API key.

**Goal:** every dashboard feature — every page, card, table, chart, and setting — natively in the desktop app, PLUS a full fox-companion feature layer with its own settings (including new "extra" settings). End state: a customer can live entirely in the desktop app.

## 2. Locked decisions (owner, 2026-07-26)

1. **Native PyQt6 rebuild** — real Qt widgets, no QWebEngineView.
2. **All three platforms** — Windows + macOS + Linux (package + QA each).
3. **Looks IDENTICAL to the web dashboard** — port the web's dark palette byte-for-byte (`--bg #0e0c0a`, `--surf #1c1815`, `--ink #f7f1e8`, `--fox #ff7a2e`, safe `#3ddc84`, breach `#ff4d4d`, warn `#ffc83d`, chart tones `--c-1..6`). **No theme toggle, no skin picker, no light mode in desktop** — one fixed look (the web's dark "original"). **Font is the only permitted deviation:** desktop keeps its already-bundled Unbounded (display) + Space Mono (mono).
4. **Autostart setting** — "Start Foxy when the PC starts" toggle the user can grant/revoke anytime (Win registry Run key · macOS LaunchAgent · Linux `~/.config/autostart/*.desktop`).

## 3. Hard rules binding this build

- **No fake/placeholder data** — purge the existing violations (`dashboard.py:1033` "acme-health-ai", `dashboard.py:1088` "org-4F2A9C · synced 2 min ago"); every new surface has an honest empty state.
- **Content-blindness** — desktop consumes the same bounded-metadata `/v1` API; never display or request raw prompt/response text.
- **Secrets:** the session cookie + org API key move to the OS keychain (`keyring` lib: Windows Credential Locker / macOS Keychain / SecretService). Never write them to QSettings/JSON. API keys / webhook secrets are shown once via a "shown once" dialog (mirror web `revealSecret`).
- Executors push `feat/desk-*` branches only; MAIN verifies + merges (push to `main` deploys prod — desktop code isn't served, but the protocol holds).

## 4. What exists today (reuse, don't rebuild)

| Exists in `desktop/` | Reuse as |
|---|---|
| `omni_fox.py` OmniAwareFox pet: 12-band × 24-frame spritesheet, states, roaming, drag/pat, pynput reactions, tray, breach poller (10s `/v1/logs/breaches?since_seq=`), health check, weekly summary, `/v1/auth/handoff` web SSO | The companion layer foundation |
| `dashboard.py` DashboardWindow: frameless shell, sidebar `QStackedWidget` page model, 5 QThread workers (`/v1/stats`, `/v1/verify`, `/v1/logs`, `/v1/analytics/threats`, `/v1/logs/{seq}`), custom-painted ScoreRing/MiniMeter/NavButton/vector `paint_icon()` set | The console shell; pages get rebuilt/extended per §7 |
| `clay_chat_popup.py` ChatPopup + `ai_providers.py` | Unchanged (companion AI chat) |
| `settings_dialog.py` 3-tab SettingsDialog | Extended per §9 |
| `fox_settings.py` FoxSettings QSettings wrapper | Extended; stays the settings gate |
| `sdk_bridge.py` UDP 9999 local telemetry, `security_overlay.py`, `eye_tracker.py`, `window_tracker.py`, `breach_poll.py` (+tests) | Unchanged |
| `omni_fox.spec` PyInstaller + `installer.iss` Inno Setup | Extended in §10 |

**Known debt to fix in D0 (from exploration):** API client duplicated 8× across 3 files; theme tokens forked 3× (`_clay_tokens` / `_matte_tokens` / `_glass_tokens`, `fox_settings.THEMES` vestigial); `_pick_font`/`resource_path`/`_is_dark` duplicated 2–4×; `apply_theme()` is a 140-line QSS blob; dead settings `behavior/roam`+`behavior/glance` (saved, never read); no window-geometry persistence; dashboard poll workers replaced each tick (thread leak); manual per-thread shutdown in `closeEvent`.

## 5. The API contract (what the client must implement)

Full endpoint census lives in the web SPA (authoritative: `foxy-audit-premium.html` script blocks) and `backend/app/routers/`. The desktop client (`foxy_client.py`, new) implements:

### 5.1 Two auth modes
- **Bearer key mode** (exists today): ingest-adjacent reads only — logs/stats/analytics/verify/coverage/usage/plan/anchors/policies-read/passport/handoff. NOT enough for parity.
- **Session mode** (new, required for parity): `POST /v1/auth/login {email,password,remember_me}` (+ `POST /v1/auth/mfa {email,code,remember_me}` branch) → cookie `session` (DB-side expiry: 30 d remember / 12 h). Everything the web can do becomes available. Bridge without password: `POST /v1/auth/handoff` (Bearer) → `POST /v1/auth/handoff/redeem` → session.

### 5.2 Client mechanics (all in ONE place — `foxy_client.py`)
- Persistent **cookie jar** (session + `foxy_csrf`), serialized to keychain (cookie values are credentials).
- **CSRF:** echo cookie `foxy_csrf` as header `X-CSRF-Token` on every POST/PUT/DELETE when a session exists (mint it with one GET, e.g. `/v1/auth/me`, at startup). On 403 `"CSRF token missing or invalid"` → re-read cookie, retry once.
- **Step-up:** on 403 `{"detail":"step_up_required"}` → emit a Qt signal; UI opens the step-up dialog (`POST /v1/auth/step-up/request` → emailed 6-digit → `/confirm {code}`); **persist the refreshed session cookie** (the 10-min grant lives inside it); retry the original request once. Gated endpoints (7): change-password, users/{id}/role, mfa/disable, DELETE keys/{id}, account/delete, account/ip-allowlist, DELETE account/badge.
- **401 "Session expired or revoked"** → drop to login screen. 403 suspended/deleted wording → terminal error state.
- `POST /v1/passport` returns PDF **or** text/html fallback — sniff Content-Type, save via QFileDialog, open with system handler.
- Body limit 2 MiB; `/health/ready` is public and NOT under `/v1`; rate-limit bucket = Authorization header else IP.
- One generic `ApiWorker(QThread)` (url, method, body → `succeeded(dict)/failed(str)` signals) replaces the 6 bespoke worker classes over time.

### 5.3 Known auth limitation (state honestly in-app)
Native Google Sign-In / enterprise OIDC logins are browser flows; the desktop login screen supports email+password(+MFA) and API-key handoff. Google/SSO-only users: use handoff (paste org API key once) or set a password on the web. No backend change in this plan.

### 5.4 FE↔BE↔DB sync workstream (phase **D-S** — web + backend, runs right after D0)
Live-code audit found the web FE ahead of the BE. The desktop must mirror only REAL controls, so first make the web honest end-to-end:
1. **Three dead Settings toggles** (`foxy-audit-premium.html:1565-1567` — Breach alerts / Weekly digest / Key rotation reminders — checkboxes wired to nothing). Fix by making them real, not by deleting (owner wants BE/DB synced up to FE):
   - **BE:** extend `_ALLOWED_PREFS` (`backend/app/routers/account.py:562`) with `notify_breach_alerts`, `notify_weekly_digest`, `notify_key_rotation_reminders`. **DB: no migration** — `users.preferences` is JSONB; Alembic head stays 0053.
   - **Senders (worker, using the existing `email_templates.layout` + `send_email`):** (a) per-user breach email on graded breach, gated on `notify_breach_alerts` (default true, matching today's checked box; dedupe against the org-level `org_policies.notify_email` path so nobody gets doubles); (b) **weekly digest** job — Mondays, per org, real numbers from `usage_daily`/stats, sent to users with `notify_weekly_digest`, dedup one-per-ISO-week via a `notifications` row (`kind="digest"`, `target_id=YYYY-Www`); (c) **key-rotation reminder** — daily check for active `api_keys` older than 90 days, email admins with `notify_key_rotation_reminders`, dedup monthly the same way. No new tables; if an executor concludes a migration is truly unavoidable they STOP and report (single linear head rule).
   - **FE (web):** wire the three toggles to `GET/PUT /v1/account/preferences` exactly like the Data & privacy prefs (`savePref` pattern).
2. **Skin picker / theme** stays web-only (localStorage) — desktop look is locked; no sync needed.
3. **Everything else checked clean**: all other FE controls have live endpoints (census in §7/§8), and no BE customer feature is missing from the FE that desktop parity needs (`/v1/logs/batch` + `keys/rotate` are SDK-only by design).
4. D-S verify gate (differs from desktop phases — it touches `backend/` + `foxy-dashboard/`): full backend integration suite green (per-file if TRUNCATE-deadlock) · `node --check` every inline script in the changed HTML · single Alembic head · email content escaped · no-fake-data grep. After D-S merges, the desktop Settings phase (D11) mirrors the three now-real toggles; the desktop companion's local alert toggles (§9.2) remain separate, device-level settings.

## 6. Design system port (D0)

New `desktop/foxy_tokens.py` — the ONLY token source (delete/absorb `_clay_tokens`, `_matte_tokens`, `_glass_tokens`, `fox_settings.THEMES`):
- Colors: byte-for-byte the web dark `:root` (§2.3 values), incl. status pairs (`safe/breach/warn` bg+tx) and chart palette `C1..C6` (`#ff7a2e`, `#5b8cff`, `#3ddc84`, `#ff6aa8`, `#ffc83d`, `#9b8cff`).
- Spacing `S1..S8` = 4/8/12/16/24/32/48/64 · radii/border/shadow constants matching the web's clay look (hard near-black borders, offset shadows).
- Fonts: registered once here (Unbounded, Space Mono from repo-root `fonts/`); one `pick_font()`; one `resource_path()`.
- One `qss(section)` builder replacing the per-window 140-line blobs; components (`.clay` card, badge, pill, seg control, dtbl) get named QSS classes reused by all pages.
- ui-ux-pro-max rules applied: 4.5:1 contrast on all text pairs, visible focus rings (fox-orange), 44×44 min hit targets, 150–300 ms motion honoring OS reduced-motion, vector icons only (extend `paint_icon()`, no emoji).

## 7. Console parity — page by page (D3–D10)

Console shell (D3): sidebar gets the web's 9 sections replacing the current 5 — **Home · Threats · Ledger · Verify · Policy · Export · Access · Billing · Settings** — plus top-bar chrome: section title/crumb, **live dot** (60 s `/v1/health` ping in key mode / `/health/ready` fallback), **notifications bell + unread badge**, **breach pip** (unseen max-seq in QSettings), **user chip** (avatar initial, name, role → menu: Settings / Devices / Log out), **announcement banner** (same real signals: latest breach → Threats; trial ≤7 d → Billing; quota ≥90%/over → Billing; dismissals persisted per-id), toasts, **Ctrl+K command palette** (QDialog: 9 page entries, paste ≥8-hex → verify-hash, `#seq` → ledger, copy-org-id, shortcuts), **`?` shortcuts overlay** + g-then-letter quick nav (g+h/a/l/v/p/e/k/b/s). Keep the existing System and Sandbox pages as extra desktop-only sections at the bottom.

Charts (D2): one `charts.py` `FoxChart(QWidget)` family — **bar, hbar, line, area, sparkline, stacked, donut, gauge** — QPainter-drawn, token palette, hover tooltips, legends, honest empty states ("No data yet" + desc), resize-aware. Replaces/extends ScoreRing+MiniMeter.

### D4 — HOME
- **Onboarding stepper** (`GET/PUT /v1/onboarding`): progress gauge + "N / M done", step rows (✓/dashed + title + desc + jump button), 3 title states, dismiss ×.
- **Capture coverage** (`GET /v1/coverage` + `/v1/usage?days=90`): status pill, message, big % + gauge, 4 stat chips (Events observed / SDK clients / Missing events / No client identity), area chart "Events captured per day · last 90", client table **Client | Events | Client seq | Status**, disclaimer footer.
- **Hero**: org-wide interactions today + LIVE pip + sparkline + % delta; 2 tiles → Threats / Quick verify.
- **Stat row**: Breaches stopped · Open alerts · Clean rate · Time to verdict (`/v1/stats`).
- **Usage trend** area chart w/ segmented metric switcher Logs/Tokens/Breaches (`/v1/usage?days=90`) + **grading donut** (graded/pending/failed, center total).
- **Quick ledger check** card (input + check → found/tampered/breach/intact) — replaces the CSS flipcard with a two-state card.
- **Recent ledger** (5 rows, → Ledger) · **Active alerts** (→ Threats) · gauges Clean rate + Time to verdict · **Quick actions** (Policy/Export/Keys/Ledger).
- **Recent activity feed**: merge `GET /v1/account/audit?limit=50` + `GET /v1/auth/login-history`, 30 max, icon map (13 action labels), relative times re-ticking 30 s, refresh, honest empty state.

### D5 — THREATS + LEDGER
Threats: stat row (Total logged / Breaches prevented / Active alerts / Clean rate) · **stacked bar** breaches-by-risk-band with 7d/30d/90d range seg (`/v1/analytics/timeseries`) + legend High ≥70 / Med 40–69 / Low · **Recent high-risk table** **# | Policy | Agent | Risk | When** (`/v1/analytics/threats`) · bar "Activity last 7 days" (`/v1/stats`) · hbar "Breaches by agent" top 8 (`/v1/analytics/by-agent`, "unattributed" fallback) · avg-risk big number + gauge (tone flips at 40/70) · hbar "Top flagged policies". Visiting Threats clears the breach pip.
Ledger: stat row (Records/Breaches/Clean rate/Pending) · area "Volume/day 90d" · **verdict donut** · filter bar (search hash/agent · policy tag · verdict select any/breach/clean/unknown/pending/blocked/redacted · Filter · Clear) · quick chips All/Breaches/Clean/Blocked/Redacted/Pending · main table **tenant(#seq·tag·agent) | hash | verdict | time** with **expandable row detail** (verdict+decision+risk, reason, PII signals, grading·agent·policy, full chain hash mono, "verify this record" → `/v1/verify/hash/{h}`) · pagination (count, prev/next, page label) — `GET /v1/logs?page&limit&q&policy_tag&verdict`.

### D6 — VERIFY + ANCHORS
3-step guided card (paste hash w/ live 64-hex validation hint → Verify/Clear → result panel with the 6 states incl. "Tampering detected @seq" / "intact · policy breach" / "verified — untampered") · quick-check card · "How the fingerprint works" collapsible (HMAC commitments + `chain_hash = SHA256(record + prev)`, offline verifier command `python verifier/foxy_verify.py foxy-audit-logs.json`) · **whole-ledger check** (`GET /v1/verify` incl. >50k partial-window message) · **⚓ Anchor now** admin (`POST /v1/anchors`, 409 handling, Etherscan link) · **anchors receipts** list + freshness + SLA (`GET /v1/anchors`, `/v1/anchors/sla`).

### D7 — POLICY (`GET/PUT /v1/policies`, PUT admin-only — hide save for members)
Unsaved-changes indicator + Save · **Content safeguards** w/ search filter: Block PII (high) · Flag prompt injections (high) · Regulated data mode (medium) · **Judge sensitivity**: max tokens, enforcement (block/flag/monitor), confidence (high/balanced/low), collapsible Alerts & webhooks (notify immediate/digest/none, email, webhook URL) · **AI Judge provider & BYOK**: provider Gemini/OpenAI/Both, key-source seg "my own key"/"Foxy's managed keys" (plan-locked chip; 403 on non-premium), Gemini/OpenAI password fields with "key set ✓" chips + remove-key ("" clears, omit keeps — never display stored keys) · privacy footer.

### D8 — EXPORT
Report config: range presets 30d/90d/1y/all + date pickers + type select (Passport · HTML/PDF | Logs · CSV | Logs · JSON) + Generate with progress states → **save-file dialog + open with system viewer** (Content-Type sniff) · **Chain metadata** card: records, integrity ("100% — no gaps"/"broken at seq N"), chain head + org id behind **mask/reveal/copy** (default masked from `hide_sensitive_metadata` pref), anchor SLA · **Export history** table **Type | Range | By | When** (`POST/GET /v1/exports` record-then-fetch pattern; bytes from `/v1/logs/export` or `/v1/passport`).

### D9 — ACCESS (admin-gated; members see a read-only notice)
Stat row (Active/Total/Limit/Last SDK activity) · keys table **Name | Key(prefix) | Status | Last used | revoke** (status badges, dimmed revoked/expired rows; revoke = step-up) · **+ new key** (name + expiry → plaintext shown ONCE dialog w/ copy; 402 limit toast) · **↻ regenerate (2FA)** (`/v1/keys/regenerate/request`→`/confirm` OTP dialog) · **Connect the SDK** card (3 code boxes + Test connection using `GET /v1/health` w/ the org key).

### D10 — BILLING
Stat row (Credits used/included/remaining/Tokens 30d) · entitlements strip · over-quota banner · quota-headroom gauge + usage area chart + daily table **day | logs | tokens | breaches** (`/v1/usage?days=30`) · **Current plan** rows (tier/status/quota/trial-ends conditional) + **Manage billing ↗** (`POST /v1/billing/portal` → open browser) + **Upgrade ↗** (browser) · **Invoice history**: bar chart of totals ("no per-line-item breakdown" note) + table **date | amount | status | period**, rows open hosted invoice via `GET /v1/invoices/{id}/link` (admin) in browser.

## 8. Settings parity (D11) — every web setting, in the console's Settings page

Tabs/cards mirroring the web's 30 items:
1. **Account & identity**: display name (save → `PUT /v1/account/profile`, feeds avatar+greeting) · email/role/org-id read-only (`/v1/auth/me`).
2. **Change password** (current+new → step-up gated).
3. **Two-factor auth**: state, enroll → emailed code → enable; disable w/ password (step-up).
4. **Data & privacy**: Mask sensitive metadata · Product update emails · Security alert emails (`GET/PUT /v1/account/preferences`). *(Web's 3 UI-only notification toggles are NOT ported — no fake controls; web skin picker NOT ported — locked look.)*
5. **Access control**: IP/CIDR allow-list w/ lock-out warning (step-up).
6. **Devices & sessions**: session list (UA, "this device", IP, signed-in, last-seen) + per-row revoke + **Log out everywhere**.
7. **Recent logins**: on-demand login history list (admin).
8. **Trust badge**: generate (SVG preview via QtSvg + public verify link + copy embed snippet) · revoke (step-up).
9. **What Foxy stores** transparency panel (static).
10. **Team** (admin): list, + add auditor (invite w/ temp password shown once / 402 seat limit), make admin/member (step-up), re-invite, disable/enable, "you" marker.
11. **Account activity** (admin): audit trail w/ refresh.
12. **Outbound webhooks** (admin): list (URL, events, last status, secret prefix) + test + remove + add (URL + breach/graded checkboxes; secret `whsec_` shown once).
13. **Enterprise SSO** (admin): domain/issuer/client-id/secret (blank keeps), active checkbox, callback URL display, save/remove.
14. **Danger zone**: export ledger JSON/CSV · full account export JSON (`/v1/account/export`) · **delete workspace** (type-name confirm, step-up, then logout).
15. **Help & support** links · desktop-app card becomes "You're on the desktop app ✓ vX.Y" + check-for-updates link.

## 9. Fox companion layer (D12) + companion settings (D13)

### 9.1 Companion features (extend what exists — all real events, no fabrication)
- **Event→reaction map** (single `companion_events.py` router consuming the existing pollers/bridge):
  breach (risk ≥ threshold) → ALERTING + red overlay + tray toast + optional beep + speech bubble; clean-streak/health-OK → CHEERING (existing); anchor confirmed → CHEERING + bubble "Chain head anchored ⚓"; quota ≥90%/over → CRYING + bubble → Billing; grading failures spike → THINKING + bubble; SDK bridge `evaluating/hash_confirmed` → THINKING/green flash (existing); connectivity lost → SLEEP + grey overlay, restored → wave.
- **Quick status panel** (new small frameless popover on fox middle-click / tray left-click): chain ✓/✗ (last `/v1/verify`), credits used/remaining, unread alerts count, today's logs sparkline, buttons Open Console · Threats · Verify — all from cached worker data, no new endpoints.
- **Notifications routing**: `/v1/notifications` unread → tray badge count + fox glance; critical level → native toast. Bell in console marks read (`/{id}/read`, `read-all`).
- Existing behaviors kept: roaming, pat→LOVING, click→chat, idle-break emotes, compliance tips, weekly summary, hardware reactions, emotes menu.
- **Purge**: remove hardcoded org strings; sidebar/tray tooltips show the real org (`/v1/auth/me` org or `/v1/health.org`).

### 9.2 Companion settings — full catalog (Settings dialog gains "Companion" + "Alerts" tabs)
**General:** ☐ **Start Foxy at PC startup** (new `autostart.py`: Win `HKCU\...\Run` · macOS `~/Library/LaunchAgents/tech.foxyaudit.desktop.plist` · Linux `~/.config/autostart/foxy-audit.desktop`; grant/revoke = write/remove; "state drift" re-checked on open) · ☐ start hidden in tray · ☐ open console on launch · close button hides-to-tray vs quits.
**Fox:** size 50–150% (scales the 192×208 sprite) · opacity 60–100% · ☑ always-on-top · ☑ roaming (WIRE the dead `behavior/roam`) · roam speed · ☑ glance-at-cursor (IMPLEMENT or DELETE `behavior/glance` — implement: eye overlay already tracks cursor; gate it) · idle-break frequency (off/rare/normal/frequent) · ☑ compliance-tip bubbles + frequency · ☑ typing/scroll reactions · ☑ hardware reactions · pat sensitivity (existing) · reaction cooldown (existing) · click action: chat | console | quick panel · ☑ remember position (+ "reset position") · monitor picker (multi-display).
**Alerts:** ☑ breach alerts · min risk threshold (0–100) · poll interval 5–60 s (default 10) · ☑ sound · ☑ native toasts · ☑ quota alerts · ☑ anchor confirmations · ☑ weekly summary · quiet hours (from–to).
**Account (existing tab, extended):** email sign-in (session) + sign-out + device name · org API key (keychain-backed) · backend URL · ☑ SDK bridge (UDP 9999) on/off.
**AI Brain:** unchanged.
All new keys live in `FoxSettings` with typed getters; secrets → keychain only.

## 10. Foundations & packaging phases

- **D0 foundations:** `foxy_client.py` (client + cookie jar + CSRF + step-up signal + 401 routing + keychain via new `keyring` dep) · generic `ApiWorker` · `foxy_tokens.py` single token/QSS/font/resource source · geometry persistence (pet pos, console pos/size, chat size) · fix worker-replacement leak + centralized shutdown · purge fake data · delete vestigial `THEMES`.
- **D1 auth UI:** login window (email/password, remember-me 30 d, MFA code step, forgot-password → neutral message + "check email, link opens the web dashboard"), API-key handoff sign-in path, step-up dialog (6-digit, resend, Enter/Esc), sessions bootstrap (`/v1/auth/me` probe), sign-out.
- **D14 packaging:** PyInstaller per-OS (spec already has macOS BUNDLE; add Linux one-file + `.desktop`), Inno Setup keeps optional startup task but the in-app toggle is authoritative; version string + "check updates" link; unsigned-binary notes per OS.
- **D15 QA sweep:** manual smoke matrix (3 OS × login modes × step-up × all 9 pages), a11y pass (focus order, contrast, tooltips), reduced-motion, HiDPI scaling, honest-empty-state audit on a fresh org.

## 11. Phase/branch protocol

### 11.0 Skills & logging protocol (mandatory, every phase)
- **`ui-ux-pro-max` FIRST on every UI phase** (D1–D13 — anything that draws widgets): load the skill before designing/building, apply its checks (contrast 4.5:1, visible focus, 44×44 targets, 150–300 ms motion + reduced-motion, vector icons only). Every executor prompt MAIN writes for a UI phase must state this requirement.
- **`claude-mem`** — executors and MAIN recall relevant memory at phase start and save decisions/gotchas as they go.
- **`code-review` skill** — MAIN runs it on every phase branch before merge (part of the §11 verify gate below).
- **`ponytail`** — apply the minimal-solution lens ONLY where provably output-neutral (dead flexibility, reinvented stdlib); never where it could change a rendered value, a hash, or the wire contract.
- **Obsidian (after every work session, executor AND MAIN):** append a dated entry to `G:\My Drive\Life\03 Projects\Foxy Audit\Devlog.md` (what was done, decisions, links; vault house style, `TBD` for unknowns, never fabricate). When a phase changes product state (new pages, new settings, D-S emails), also update the vault's `Desktop` (and for D-S, `Dashboard`/`Backend`) reference notes — create notes freely, ask before editing existing ones, never touch `Templates/`/`.obsidian/`.

One executor phase = one branch off **fresh `origin/main`**: `feat/desk-d<N>-<slug>` (D0 foundations → **D-S web/BE sync (§5.4, branch `feat/web-settings-sync`)** → D1 auth → D2 charts → D3 shell → D4 home → D5 threats-ledger → D6 verify → D7 policy → D8 export → D9 keys → D10 billing → D11 settings (mirrors the D-S-realized toggles) → D12 companion → D13 companion-settings → D14 packaging → D15 qa). Phases share files — **rebase before push** (see admin-phase-stacking rule). MAIN verifies each: `python -m compileall desktop` · `pytest desktop` (pure-logic tests: client CSRF/step-up/retry with a stub HTTP server, autostart dry-run, event-router map, breach cursor) · scope grep (diff stays in `desktop/` + this plan) · no-fake-data grep · no-secret grep (no `foxy_sk_`, no cookie values in code/QSettings writes) · manual launch smoke on Windows · code-review skill → merge via SHA push.

## 12. Verification (end-to-end)

1. `cd backend && docker compose up --build -d` → local stack; seed prints an org key.
2. Desktop: sign in with seeded email/password (remember-me), confirm every page renders real seeded data; run an SDK demo (`python demo/mock_llm.py --scenario all` with the key) → watch live: ledger rows appear, breach → fox ALERT + toast + pip + notification; verify page confirms chain; export passport saves + opens; step-up path on key-revoke works twice (grant expiry 10 min).
3. Kill backend → live dot goes offline, fox sleeps, UI shows honest error states (no stale fake data).
4. Autostart: toggle on → reboot (or check registry/plist/desktop-file) → Foxy launches to tray; toggle off → artifact removed. All three OS.
5. `python verifier/foxy_verify.py` on a desktop-exported `foxy-audit-logs.json` passes — the desktop export is independently verifiable.
6. After D-S: the three web Settings toggles persist across reload (`GET /v1/account/preferences` returns them); seeded-org breach triggers exactly one per-user alert email when the pref is on and none when off; digest/reminder jobs dedupe (run worker twice, one email).
