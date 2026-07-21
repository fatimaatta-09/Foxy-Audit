# Built with Codex + GPT‑5.6

> Honest provenance for the **OpenAI Build Week** submission (category: **Developer Tools**).
> Everything below is verifiable in the source code and the git history. Nothing here is invented —
> the Build Week rules require accurate provenance, and Foxy Audit's real OpenAI story is strong
> enough that it does not need to be embellished.

---

## TL;DR

Foxy Audit is built **on OpenAI** in two concrete, checkable ways:

1. **In the product** — **GPT‑5.6** (the OpenAI **Responses API**) is a first‑class *AI Judge* that
   grades captured events. It is content‑blind by construction: it never receives a raw prompt or
   response, only hashes, a token count, a policy tag, and locally‑detected signal labels.
2. **In the build** — **Codex** was used during the submission window as a coding assistant, under
   human direction and review, alongside the rest of the toolchain. Per‑commit provenance is recorded
   in git and was not rewritten.

---

## 1. GPT‑5.6 in the product (the core OpenAI integration)

This is the part a judge can run and read directly.

| File | What GPT‑5.6 does |
|---|---|
| [`backend/app/openai_judge.py`](../backend/app/openai_judge.py) | POSTs **content‑blind metadata only** to the OpenAI **Responses API** (`https://api.openai.com/v1/responses`, model `OPENAI_MODEL`, default `gpt-5.6`) and parses a structured verdict. Raw prompt/response text is *never* placed in the request body. A provider failure is stored as `unknown`, never silently treated as "clean." |
| [`backend/app/judge.py`](../backend/app/judge.py) | Combines the GPT‑5.6 verdict with the Gemini verdict **conservatively**: any breach wins, disagreement resolves to breach, and an empty or self‑contradictory answer is quarantined as `evaluator_unknown` rather than laundered into a grade. |
| [`demo/live_openai_client.py`](../demo/live_openai_client.py) | A runnable end‑to‑end demo that makes a **real GPT‑5.6 Responses API call** wrapped by `@foxy.audit`, then proves the raw text stayed in the client process and the tamper‑evident chain verifies. |

### Run the real GPT‑5.6 path

```powershell
# Terminal 1 — backend + a configured GPT-5.6 judge
cd backend
$env:OPENAI_API_KEY = "your_openai_key"
$env:OPENAI_MODEL   = "gpt-5.6"
docker compose up --build -d
docker compose logs foxy-seed          # copy the FOXY_API_KEY it prints

# Terminal 2 — a real GPT-5.6 call, audited end to end
python -m pip install -e .\sdk
$env:OPENAI_API_KEY   = "your_openai_key"
$env:OPENAI_MODEL     = "gpt-5.6"
$env:FOXY_API_KEY     = "foxy_sk_...printed above..."
$env:FOXY_BACKEND_URL = "http://127.0.0.1:8000"
python demo\live_openai_client.py
```

Expected (all four must appear — do not claim success otherwise):

```text
[PASS] OpenAI response received
[PASS] Raw prompt/response stayed in the client process; Foxy received commitments
[PASS] Worker verdict: { ... }
[PASS] Foxy chain verified: {"ok": true, ...}
```

No key? The dependency‑free demos still exercise the real commitment, chain, and tamper‑detection
logic: `python demo/offline_demo.py` and `python demo/mock_llm.py --scenario all`.

---

## 2. What existed before vs. what was built during the window

The Build Week rules ask for an honest before/after. The authoritative record is `git log` (dated,
co‑authored commits); this is a plain‑language summary of it.

**Predates the submission window** (built and independently verified earlier):
- Local hashing with raw‑text discard, the sequential tamper‑evident hash chain, and the standalone
  dependency‑free verifier.
- The FastAPI + PostgreSQL backend, the SDK decorator, the desktop companion, and optional
  public‑chain anchoring.

**Built or meaningfully extended during the window** (see the dated commits):
- **Host‑side preflight guard** — `mode="block"|"redact"` runs a local policy check (PHI/PII,
  secrets, prompt‑injection) *before* the wrapped model call and blocks or scrubs the prompt on the
  host. Files: [`sdk/src/foxy_audit/policy.py`](../sdk/src/foxy_audit/policy.py),
  [`client.py`](../sdk/src/foxy_audit/client.py).
- **GPT‑5.6 multi‑provider judge + per‑tenant selection** — the OpenAI Responses API judge above,
  combined conservatively with Gemini, plus per‑tenant provider/key selection with encrypted
  bring‑your‑own keys. Files: `judge.py`, `openai_judge.py`, `judge_routing.py`, `crypto_secrets.py`.
- **Product surface + hardening** — the customer dashboard and staff admin console, branded
  transactional emails, and a round of security hardening (key‑at‑rest encryption with rotation,
  content‑blind logging, step‑up auth).

---

## 3. How AI coding tools were used (honest process)

- **Codex** was part of the development toolchain during the window (Codex session
  `019f720b-52aa-78d3-a57f-655a8ba3731f`). It was given full repository context, asked to propose an
  approach before generating code, and **every diff was reviewed by the project owner before it was
  accepted**.
- Per‑commit provenance — including co‑author trailers and dates — lives in git and was **not**
  rewritten to overstate any one tool's contribution. `git log` is the source of truth; this document
  claims nothing beyond what the commits show.
- The strongest OpenAI claim here is not about which editor typed a line — it is that **GPT‑5.6 is a
  live, content‑blind evaluator inside the shipped product** (Section 1), which a judge can run.

---

## 4. Verify any of this yourself

```bash
grep -n "responses" backend/app/openai_judge.py     # the OpenAI Responses API call
grep -n "gpt-5.6"   demo/live_openai_client.py       # the real GPT-5.6 demo call
python demo/live_openai_client.py                    # a real, audited GPT-5.6 run
git log --oneline                                    # dated before/after provenance
```

Codex session: `019f720b-52aa-78d3-a57f-655a8ba3731f`
