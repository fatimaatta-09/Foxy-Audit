# Branch reconciliation: `origin/foxy-skeleton` (team) vs `feat/phase2-backend-and-dashboard` (mine)

Both branches diverged from `b3f4648`. This maps every overlapping file to a verdict so the merge can
be done deliberately. **Nothing has been merged or force-pushed** — my work is safely on its own branch.

## The one real fork: the ingest / grading architecture
Almost everything else is complementary. The deep conflict is a single architectural decision that both
branches rewrote in opposite directions:

| | Team (`foxy-skeleton`) | Mine (`feat/…`) |
|---|---|---|
| Ingest route | `POST /v1/logs/batch` (list, `slowapi` 60/min rate-limit) | single `POST /v1/logs` (synchronous chain write) |
| Grading | in-process **ThreadPoolExecutor** that also builds the chain + inserts rows + applies `OrgPolicy` | **Postgres-outbox poller** (`grading_status`, `FOR UPDATE SKIP LOCKED`, dead-letter) that only grades pre-inserted rows |
| Crash safety | ❌ in-memory — jobs lost on restart (this is exactly review finding §2.4) | ✅ durable — survives crash/restart |
| SDK | rewritten to batch-async; **drops the `policy_breach` UDP ping** to the desktop fox | unchanged (single-post + UDP breach ping intact) |

**These cannot both be the same route** — it's a product decision. Note the team's new worker *still* has
the §2.4 "jobs lost on crash" problem the review flagged as *Critical for trust*; my outbox fixes it.

## Per-area verdict

### ✅ Cleanly mergeable (take both — additive)
| File | Team added | I added | How |
|---|---|---|---|
| `config.py` | `cors_origins` + `get_cors_origins()` | `session_secret`, 4 `grading_*` settings | union the fields |
| `requirements.txt` | `slowapi`, `weasyprint` | `bcrypt`, `itsdangerous` | union all four |
| `models.py` | `OrgPolicy` table + `pii_signals` col | `User` table + 4 `grading_*` cols | union; keep `Boolean` import |
| `verify.py` | count-guard + `yield_per(1000)` streaming | `require_org`→`resolve_org` | take both |
| `passport.py` | HTML→**PDF** (weasyprint) + template-dir fix | same template-dir fix + `resolve_org` | take theirs' PDF + my auth swap |
| `docker-compose.yml` | `foxy-seed` + `foxy-backend` services | (comment only) | take theirs + add a real `worker` service if adopting my poller |

### ➕ One-sided (only one branch touched it)
| File | Owner | Verdict |
|---|---|---|
| `auth.py` (`_scope_org`, `require_user`, `require_role`, `resolve_org`) | **mine** | keep mine |
| `auth_human.py` (login/logout/me/users) | **mine** | keep mine |
| `gemini.py` (per-policy prompt, `evaluate(meta, policy_config)`) | **theirs** | keep theirs |
| `health.py` (chain_height, uptime, gemini_model) | **theirs** | keep theirs |
| `policies.py` (`GET/PUT /v1/policies`) | **theirs** | keep theirs |
| `foxy-audit-premium.html` (login + live wiring) | **mine** | keep mine |
| `README.md` | both | keep mine (theirs is only a CRLF re-encode) |

### ⚠️ Real conflicts (manual merge required)
| File | Why | Resolution |
|---|---|---|
| `worker.py` | opposite architectures (see fork above) | **pick one** — recommend porting my durable `grading_status` outbox onto their policy-aware worker |
| `logs.py` | `/v1/logs/batch`+offset-page vs single `/v1/logs`+cursor+`/v1/stats` | follow the chosen ingest model; layer my `/v1/stats` + filters on top |
| `schemas.py` | both define a different `LogListItem` | merge into one (`id`,`pii_signals`,`grading_status`,`Verdict`); container follows chosen `logs.py` |
| `main.py` | both rewrote imports / CORS / lifespan / routers | union routers (`auth_human` **and** `policies`); keep **both** `SessionMiddleware` + their restrictive CORS; keep my `/dashboard`; lifespan per worker choice |
| `seed_org.py` | their RLS-GUC wrapper vs my `--admin-*` user creation | combine both in one transaction |
| `dashboard.py` (desktop) | their backend QThread workers + Sandbox page vs my clay/glass restyle | graft their wiring onto my restyle (same regions) |
| migrations | both use rev `0003`+`0004` (`down_revision 0002/0003`) → Alembic multi-head | **renumber mine**: `0003_policies → 0004_pii_signals → 0005_grading_status → 0006_users` (DDL is disjoint, order free) |
| SDK `dispatch.py`/`client.py` | their batch + dropped UDP ping vs mine (=base) | tied to worker choice; if taking theirs, re-add the UDP breach ping or move breach-notify server-side |

## Gating dependencies (must line up or imports break)
- `resolve_org` exists **only on mine** — needed by every `Depends(resolve_org)` swap.
- `grading_status` column **only mine**; `pii_signals` column **only theirs** — the merged `models.py` must have **both**.
- `/v1/logs/batch` (theirs) vs `/v1/logs` (mine) — the SDK and the desktop UDP-breach flow depend on which wins.

## Recommended reconciliation
1. **Base on the team's branch** (it's the mainline: batch, rate-limiting, policies, PII, PDF passports, richer health, SDK chaining).
2. **Graft my additive features** on top: human auth (`auth.py`+`auth_human.py`+`users`+`SessionMiddleware`), the `/dashboard` serving + HTML wiring, `/v1/stats` + read filters, `resolve_org`.
3. **Renumber my two migrations** to `0005`/`0006`.
4. **Decide the queue explicitly:** their worker still loses jobs on crash (§2.4). Recommend porting my `grading_status` outbox onto their policy-aware worker — either now, or as a fast follow-up PR.
5. Land it as a **PR** so the team reviews the queue + dashboard.py decisions.
