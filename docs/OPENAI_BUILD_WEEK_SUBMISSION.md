# OpenAI Build Week Submission Runbook

This document is a truthful, repeatable test plan for Foxy Audit's OpenAI Build
Week submission. It separates checks that this repository can perform from
submission information that only the project owner can provide.

## Project in one paragraph

Foxy Audit is a developer tool for AI teams that need evidence their captured
AI records have not been changed, without centralizing prompt and response text.
The SDK creates customer-keyed commitments locally, the backend records an
ordered tamper-evident chain, and customers can independently verify exported
evidence. Optional OpenAI and Gemini judges assess only bounded metadata; they
do not inspect raw conversation content.

This is an integrity and evidence layer. It is not legal advice, a compliance
certification, a content firewall, or proof that an uninstrumented model call
was captured.

## Recommended category

Submit under **Developer Tools**. The core user is the team building or
operating an AI application; Foxy integrates at the model-call boundary and
returns evidence for engineering, security, and audit workflows.

## What GPT-5.6 Does

Foxy uses the official OpenAI Responses API in two real, optional locations:

1. `demo/live_openai_client.py` makes a customer-style GPT-5.6 model call that
   is wrapped by `@foxy.audit`.
2. `backend/app/openai_judge.py` sends only content-blind commitments and
   bounded metadata to the configured GPT-5.6 judge after the event is durably
   recorded. The judge returns structured JSON. A provider failure is stored as
   `unknown`, never silently treated as clean.

The customer model provider receives the demo prompt. Foxy does not: the SDK
creates commitments locally and sends Foxy only commitments, policy tags, token
counts, locally detected signals, and allowlisted operational metadata.

## Clean Judge Setup

Prerequisites: Docker Desktop, Python 3.9+, an OpenAI API key with access to
the selected model, and no real customer data in the demo prompt.

```powershell
# Terminal 1, from the repository root.
cd backend
$env:OPENAI_API_KEY = "your_openai_key"
$env:OPENAI_MODEL = "gpt-5.6"
docker compose up --build -d
docker compose logs foxy-seed
```

Copy the `FOXY_API_KEY` printed by `foxy-seed`. If the database was previously
used and the one-shot seeder has already exited, create a separate demo
organization instead:

```powershell
docker compose exec foxy-backend python scripts/seed_org.py --name "Judge Demo"
```

In Terminal 2, from the repository root:

```powershell
python -m pip install -e .\sdk
$env:OPENAI_API_KEY = "your_openai_key"
$env:OPENAI_MODEL = "gpt-5.6"
$env:FOXY_API_KEY = "foxy_sk_key_printed_by_the_seed_service"
$env:FOXY_BACKEND_URL = "http://127.0.0.1:8000"
python demo\live_openai_client.py
```

Expected real result:

```text
[PASS] OpenAI response received
[PASS] Raw prompt/response stayed in the client process; Foxy received commitments
[PASS] Worker verdict: { ... }
[PASS] Foxy chain verified: {"ok": true, ...}
```

Do not claim this run succeeded unless all four checks appear. If the worker
times out, stop and inspect `docker compose logs foxy-worker`; do not replace it
with an offline result.

## Fast Offline Check

When no provider key is available, run the dependency-free verifier demo:

```powershell
python demo\offline_demo.py
```

It uses clearly labelled synthetic records, but executes the real commitment,
chain, receipt, and tamper-detection logic. It is useful for first review but
does not demonstrate a live provider call.

## Required Submission Material

Before submitting, verify the current [official rules](https://openai.devpost.com/rules)
and [FAQ](https://openai.devpost.com/details/faqs). The repository cannot do
these owner-specific steps on your behalf:

- Choose the Developer Tools category in Devpost.
- Provide a public YouTube video under three minutes with voiceover.
- Provide the code URL and a license, or share the private repository with the
  review accounts required by Devpost.
- Add the `/feedback` Codex Session ID from the primary project build session.
- Explain what existed before the event and what was meaningfully extended
  during the event with Codex and GPT-5.6. Use dated commits and actual session
  records; do not invent provenance.

## Three-Minute Video Sequence

1. **0:00-0:20 - Problem.** Explain that regulated AI teams need evidence their
   captured records were not altered but should not have to centralize raw
   conversations in an observability vendor.
2. **0:20-0:55 - Integration.** Show the `@foxy.audit` decorator around the
   real GPT-5.6 client function and state what stays local versus what Foxy
   receives.
3. **0:55-1:35 - Live run.** Run `live_openai_client.py`. Show the actual model
   response, durable receipt, queued verdict, and chain verification. Do not
   hide an error or substitute a screenshot if the run fails.
4. **1:35-2:15 - Proof.** Show the ledger/dashboard and deliberately modify a
   disposable test record, then show verification report the first broken
   sequence.
5. **2:15-2:40 - Operations.** Show one customer/admin control such as policy
   settings, team access, API-key expiry, notifications, or the staff health
   panel.
6. **2:40-3:00 - OpenAI and Codex.** Point to the Responses API judge, the
   content-blind projection, the tests, and the specific Codex-assisted changes
   that were made during the event.

## Submission Stop Checklist

- [ ] `python demo/offline_demo.py` passes.
- [ ] `python demo/live_openai_client.py` completes all four real checks.
- [ ] `pytest backend/tests/integration -q` passes against PostgreSQL.
- [ ] CI is green for the final commit.
- [ ] README setup commands were followed on a clean machine or by a teammate.
- [ ] The public video uses the final commit and includes voiceover.
- [ ] The Devpost description names GPT-5.6 and Codex accurately.
- [ ] The Devpost `/feedback` Session ID is entered by the project owner.
