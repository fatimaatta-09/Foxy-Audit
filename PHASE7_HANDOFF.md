# Phase 7 Handoff — Foxy Audit

> **New chat: start here.** This is the single orientation doc. Read it + the auto-loaded
> memory index (`MEMORY.md`). It tells you where the build is, what's genuinely missing,
> and a proposed Phase 7 shape. When a doc and the code disagree, **trust the code.**
> Deep technical map: `CODEBASE_DEEP_DIVE.md`. Honest status: `Foxy-Audit-Status-Dossier.html` (v2).

## Current state (as of 2026-07-06)

- **`main` @ `3485f34`**, deployed live to VM **34.18.4.58** (foxyaudit.tech) — CD green.
- Working branch: **`phase3-pilot-ready`** (== `main` after the last push; Phase 6 was committed here then `git push origin HEAD:main`).
- **Phases 1–6 shipped.** 181 backend integration tests green; SDK + verifier tests green. Migrations at a single head **0027**.
- 3 sites live: `foxyaudit.tech` (sales), `app.foxyaudit.tech/dashboard`, `admin.foxyaudit.tech/admin/`.

## What is REAL + production-live (do NOT rebuild)

Hash chain + `/v1/verify` tamper detection · durable Postgres-outbox worker · **RLS actually
enforced** (`SET LOCAL ROLE foxy_app`, mig 0021) · human auth + RBAC + **email-OTP MFA via Brevo
(live)** · HMAC-peppered multi-key · Gemini judge (policy-aware **+ temporal/7-day history**) ·
broad PII (email/SSN/phone/card-Luhn/IP + optional Presidio) · password reset/invites/export/
soft-delete · Stripe webhook (+ credential delivery) · **all Phase 6**: self-serve signup +
checkout (6A), agent attribution in the chain (6B), public trust badge (6C), open-source
verifier `verifier/` (6D), per-tier anchor SLA (6E). Desktop **breach reaction is real** now
(polls `/v1/logs/breaches`). Analytics fixed, `traffic.direction` dropped (0018), Gemini default
`gemini-2.5-flash`. Reviewer's 10 gaps all closed; teammate issues 10/12 done.

## What is MISSING — the honest gaps (Phase 7 candidates)

From a no-fakes audit (2026-07-06). Full detail in memory `dossier-v2-audit-gaps` + dossier §00.

### A · The 4 audit gaps (still stubbed/open)
1. **Prod anchoring is stubbed.** Contract `0x4D4F3359cA43874f9e7e9E158f6E7209dD984F4E` IS deployed
   on Sepolia and the `evm` provider is real + dev-verified, but prod defaults to
   `ANCHOR_PROVIDER=stub`; live check showed the prod org has anchored nothing (`/v1/anchors`=[]).
   → Fix = a **VM `deploy/.env` flip** (`ANCHOR_ENABLED=true` + `evm` + contract/RPC/funded-key) +
   restart the worker. (Uses a real funded key = live gas.)
2. **Desktop `dashboard.py` "Auditor Console" tiles are locally synthesized** (block height / chain
   root / score = sha256 of local state; score self-heals +0.1/sec). The **web** dashboard numbers
   are real; only the secondary desktop console fakes them. → Point tiles at `/v1/stats` + `/v1/verify`, or drop them.
3. **Sales page (`foxy-sale-page/index.html`):** contact form **drops the message** (sends only
   name+email to `/v1/leads`); placeholder links remain (`…/yourusername/…`; "Download" + footer →
   bare `github.com`).
4. **Web dashboard (`foxy-audit-premium.html`) static labels:** "Yo Fatima" greeting, "42ms judge
   latency", "Gemini 1.5 Pro" (backend runs 2.5-flash). Big counters ARE overwritten by JS; these 3 aren't.
- Minor: SDK still sends vestigial `chain_hash`/`timestamp` the backend ignores.

### B · 2 open teammate issues (desktop-only)
- **#2** — desktop has no "open the web dashboard" tray/menu link (genuinely absent).
- **#6** — fox sprite disappears (translucency): mitigated with a guard, not eliminated / no fallback.

### C · Ops flips (config, not code)
- **Arm paid checkout** — paste Stripe `price_…` IDs into VM `deploy/.env` (`STRIPE_PRICE_COMPANION/GUARDIAN`).
- **Rotate the chat-exposed secrets** — Brevo key, Stripe test keys, session secrets, and the funded
  Sepolia key in `backend/.env` (gitignored but real). Deferred by team to "end of project" = now.

## Proposed Phase 7 shape (for the next chat to refine with the user)

Run it like Phase 5/6: **one sub-item per prompt, TDD-first (RED→GREEN), full backend suite green
before the next.** Suggested grouping:
- **7A · Close the code gaps** — sales contact-form message + real links; dashboard static labels;
  desktop "open web dashboard" tray link (#2); drop SDK vestigial fields. (cheap, mostly frontend/desktop)
- **7B · Real desktop console** — wire `dashboard.py` tiles to `/v1/stats` + `/v1/verify` (or remove).
- **7C · Anchoring for real** — flip prod evm on the VM, then add **anchor monitoring** (alert on
  failed/stale anchors) + a **wallet-balance check** so it can't silently stop.
- **7D · Enterprise gates** (business, not just code) — SSO/SAML/SCIM, SOC 2, data residency, uptime SLAs, BAA.
- **7E · Mainnet/L2** migration off Sepolia + operational key management.

## How to run / test / deploy (essentials)

```bash
# Backend integration suite (needs the foxy_pytest DB — see memory backend-local-run-and-tests)
DATABASE_URL='postgresql+psycopg://foxy:foxy@localhost:5432/foxy_pytest' API_KEY_PEPPER=testpepper \
  backend/.venv/Scripts/python.exe -m pytest backend/tests/integration -q
# SDK + verifier (SDK is pip install -e ./sdk into backend/.venv)
backend/.venv/Scripts/python.exe -m pytest sdk/tests verifier/test_verify.py -q
# Deploy = commit on phase3-pilot-ready then push to main → CD SSHes to the VM
git push origin HEAD:main
```

## Standing constraints / decisions
- **ZKP is out of scope** (team decision).
- Secret rotation was deferred to the end — it's now due (see C).
- Design reference for web/dashboard = the clay/paprika look already in the repo (no React/Tailwind).
- gitleaks scans the pushed commit; keep fake test values low-entropy (e.g. `adminpass123`).

## Key pointers
- Memory (auto-loads in a new chat): `MEMORY.md` → `phase6-progress`, `dossier-v2-audit-gaps`,
  `foxyaudit-prod-accounts` (sensitive), `backend-local-run-and-tests`, `docs-stale-main-is-forward`.
- Status dossier v2 (honest): `Foxy-Audit-Status-Dossier.html` + Artifact.
- Deep technical map: `CODEBASE_DEEP_DIVE.md`.
