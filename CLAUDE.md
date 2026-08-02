# CLAUDE.md — Foxy Audit

Guidance for Claude Code working in this repository. **Read "How Claude works here" first — it is mandatory, not advisory.**

Foxy Audit is a **content-blind, tamper-evident audit-evidence platform for AI systems** in regulated industries (healthcare, finance, legal). A Python SDK creates local commitments and ships only bounded metadata to a hash-chained PostgreSQL ledger; customers export and independently verify evidence without trusting Foxy. Live at foxyaudit.tech. Deep product docs live in the Obsidian vault (see below).

---

## How Claude works here (operating model — READ FIRST)

### 1. The two-chat workflow
All substantial work runs through a **planner ↔ executor** split:

- **MAIN chat = planner + reviewer + committer.** It plans the *entire* task, writes the full plan to a **Markdown file** in `docs/plans/` (e.g. `docs/plans/<slug>.md`), and **ends its message with a paste-ready prompt** the user hands to a separate **executor** chat. The main chat then reviews and commits/merges what the executor produces.
- **EXECUTOR chat(s) = build per the plan file.** They implement, run checks, and push feature branches (`feat/*`, `fix/*`) — never to `main`.
- Keep the plan file the single source of truth; if scope changes, update the plan file and re-issue the prompt.

### 2. The poller (toggle on / off)
The MAIN chat can run a **background poller** that watches `origin` for executor-pushed branches, then verifies + merges them. It is **toggleable**:
- **ON** while executor chats are actively building (poll every ~90s; on a new unmerged `feat/*`/`fix/*` branch → run the verify checklist → merge).
- **OFF** otherwise (default). The user says "turn the poller on/off"; honor it. Never leave it running unattended past the work session.

### 3. Mandatory skills / tools
- **UI/UX work → ALWAYS invoke the `ui-ux-pro-max` skill first.** Any design, build, or review of UI (customer dashboard, admin console, sale page, transactional emails, slide decks, badges) *must* start by loading `ui-ux-pro-max`. No UI without it.
- **`claude-mem` → always on.** Recall relevant memory at the start of a task and save decisions, facts, and gotchas as you go.
- **`code-review` skill → run before merging** any non-trivial change (the committer's gate).
- **`ponytail` → use for simplification, but ONLY when it does not change behavior/output.** Apply its "laziest correct / minimal" lens where it's provably output-neutral (dead flexibility, reinvented stdlib, needless deps). If simplifying could alter a result, a verdict, a hash, or the wire contract — **skip it.**

### 4. Obsidian sync (mandatory after every session)
After any work, **append a dated log entry** to the Obsidian Foxy Audit folder:
```
G:\My Drive\Life\03 Projects\Foxy Audit
```
- Log **what was done, decisions, and links**, dated (`YYYY-MM-DD`), in the vault house style: frontmatter (`type/date/tags/ai-first`), a "For future Claude" preamble, `[[wikilinks]]`, dated sources, `TBD` for unknowns — **never fabricate**.
- If the product's state changed, also update the relevant reference note there (hub: `Foxy Audit — Full Reference`). Create new notes freely; **ask before editing** the MOC / existing notes; **never touch** `Templates/` or `.obsidian/`.

### 5. Commit / merge protocol (committer)
- Branch off **fresh `origin/main`**; never edit `main` directly. Isolate work in a `git worktree` to avoid the shared-tree lock.
- **Verify checklist before merge:** fast-forward-safe over `origin/main` · `node --check` every inline `<script>` in changed HTML · scope grep (change stays in intended area) · **no fake/placeholder data** grep · **no secret** grep · single Alembic head for migrations · `code-review` skill.
- Merge via **direct SHA push**: `git push origin <sha>:refs/heads/main`.
- **Pushing to `main` = deploying to production** (GitHub Actions → VM → `docker compose up --build` → `/health/ready` smoke + auto-rollback). During a judging / XPRIZE window, keep work on branches and merge only when intended.

### 6. Hard rules (never break)
- **No fake / placeholder data** — honest empty states only, in code *and* UI.
- **Content-blindness** — raw prompt/response text never leaves the customer process via the SDK and is never stored server-side. Only hashes + bounded metadata.
- **Never commit secrets**; never serialize `password_hash` / `key_hash` / `*_key_enc` / session `token_hash`. API keys are shown once.
- The **KEK** (`PROVIDER_KEY_ENCRYPTION_KEY`) is a crown jewel — lose it and every stored BYOK key is unrecoverable.
- **Verify live state before acting** — read the actual code / schema / deployed branch before declaring a bug or writing a fix.

---

## What this repo is (the product)

Three tiers plus the human surfaces:
1. **SDK** (`sdk/`, `pip foxy-audit` 1.1.x) — `@foxy.audit(policy, mode, agent)`. `mode="block"|"redact"` runs a **local policy check first** (PHI/PII, secrets, prompt-injection) and blocks/redacts **before** the model call; otherwise it runs the call, then hashes prompt+response into customer-keyed HMAC-SHA-256 commitments and ships only metadata over a durable SQLite spool.
2. **Backend** (`backend/`, FastAPI + PostgreSQL) — validates content-blind metadata, appends it to a **sequential SHA-256 hash chain** (per-org, RLS-isolated), and a durable outbox **worker** grades each event's *metadata* with the **AI judge** (Gemini and/or **GPT-5.6** via the OpenAI Responses API; per-tenant BYOK, keys encrypted at rest). Optional EVM/Sepolia anchoring of the chain head. Three-ASGI split: `customer_api` (`/v1/*`), `admin_api` (`/admin/v1/*`), root.
3. **Verifier** (`verifier/foxy_verify.py`) — dependency-free; recomputes the chain from an export → tamper-evident proof anyone can run.
4. **Surfaces** — customer dashboard (`foxy-dashboard/`, app.foxyaudit.tech), staff admin console (`foxy-adminpage/`, admin.foxyaudit.tech), sale page (`foxy-sale-page/`, foxyaudit.tech), Compliance Passport PDF, public trust badge, and the PyQt6 **desktop fox** companion (`desktop/`, internal name `omni_fox` / "OmniAware Fox").

## Where things live

| Path | What |
|---|---|
| `sdk/src/foxy_audit/` | SDK: `client.py` (decorator + guard), `policy.py`, `pii.py`, `dispatch.py`, `spool.py`, `hashing.py` |
| `backend/app/` | FastAPI app: `main.py` (3-ASGI), `chain.py`, `worker.py`, `gemini.py`, `openai_judge.py`, `judge.py`, `judge_routing.py`, `crypto_secrets.py`, `routers/`, `models.py` |
| `backend/migrations/versions/` | Alembic (linear, head **0060**; 59 files — the number `0023` was never used) |
| `foxy-dashboard/` · `foxy-adminpage/` · `foxy-sale-page/` | Web UIs (CSP-safe: inline SVG charts, embedded fonts, no CDN; token-driven theming) |
| `verifier/` · `demo/` · `contracts/` | Standalone verifier · demos (`mock_llm.py`, `offline_demo.py`, `live_openai_client.py`) · `AnchorRegistry.sol` |
| `deploy/` · `.github/workflows/` | Prod compose + `.env.example` · `ci.yml` / `deploy.yml` / `release.yml` |
| `docs/` · `JUDGES.pdf` | In-repo docs · judge test guide |

## Running & commands

```bash
# Local full stack (from backend/): prints an API key in foxy-seed logs
cd backend && docker compose up --build -d && docker compose logs foxy-seed

# SDK / guard demos (no key, no network):
python demo/mock_llm.py --scenario all
python demo/offline_demo.py

# Backend tests (native PG on :5433, db foxy_pytest). Full suite can hit a TRUNCATE
# deadlock — run per-file if it trips:
cd backend && python -m pytest tests/integration -q
python -m pytest tests/integration/test_crypto_secrets.py -q -p no:randomly

# Independent chain verification (the core proof):
python verifier/foxy_verify.py logs.json
```

## Reference

**Deep per-area detail lives in the Obsidian vault at
`G:\My Drive\Life\03 Projects\Foxy Audit\`.** Rewritten 2026-07-30 from this
codebase; every note carries a `verified-against:` commit SHA, so you can
`git diff <sha> HEAD` to see what it has not caught up with.

**Start at `Foxy Audit\CLAUDE.md`** (rules, mandatory skills, the section map, and
a what-breaks-what matrix), then open the note for your area:

| Working on | Read |
|---|---|
| `backend/app/` | `Backend\CLAUDE.md` — 76 modules, 183 routes, the judge pipeline |
| `models.py`, `migrations/` | `Database\CLAUDE.md` — per-table usage map, the 3 RLS postures |
| `sdk/` | `SDK\CLAUDE.md` — the wire contract, the two enforcement vocabularies |
| `desktop/` | `Desktop\CLAUDE.md` — 44 modules, Qt lifecycle, companion layer |
| `foxy-dashboard/` | `Dashboard\CLAUDE.md` — the token system, the two `fetch` patches |
| `foxy-adminpage/` | `Admin Console\CLAUDE.md` — **read its phase-stacking rule first** |
| `foxy-sale-page/` | `Sale Page\CLAUDE.md` — plus a note per page under `Sale Page\<Page>\` |
| `verifier/`, `contracts/` | `Verifier\CLAUDE.md`, `Contracts\CLAUDE.md` |
| `deploy/`, `.github/` | `Deploy & CI\CLAUDE.md` |
| `demo/`, `docs/` | `Demo & Docs\CLAUDE.md` |

Two registers worth checking before you report anything:
`Worth Noting — Issues.md` (known product defects, with what was already ruled
out) and `Where Claude Was Wrong.md` (Claude's own recurring error patterns).

Narrative/history: `Foxy Audit — Full Reference.md` and
`Devlogs\YYYY-MM-DD.md` (one file per date).

- In-repo: `README.md`, `docs/plans/` (live work only), `docs/done/` (archive),
  `JUDGES.pdf`.
- Naming note: the desktop code still uses the older "OmniAware Fox" / `omni_fox` naming (QSettings keys `OmniAwareFox` / `DesktopPet`).
