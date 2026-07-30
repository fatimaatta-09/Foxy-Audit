# Foxy Audit — Ship-to-Client Readiness Report

Work against the teammate's two docs (`foxy_audit_master_overview.md`,
`claude_code_ship_prompt.md`), full 5-section scope. The core cryptographic claim was
already verified; this pass added packaging, download-serving, onboarding, and PyPI prep.
**What remains is credentials and real-OS validation, not engineering.**

---

## §1 — Security audit & hardening
**Already real (verified, not rebuilt):** prod fail-fast on insecure secrets
(`config._require_secure_prod`); RLS via the confined non-superuser `foxy_app` role with a
real org-A-can't-read-org-B test (`test_rls.py`); GitLeaks full-history scan in CI; no real
secrets in tracked files.
**Added this pass:**
- `pip-audit` CI job (backend + SDK env, report-only).
- Prod fail-fast now also requires `ANCHOR_EVM_PRIVATE_KEY` when EVM anchoring is enabled
  (was lazy) — `test_config_prod.py`.
- `test_admin_guard.py` pinning the allow-list behaviour.
**Discrepancy resolved:** the docs claimed the admin IP allow-list "fails closed." It
actually **fails OPEN by design**. Kept fail-open (the operator's admin WiFi has a dynamic
IP that changes daily; staff login + MFA is always required), corrected the overview +
status docs, added the test.
**Human step:** confirm the live Brevo / Gemini / Sepolia keys were rotated (not visible
from the repo). `pip-audit` runs on GitHub's runners — the local sandbox blocks pypi.org
(SSL interception), so review its output in CI.

## §2 — SDK installable + PyPI
- `pyproject` → **1.0.0** + `[project.urls]` + trove classifiers.
- README: fixed the wrong decorator kwarg (`policy_group` → `policy="hipaa_basic"`) and made
  the install honest (published command + source-install fallback).
- CI proves a **clean-venv non-editable** `pip install ./sdk` + `foxy --help`.
- `release.yml` builds sdist+wheel and publishes on `v*` tags.
**Human step:** set `PYPI_API_TOKEN` (or switch to Trusted Publishing) and tag `v1.0.0`.
Until then `pip install foxy-audit` won't resolve for outside users (README says so).

## §3 — Desktop installers ("the .exe")
- `resource_path()` now used consistently (dashboard spritesheet + all font loads) →
  `--onefile` safe. Shared in `fox_settings.resource_path()`.
- **First-run dialog:** no stored key → "Paste your Foxy Audit API key" prompt (dismissable;
  still settable via tray → Settings). Fixed the wrong default `backend_url`
  (`api.foxyaudit.dev` → `app.foxyaudit.tech`).
- `desktop/omni_fox.spec` (PyInstaller) + `desktop/installer.iss` (Inno Setup).
- `release.yml` `build-desktop` matrix: **Windows .exe + Linux AppImage** now, **macOS
  stubbed** (built, not notarized). All **unsigned** unless the cert secrets are set.
**Human step:** buy a Windows code-signing certificate (else SmartScreen warns) and an Apple
Developer ID (for macOS). **Native Windows/macOS builds must be validated on those OSes** —
they can't be produced/tested in a Linux CI sandbox.

## §4 — Download serving + site
- Host **nginx** gets a `/download/` location aliasing an out-of-git dir
  (`/home/devops/foxy-downloads`); Caddyfile mirrors it for the dedicated-box path.
- `release.yml` `publish-installers-to-vm` scp's the builds there under **stable names**
  (`FoxyAudit-Setup.exe`, `FoxyAudit-x86_64.AppImage`) so the site links never change.
- `desktop.html`: real Windows + Linux download buttons + an honest "early access · unsigned
  · SmartScreen" note.
**Human step:** `mkdir -p /home/devops/foxy-downloads` on the VM once.

## §5 — Post-purchase onboarding ("the other thing")
- New `welcome.html`: accept **Terms + Privacy** → save the **shown-once key** (copy) →
  **install the SDK** → **download the desktop app** → **open dashboard** (gated on the Terms
  checkbox; records a best-effort consent audit row via the existing `/v1/consent`).
- The free-signup modal now routes into it via `sessionStorage` (key never in the URL).
- The **paid** post-checkout path can point here later (payment is parked — Stripe dormant,
  Payoneer absent).

---

## Verification evidence (run locally, real output)
- Full backend integration suite: **246 passed, 2 skipped**.
- **Standalone verifier — Test C** (the core claim): intact ledger → `[OK] chain intact`
  (exit 0); a one-character tamper at seq 2 → `[FAIL] CHAIN BROKEN at seq 2` (exit 1).
- Verifier recipe-vs-backend cross-check: **6 passed**. SDK + `foxy doctor` CLI tests: **19 passed**.
- **Could not run locally** (need a real environment, not this sandbox): PyInstaller native
  Windows/macOS builds, AppImage GUI launch (needs a display), `pip-audit` (sandbox SSL),
  and the PyPI publish itself.

## To actually hand this to a real client, you still need:
1. A Windows code-signing certificate **and** an Apple Developer ID (macOS).
2. A `PYPI_API_TOKEN` + the decision to tag `v1.0.0`.
3. `mkdir -p /home/devops/foxy-downloads` on the VM; confirm live-secret rotation.
4. Push a tag (`git tag v1.0.0 && git push --tags`) → CI builds the installers, scp's them to
   the VM, and (with the token) publishes the SDK. Then validate the Windows/macOS installers
   on real machines before distributing.
