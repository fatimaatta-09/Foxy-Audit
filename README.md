<div align="center">
  <img src="logo.png" alt="Foxy Audit logo" width="150"><br>

# 🦊 Foxy Audit

### Proof your AI didn't leak or tamper with data — without ever seeing the data yourself.

[![Python](https://img.shields.io/badge/python-3.11%2B-blue?logo=python&logoColor=white)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/backend-FastAPI-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![PostgreSQL](https://img.shields.io/badge/database-PostgreSQL-336791?logo=postgresql&logoColor=white)](https://www.postgresql.org/)
[![PyQt6](https://img.shields.io/badge/desktop-PyQt6-41CD52?logo=qt&logoColor=white)](https://pypi.org/project/PyQt6/)
[![Built with Codex](https://img.shields.io/badge/extended%20with-Codex%20%2F%20GPT--5.6-412991?logo=openai&logoColor=white)](#how-this-was-built-with-codex--gpt-56)
[![Status](https://img.shields.io/badge/status-active%20development-orange)]()

**[Live app](https://app.foxyaudit.tech) · [Docs](docs/) · [Report a Bug](../../issues) · [Request a Feature](../../issues)**

<sub>🏆 An **OpenAI Build Week** submission — category **Developer Tools** · GPT-5.6 (Responses API) judge + Codex-assisted build</sub>

</div>

<br>

## 🔴 The problem

AI companies selling into regulated industries — healthcare, finance, legal — constantly get asked
one question they can't answer well:

> *"Prove your AI didn't leak or alter sensitive data."*

Today there are exactly two answers: **"trust us,"** or a third-party audit that costs tens of
thousands of dollars and takes months. Neither one actually gives the buyer proof — just a promise,
or a snapshot in time that's stale the moment it's signed.

## 🟢 The idea

Foxy Audit replaces the promise with math. Every AI interaction gets a cryptographic fingerprint,
computed **locally, on the developer's own machine** — the raw prompt and response are hashed and
discarded immediately, never transmitted anywhere. Each fingerprint is chained sequentially, so
altering any historical record breaks every hash that comes after it. Anyone — an auditor, a buyer,
a skeptic — can independently recompute and verify the whole chain themselves, without trusting our
servers, our database, or our word for anything.

> **The one sentence that matters:** every competing AI governance tool needs to see your data to
> govern it. Foxy Audit proves integrity without ever holding what was said.

<br>

## 📑 Table of contents

- [How it's different](#-how-its-different)
- [Architecture](#-architecture)
- [Quickstart](#-quickstart)
- [Verifying it actually works](#-verifying-it-actually-works)
- [How this was built with Codex / GPT-5.6](#-how-this-was-built-with-codex--gpt-56)
- [Project status](#-project-status)
- [Roadmap](#-roadmap)
- [Security](#-security)
- [License](#-license)

<br>

## ⚖️ How it's different

| | Sees your raw prompts/responses? | What you get |
|---|:---:|---|
| **AI gateways** (route/proxy your live traffic) | Yes — has to, to route it | Traffic control, cost management |
| **Observability platforms** (ingest calls to scan them) | Yes — their own docs require publishing prompts/responses to their system | Hallucination/toxicity detection |
| **Governance/compliance platforms** (policy documentation) | No, but produces paperwork, not runtime proof | Policy templates, risk frameworks |
| **Foxy Audit** | **Never, for any feature, ever** | **Cryptographic, independently-verifiable proof of integrity** |

We're not a bigger version of any of these. We're the layer none of them provide: mathematical
proof of what happened, generated without ever holding what was said.

<br>

## 🏗 Architecture

```
+-----------------------+        UDP ping         +------------------------+
|   Your AI app          | ----------------------> |  Desktop Companion      |
|   + Foxy SDK            |                          |  (PyQt6, real-time      |
|   (@foxy.audit(...))    |                          |   green/red reaction)   |
+-----------+-------------+                          +------------------------+
            |  hash + metadata only
            |  (raw text discarded locally)
            v
+-----------------------------------------------------------------------+
|                     FastAPI + PostgreSQL Backend                        |
|                                                                           |
|   chain.py         -> sequential SHA-256 hash chain (tamper-evident)    |
|   judge.py          -> combines Gemini + GPT-5.6 verdicts conservatively|
|   policy_engine.py  -> evaluates metadata against org policy config     |
|   anchor.py         -> optional EVM/Sepolia public chain anchoring      |
+-----------------------------------------------------------------------+
            |
            v
+-------------------------+      +----------------------------------+
|   Web Dashboard           |      |   Standalone Verifier              |
|   Ledger, Threats,        |      |   verifier/foxy_verify.py           |
|   Policy, Verify,         |      |   Dependency-free. Recomputes       |
|   Compliance Passport     |      |   the entire chain from scratch --  |
|   export                  |      |   trusts nothing from our servers.  |
+-------------------------+      +----------------------------------+
```

**Three integration points, in order of how most people touch this product:**

1. **Developer** adds one line to existing code (`pip install foxy-audit`):
```python
   from foxy_audit import FoxyClient
   foxy = FoxyClient(api_key=os.getenv("FOXY_API_KEY"))

   # mode="block" runs a local policy check FIRST — PHI/PII, secrets, and
   # prompt-injection are caught and the call is blocked before the prompt ever
   # leaves the machine. mode="redact" scrubs them; the default just records.
   @foxy.audit(policy="hipaa", mode="block", agent="gpt-5.6")
   def call_llm(user_prompt: str):
       return real_llm_call(user_prompt)
```
2. **Compliance officer / founder** logs into the web dashboard — no install, just a browser — to
   review the ledger or export a one-click Compliance Passport for a buyer.
3. **Anyone skeptical** runs the standalone verifier against an exported ledger, with zero
   dependencies and zero trust required in Foxy's own infrastructure.

<br>

## 🚀 Quickstart

```bash
# 1. Backend
cd backend
docker compose up --build -d
docker compose logs foxy-seed          # copy the API key printed here

# 2. Desktop companion (separate terminal)
cd ../desktop
pip install -r requirements.txt
python omni_fox.py

# 3. SDK (separate terminal)
cd ..
pip install -e ./sdk
export FOXY_API_KEY="paste-key-here"
python demo/run_demo.py
```

One command health check — backend connectivity, desktop pet detection, and chain verification,
end to end:
```bash
foxy doctor
```

<br>

## ✅ Verifying it actually works

This is the test that matters — not "does it run," but "does it do the specific thing it claims."

```bash
# Export the ledger
curl -H "Authorization: Bearer $FOXY_API_KEY" \
  "http://127.0.0.1:8000/v1/logs/export?format=json" -o foxy-audit-logs.json

# Verify independently -- trusts nothing from our servers, recomputes from scratch
python verifier/foxy_verify.py foxy-audit-logs.json
# -> chain intact -- N rows verified from genesis
```

**Now break it on purpose.** Open the export, change one character in any historical row's
`response_hash` or `chain_hash`, save it, and re-run the verifier. It must report that exact row as
tampered — and everything chained after it as invalid too. If it still says "intact" after your
edit, the core claim of this product is false. This is the single most important test in this repo.

**No OpenAI key? Two dependency-free demos a judge can run in seconds:**
```bash
python demo/offline_demo.py             # build a chain, verify it, watch tamper detection fire
python demo/mock_llm.py --scenario all  # drive the host-side guard with a mock LLM (block/redact)
```
For the full live GPT-5.6 path, `demo/live_openai_client.py` makes a real Responses API call wrapped
by `@foxy.audit` — see [docs/OPENAI_BUILD_WEEK_SUBMISSION.md](docs/OPENAI_BUILD_WEEK_SUBMISSION.md).

<br>

## 🤖 How this was built with Codex / GPT-5.6

The core hashing, chaining, and verification logic in this repo predates this hackathon — it was
built and independently verified (including the live tamper-detection test above) before the
submission window opened. In the interest of being precise about what's new vs. what existed, here
is exactly what was built or meaningfully extended with Codex/GPT-5.6 during the submission period:

- **`backend/app/judge.py`** — a multi-provider AI judge that runs GPT-5.6 (OpenAI Responses API)
  alongside the existing Gemini evaluator and combines their verdicts conservatively: if either
  provider flags a policy breach, or the two disagree, the combined result treats it as a breach
  rather than silently trusting a "clean" verdict. `judge.validate()` also quarantines an empty or
  self-contradictory model answer as `evaluator_unknown` instead of laundering it into a grade.
- **`sdk/src/foxy_audit/policy.py` + `client.py`** — the **host-side preflight guard**. With
  `mode="block"` / `"redact"`, the SDK runs a local policy check (PHI/PII, secrets, prompt-injection)
  *before* the wrapped model call and blocks or scrubs the prompt on the host — the content-blind
  `blocked`/`redacted` events are graded terminally, never sent to a judge.
- **`backend/app/crypto_secrets.py` + `judge_routing.py`** — **per-tenant AI-judge selection**: each
  org picks its judge/provider and can bring its own API key, encrypted at rest with a rotatable
  Fernet key and bound to `(org_id, provider)` so a stored blob can't be replayed across tenants.
  Keys are decrypted in memory only at grading time and never returned, logged, or chained.

Every change was made by giving Codex full context of the existing codebase first, having it
propose an approach before generating code, and reviewing every diff before accepting it.

**Codex Session ID:** `019f720b-52aa-78d3-a57f-655a8ba3731f`

<br>

## 📊 Project status

**Genuinely working, verified directly against the code:**
- ✅ Local hashing, zero raw-text transmission
- ✅ Sequential hash chain with confirmed tamper detection
- ✅ **Host-side preflight guard** — block/redact PHI/PII/secrets/injection *before* the LLM call
- ✅ Multi-provider AI judge (Gemini + GPT-5.6), per-tenant with encrypted bring-your-own keys
- ✅ Row-Level Security enforced via a non-superuser application role
- ✅ Real signup → API key → email flow via Stripe webhook
- ✅ Standalone, dependency-free verifier script
- ✅ Compliance Passport with host-side enforcement counts
- ✅ SDK published to PyPI (`pip install foxy-audit`)
- ✅ Admin IP allow-list fails closed in production
- ✅ Legal pages (Terms, Privacy) — real content, not placeholders

**Honestly still open:**
- ⏳ The preflight guard covers the **SDK-wrapped** path; a network-level gateway/sidecar to also
  catch calls that bypass the SDK is future work, not a hidden gap
- ⏳ Desktop/mobile installers (signed `.exe`, notarized `.dmg`, Linux AppImage) not yet built

<br>

## 🗺 Roadmap

1. **Network-level gateway / sidecar** — observe traffic at the model boundary so even calls that
   bypass the SDK decorator are captured (closes the completeness gap noted above).
2. Policy versioning (each event tagged with the exact policy version + hash it was checked against)
3. No-login auditor verification portal
4. Evidence API so governance platforms can pull our proof directly

*(Zero-knowledge proof extensions are a genuine long-term direction, not a near-term promise.)*

<br>

## 🔒 Security

Found a vulnerability? Please don't open a public issue — open a private
[security advisory](../../security/advisories/new), or email **support@foxyaudit.tech**.

<br>

## 📜 License

**All rights reserved.** This repository is source-available for evaluation, review, and hackathon
judging purposes only. No license is granted to copy, modify, distribute, or use this code
commercially without explicit written permission from the author.
