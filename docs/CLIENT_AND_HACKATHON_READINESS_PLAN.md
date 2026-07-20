# Client and Hackathon Readiness Plan

**Assessment date:** 2026-07-20
**Working branch:** `feat/judge-redemption-access`
**Scope:** honest readiness assessment for a judge or design-partner client

## Executive Verdict

Foxy Audit is **not yet client-ready or submission-ready as a deployed product**.
It has a credible working core and the judge-access branch now contains a real,
finite redemption and allowance path. The missing work is the proof that the
exact branch is deployed, that the public redemption flow works against the
production database, and that every UI claim matches a verified runtime signal.

The product must be described as:

> A hash-only AI interaction evidence system for interactions captured through
> its SDK, with a tamper-evident server-side chain, optional policy grading, and
> a desktop companion that visualizes local capture and actual backend verdicts.

It must **not** be described as complete monitoring of every AI call, a
certification, a guaranteed clean verdict, or a live blockchain-backed system
unless those conditions are separately configured and verified.

## What Is Implemented

These statements are based on the current repository, not on placeholder data:

| Area | Current state | Evidence / limitation |
|---|---|---|
| SDK privacy boundary | Implemented | Prompt and response are hashed locally; the metadata request contains hashes, token count, policy tag, and local PII signals. The SDK cannot detect calls that bypass its decorator. |
| Tamper-evident ledger | Implemented in backend | The backend builds and verifies sequential chain links. This is a hash chain, not automatically a blockchain anchor. |
| Async ingestion | Implemented | SDK dispatch is background HTTP and the customer API accepts metadata for durable processing. |
| Policy grading | Implemented as optional Gemini path | The authenticated health response and dashboard show configured/unavailable state. Provider fallback verdicts are persisted as `decision: unknown` and excluded from clean-rate statistics. |
| Judge access | Implemented on this branch | Campaigns have hashed codes, finite capacity, expiry, duplicate-email protection, one-time API-key provisioning, Premium workspace access, auditing, and hard `402` capture enforcement after allowance expiry. |
| Dashboard allowance | Implemented in source | The dashboard can show the finite evaluation allowance, but the exact branch must be deployed and exercised before it can be promised to judges. |
| Desktop companion | Partially implemented | The pet listens for local SDK events, shows local capture separately from clean verdicts, polls real backend breaches, opens the dashboard through a one-time handoff, and has optional provider-backed chat. A packaged client distribution is not yet verified. |
| Sales site | Public surface exists | The live marketing site explains the product and links to dashboard/docs, but the judge redemption path and API origin must be checked on the deployed release. |
| Public anchoring | Optional only | Do not claim public-chain anchoring until provider credentials, funding, transaction confirmation, and independent verification are tested. |

## Claims to Remove or Correct

### Implemented: truthful capture state

The SDK emits `evaluating`, then `hash_ok` after local hashing and queueing. The
pet and desktop table label this state as local capture, not backend acceptance
or compliance. Offline runs are labeled `local_only`, and backend verdicts are
shown only after the dashboard receives them.

### Implemented: judges have a real no-LLM path

An LLM is not required to test the SDK, hash chain, allowance, dashboard, or
verifier. The branch provides a small judge script that wraps a deterministic local Python
function with the real SDK. The function is a test client, not seeded audit
data; every record is created by the judge's run and is visible in the normal
ledger. It should accept `FOXY_API_KEY` and `FOXY_BACKEND_URL`, make one safe
compliant call and one intentionally over-threshold call only if the deployed
policy is configured to evaluate that condition.

Do not fabricate a verdict when Gemini is unavailable. The judge guide should
say that the chain and capture path remain testable, while AI grading is marked
unavailable unless a real configured evaluator is active. If a deterministic
local policy mode is added later, it must be explicitly labeled as local policy
evaluation and must be tested as a separate provider.

The branch also exposes evaluator configuration state through `/v1/health`,
shows unavailable provider state in the dashboard, and stores provider fallback
results as `unknown` rather than clean.

### Remaining: do not claim ChatGPT judging yet

The backend currently implements Gemini evaluation. The desktop has an optional
OpenAI-compatible chat provider, but that is not the backend policy judge. Do
not market “Gemini + ChatGPT judge” until a backend OpenAI provider is actually
implemented, configured, tested, and deployed. The safe interim wording is
“optional Gemini policy evaluator; provider status is shown honestly.”

### Implemented: local diagnostic path

`foxy doctor` reports authenticated upload queueing, evaluator configuration,
local pet signals, and server-side chain verification without exposing secrets.

### P0: Deployment and origin must be proven

The public marketing origin and the application/API origin are separate. The
branch's migration and campaign code is not proof that the public site is
running it. Before sharing a redemption link:

1. Deploy this exact commit range.
2. Apply migrations `0035` and `0036`.
3. Configure the production database, API-key pepper, session secrets, mail
   provider, and evaluator settings through a secret manager.
4. Confirm CORS, TLS, cookie scope, dashboard origin, and SDK API origin.
5. Create a campaign from the staff console or configure one deployment-only
   code. Never commit or publish the plaintext code.
6. Redeem once with a disposable judge email and run the full test below.

No production secret, campaign code, or judge API key belongs in GitHub, README,
the video, browser screenshots, or client-side source.

### P1: Desktop pet positioning

The pet is a Foxy Audit desktop companion built with PyQt6. It is not an
official OpenAI Codex pet and should not be marketed as one. It reacts to local
SDK telemetry, real backend breach polling, and local machine signals. A
packaged, signed installer and a clean first-run setup are still required before
calling it a client distribution.

## Exact Judge Test Path

This is the minimum honest hands-on path. It uses no seeded evidence and does
not require the judge to own an LLM.

1. Open the non-secret redemption URL and enter the separately supplied code.
2. Use a disposable email, set the dashboard password, and save the one-time
   API key in a password manager. Do not put it in a public recording.
3. Install the SDK from the exact release commit.
4. Set `FOXY_API_KEY` and the deployed API base URL.
5. Run `demo/judge_client.py`, which calls a local deterministic function
   decorated with `@foxy.audit`. This creates real hash-only events.
6. Confirm the desktop pet shows local hashing/queueing, not a fabricated clean
   verdict.
7. Open the dashboard and confirm the new record, remaining allowance, chain
   status, and evidence boundary.
8. Run the verifier or dashboard verification action and compare the result with
   the ledger record.
9. If a real evaluator is configured, wait for its recorded verdict and verify
   the breach path using a policy-approved test input. If it is not configured,
   confirm the UI says unavailable and continue testing capture, chain, and
   allowance only.
10. Exhaust or intentionally lower the campaign allowance in a disposable
    campaign and confirm the next capture receives `402` without creating a
    ledger row.
11. Revoke a disposable campaign from the staff console and confirm new
    redemptions stop while already issued access follows the documented policy.

## OpenAI Build Week Submission Checklist

The current repository does not prove that all submission requirements are
complete. Treat every unchecked item as a release blocker. Confirm the current
event page immediately before submitting because deadlines and instructions are
time-sensitive.

| Requirement | Status | Required action |
|---|---|---|
| Build with required OpenAI developer tools | Partial | Preserve dated Codex/GPT-5.6 work evidence and describe exactly what was built or extended during the event. |
| Select one category | Missing | Choose one category, most naturally Developer Tools, and use the same category everywhere. |
| Public demo video under the event limit | Missing | Record the real redemption, SDK, pet, dashboard, and verifier flow with audio. No seeded data or fake provider output. |
| Public or permitted private code repository | Completed in source | Root MIT license is present; confirm repository access permissions before submission. |
| English submission materials | Partial | Prepare the final English project description, setup steps, architecture, and limitations. |
| Judge setup path | Partial | Deploy this branch, create a bounded campaign, and publish only the redemption URL plus separately delivered code. |
| Working intended platform | Partial | Run the deployed SDK-to-API-to-dashboard-to-verifier test and capture the output. |
| Third-party integrations | Partial | Document Gemini, Stripe, email, and chain dependencies and ensure production credentials/terms are valid. |
| Honest claims | Open | Remove unsupported “complete capture,” “ChatGPT judge,” “blockchain secured,” “certified,” and “clean” claims unless proven. |

Official references: [OpenAI Build Week rules](https://openai.devpost.com/rules),
[official FAQ](https://openai.devpost.com/details/faqs), and [OpenAI Build Week
overview](https://openai.com/build-week/).

## Priority Implementation Order

### Phase 1: Deployed redemption and evidence flow

- Deploy migrations `0035` and `0036` with this branch.
- Configure a finite campaign and test redemption, Premium allowance, API-key
  provisioning, dashboard visibility, and `402` enforcement.
- Validate marketing-site redemption UX, dashboard origin, CORS, email delivery,
  password setup, and one-time key handling.
- Capture real command output and dashboard evidence for the judge guide.

### Phase 2: Client distribution

- Build and test a signed desktop installer for supported operating systems.
- Make first-run API-key and backend URL setup explicit and recoverable.
- Add CI coverage for SDK, bridge, redemption, allowance, and verifier paths.

### Phase 3: Provider maturity

- Implement a backend OpenAI judge only if it improves the product and can be
  operated with real credentials, rate limits, timeouts, retention controls, and
  provider status.
- Keep deterministic policy checks separate from AI evaluation and label each
  verdict source.
- Make fail-open/fail-closed behavior an explicit organization setting and show
  evaluator-unavailable records distinctly.

### Phase 4: Submission and customer proof

- Record the real under-limit demo video.
- Publish the architecture diagram and judge runbook.
- Submit the repository, category, description, limitations, and live URL.
- Run a design-partner pilot before making regulated-industry outcome claims.

### Phase 5: Enterprise readiness

- Add SSO/SAML, SCIM, customer-managed retention, export/deletion workflows,
  backup restore drills, incident alerting, and support ownership.
- Complete Stripe test/live webhook validation and publish the product catalogue
  and credit semantics.
- Configure and independently verify public anchoring only if the commercial
  plan promises it.

## Readiness Gate

Foxy Audit can be called **judge-ready** only when Phases 0 and 1 pass with real
records and a deployed commit. It can be called **client-ready** only after the
desktop distribution, support/security controls, payment lifecycle, and restore
procedures are tested. At the assessment date, it is a strong working prototype
with a real access-control branch, not yet a verified production service.
