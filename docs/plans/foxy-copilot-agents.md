# Foxy Copilot — the intelligence layer over the evidence

**Plan of record** · 2026-07-28 · MAIN chat is the committer; executors build per this file.
Repo state at planning time: `main` = `46e0055`, Alembic head **0054**.

---

## Context

Foxy already runs an AI judge on every event. What it has never had is an agent surface **for
the human** — the compliance officer who must justify a decision, the engineer who must write a
policy, the founder who must know what happened this week.

The owner asked for five agents and then gave a free hand. This plan is a superset: five became
thirteen, organised into a coherent layer rather than a feature list, because most of them share
one harness and the same three inputs the product already computes.

**The constraint that shapes everything below:** Foxy's value is that its evidence is
*deterministic and independently verifiable*. A language model must never be able to weaken
that. So:

> ### Deterministic detection. Generated narration.
> Numbers, verdicts, scores and evidence stay computed in Python. The model only ever *explains*
> what was already decided. No figure that appears in an evidence export originates from a model.

That single rule is what makes an LLM safe to put inside an audit product, and it is the thing
most likely to be quietly violated by a well-meaning executor. It is tested, not just stated.

**A second property worth naming, because it sells:** the Copilot is **content-blind too**. It
never sees a prompt or a response — the SDK never sends them. Foxy can say its own AI cannot
read customer data either. That is a differentiator, and it is also a hard design constraint.

**Owner decisions (2026-07-28):** platform Gemini key pays for everything · the assistant gets
docs + metrics + actions · lead triage sends message/company only, never email or name.

---

## 1. Reuse — and the one line that must survive

`backend/app/gemini.py::evaluate()` already does structured JSON, `temperature=0`,
`response_mime_type="application/json"`, settings-driven timeout, and key handling. Do not write
a second Gemini client. **Extract** the raw call into
`gemini.generate_json(system_prompt, payload, *, api_key, timeout) -> dict` and have
`evaluate()` call it — `evaluate()`'s behaviour must not change; it is the judge, on the money
path, and its existing tests are the proof.

**The load-bearing line, easy to lose in a refactor:**

```python
log.warning("gemini evaluate failed (%s)", type(exc).__name__)   # TYPE ONLY
```

Google authenticates with the API key as a `?key=…` **URL query parameter**, and several
exception types in that stack embed the request URL in `str(exc)`. Logging the message writes
the key to the logs. Every agent inherits `type(exc).__name__`-only logging, and a test asserts
it, because this is invisible in review and unrecoverable in production.

| Need | Exists | Path |
|---|---|---|
| Gemini call, JSON out | `evaluate()` | `gemini.py:89` |
| Per-tenant key resolve/decrypt | `resolve_judge_routing()` | `judge_routing.py:111` |
| Scheduled loop pattern | `usage_loop()` | `usage.py:181` |
| Email send | `send_email()` | `email.py:21` |
| Passport counters | `blocked/redacted/enforced_events` | `routers/passport.py:124-182` |
| Chain state | `verify_chain()` | `routers/verify.py:24` |
| Capture gaps | `get_capture_coverage()` | `routers/coverage.py:24` |
| Dual-judge merge | `combine()` | `judge.py:67` |
| Rule taxonomy (`phi.`/`pii.`/`injection.`/`secret`) | `policy.py` | `sdk/src/foxy_audit/policy.py` |
| Policy write + admin gate | `PUT /v1/policies` | `routers/policies.py:152` |
| Leads | `MarketingLead` | `models.py:684` |
| Rate limiting | `slowapi` `Limiter` | `routers/logs.py:40` |

---

## 2. `backend/app/agents.py` — one harness, thirteen callers

```python
def run_agent(name, system_prompt, data: dict, schema: dict) -> AgentResult
# -> ok(fields) | unavailable(reason).  Never raises. Never fabricates.
```

Enforced centrally so no agent has to remember:

- **No invention.** Any number in the response that is not present in `data` fails validation →
  `unavailable`. The schema has no free-numeric fields.
- **Fail honest, not blank.** Timeout/quota/bad-JSON → `unavailable` + reason; the UI shows an
  honest empty state. Generated prose is still data: a plausible invented summary is the worst
  form of the no-fake-data violation.
- **Content-blind.** Rejects a `data` dict containing `prompt|response|content|text|body` at any
  depth. The SDK never ships raw text, so this can only trip on a coding mistake — exactly when
  you want it to.
- **Cache by input digest.** Every agent output is stored in `agent_outputs`
  (`org_id, kind, body, input_digest, generated_at`) and regenerated only when the digest moves.
  Most weeks nothing has changed; this is the difference between a viable cost model and an
  unbounded one.
- Logs `type(exc).__name__` only.

Batch agents run on a new `agents_loop` following `usage_loop`'s shape (own thread, own session,
never on a request path). **No agent runs on a dashboard page load.**

---

## 3. The catalogue

### Tier 1 — Explain (data → English, read-only, zero risk)

| # | Agent | Reads | Surface |
|---|---|---|---|
| 1 | **Alert summary** | graded-event metadata, 7-day activity | card atop Threats |
| 2 | **Passport narrative** | `passport.py` counters | PDF, labelled block |
| 3 | **Breach explainer** | one event's rules + verdict + tag | expandable ledger row |
| 4 | **Chain explainer** | `verify_chain()`, last anchor | Verify page |
| 5 | **Judge disagreement** | both `Verdict`s before `combine()` | event detail |

**#3 is the sleeper.** When a breach fires, a compliance officer must *justify* it to someone.
Today they get `phi.mrn_detected` and a risk score. Turning the rule taxonomy plus the verdict
into "this was blocked because the prompt matched a medical-record-number pattern under your
HIPAA policy, before it reached the model" is the difference between evidence and usable
evidence.

**#5 exists only because Foxy runs two judges.** `combine()` already merges Gemini and GPT-5.6
and marks `multi_judge_*`. Nobody else can build this feature, so it is worth more than its cost.

### Tier 2 — Advise (deterministic score, generated explanation)

| # | Agent | Deterministic part | Model's job |
|---|---|---|---|
| 6 | **Audit-readiness** | gaps from `get_capture_coverage()`, chain from `verify_chain()`, anchor age | prioritise + explain the fix |
| 7 | **Policy drift** | declared `enforcement_mode` vs observed verdict mix | narrate the gap |
| 8 | **Anomaly flags** | z-score on policy-tag counts week/week | explain the spike |

**#6 is the strongest feature in this plan.** It answers the buyer's actual question — *"are we
ready if we get audited?"* — from three things already computed. Score and gaps are Python; the
model only writes the prose. Ship this before the assistant.

The scores in #6–#8 are computed, never generated. A model that "estimates" audit readiness is
exactly the product Foxy exists to argue against.

### Tier 3 — Act (proposal + confirmation)

**#9 · Policy authoring copilot** — the highest-value agent here, because writing a policy is
the hardest part of onboarding. Describe the need in English → the model proposes a
**`PolicyConfig` diff**, rendered as a reviewable before/after. The operator applies it through
the existing `PUT /v1/policies` with its existing admin gate. The model never writes config
directly; it produces a diff a human accepts. Enum fields are validated against
`Literal["block","flag","monitor"]` before the diff is shown, so an invented mode never renders.

**#10 · The Foxy Assistant** — §4.

### Tier 4 — Foxy's own operations (internal, not customer-visible)

| # | Agent | Note |
|---|---|---|
| 11 | **Lead triage** | message/company/source/UTM only — **never `email` or `name`**. Score 1–5 + draft reply. Human sends; never auto-send. |
| 12 | **Revenue digest** | weekly email to the owner via `send_email()`. Lowest risk of all: nobody else sees it. |
| 13 | **Support inbox drafts** | `admin_inbox.py` already filters message-bearing leads. Same shape as #11. |

---

## 4. The Foxy Assistant (docs + metrics + actions)

### 4.1 No retrieval layer — measured, not assumed

`docs/` is 325 KB, but nearly all of it is internal build docs. The **customer-facing** corpus is
`README.md` (13 KB) + `sdk/README.md` (3.8 KB) + `THREAT_MODEL_AND_EVIDENCE_BOUNDARY.md` (10 KB)
≈ **27 KB ≈ 7k tokens** — trivial inside `gemini-2.5-flash`. **No embeddings, no vector DB, no
pgvector, no new dependency.** Concatenate into the system prompt.

**The corpus is an explicit allowlist constant, never a directory glob.** A glob would happily
tell a customer about `ADMIN_LIQUID_GLASS_REDESIGN.md` or anything under `docs/plans/`. Tested.

### 4.2 Actions: the agent proposes, existing endpoints dispose

The whole safety design in one sentence: **the assistant never executes anything.** It returns a
structured *proposal*; the UI renders a confirmation card; the click calls **the same `/v1`
endpoint the dashboard already calls**, with the same session auth, CSRF and step-up gate. No new
mutating endpoint, no privileged path. Anything step-up-gated today stays gated when the idea
came from the assistant.

| Tier | Examples | Treatment |
|---|---|---|
| 0 navigation | "show Threats filtered to breaches" | client-side, no confirm |
| 1 safe writes | run an export, mark notifications read | confirm card → existing endpoint |
| 2 dangerous | delete/rotate key, change role, billing, account delete | **not exposed in v1** |

Tier 2 is excluded **by allowlist, not by prompt instruction** — a prompt is not a permission
system. Tested: no step-up-gated endpoint may appear in the action allowlist.

### 4.3 Metrics without breaking content-blindness

Bounded metadata only — counts, verdicts, policy tags, quota, chain status. Never prompt or
response text. Enforced by the §2 harness guard; a tenant-isolation test proves org A cannot see
org B.

### 4.4 Surface

Session-authed `POST /v1/assistant` on `customer_api`, per-org rate limit via the existing
`slowapi` limiter, bounded conversation history so the prompt cannot grow without limit.

---

## 5. Phasing — one branch per phase, `feat/agents-<n>-<slug>`

| Phase | Contents | Why here |
|---|---|---|
| **P1** | `generate_json` extraction + `agents.py` harness + `agent_outputs` table + `agents_loop` + **#1 alert summary** | Everything else is a prompt and a surface once this exists. Judge tests prove the extraction was safe. |
| **P2** | **#2 passport narrative**, **#3 breach explainer**, **#4 chain explainer** | Pure Tier-1 explain, no new data, immediate customer value |
| **P3** | **#6 audit-readiness** (+ #7, #8 if time) | The buyer's question. Ship before the assistant. |
| **P4** | **#9 policy copilot** | Highest value, needs the diff-review UI |
| **P5** | **#10 assistant** | Depends on P1–P3 surfaces existing |
| **P6** | **#11–#13 internal** (lead triage, digest, support drafts) | Zero customer risk; can slot earlier if sales needs it |
| **P7** | **#5 judge disagreement** | Genuinely unique; small once P2 exists |

---

## 6. Hard rules

- **Deterministic detection, generated narration.** No evidence figure originates from a model.
- **No fake data** — unavailable agent → honest empty state, never invented prose.
- **Content-blindness** — no raw prompt/response text to any model, ever.
- **Never log `str(exc)` from a Gemini call.** §1.
- **The Passport is evidence** — the narrative is a labelled "Summary (generated)" block that
  never replaces or restates a deterministic number. If the agent is unavailable, the PDF
  renders exactly as today. Generation must never block or alter an evidence export.
- Alembic linear; head is **0054** (`CLAUDE.md` still says 0053 — stale, fix in passing).

---

## 7. MAIN ↔ EXECUTOR protocol (the fix)

Previous failure: prompts went stale, the executor ran out of context and stopped picking work
up, and neither side knew whose turn it was.

1. **Every message ends with a prompt for the other side. Both directions, no exceptions.** MAIN
   ends with a paste-ready `TO EXECUTOR` block; the executor ends with a paste-ready `TO MAIN`
   block. If there is nothing to do, the block says exactly that ("nothing queued — awaiting
   owner"), so silence is never ambiguous.
2. **One task ID, one branch, one status.** `TASK <n> — <phase>`, `feat/agents-<n>-<slug>`. The
   executor's report opens `TASK <n> · <branch> · <SHA> · DONE|BLOCKED`.
3. **The inbox is self-contained.** Every prompt restates repo path, branch base, fast-check
   commands and hard rules. A fresh chat with zero history must be able to start from the block
   alone — assuming context the new chat lacked is precisely what broke before.
4. **MAIN's poller (only while an executor is building; OFF by default).** Every ~90s, check
   `origin` for unmerged `feat/agents-*`; on a new branch run the gate — FF-safe · scope grep ·
   no-fake-data · no-secret · single Alembic head · full backend suite · guards re-broken to
   prove they bite — then merge by SHA push and watch the deploy.
5. **Stale tasks are deleted on merge, in the same action.** A completed block left in the inbox
   is what sent an executor to rebuild finished work.

---

## 8. Verification

```bash
cd backend && python -m pytest tests/integration -q      # per-file if TRUNCATE deadlocks
python -m pytest tests/integration/test_judge*.py tests/integration/test_gemini*.py -q
alembic heads                                            # exactly one
```

Each guard must fail when the rule it protects is removed — re-break every one:

1. **Key never logged** — raise an exception whose `str()` embeds a fake key; assert it is absent
   from captured logs.
2. **Content-blindness** — pass `data` containing a `prompt` key; assert refusal.
3. **No invention** — stub a response containing a number absent from `data`; assert
   `unavailable`.
4. **Honest failure** — stub a timeout; assert an empty state, not prose.
5. **Determinism** — assert every score/count in #6–#8 comes from the Python path with the model
   stubbed out entirely.
6. **Corpus allowlist** — no file outside the allowlist reaches the prompt.
7. **Action allowlist** — no step-up-gated endpoint is proposable.
8. **Tenant isolation** — org A's assistant cannot read org B's metrics.
9. **Passport unaffected** — with the agent unavailable, the PDF matches today's apart from the
   absent summary block.
10. **Judge unchanged** — the `generate_json` extraction leaves every existing judge test green.
