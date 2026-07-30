# Foxy Audit — Master Overview
*Purpose, novelty, usage, verification, and current status — one document, everything current as
of this review.*

---

## 1. Why this exists (the actual problem)

AI startups selling into regulated industries — healthcare, finance, legal — lose enterprise deals
because they can't prove their AI workflows don't leak sensitive data. A hospital or bank's
compliance team asks "show us your AI isn't leaking PHI/PII into your LLM calls," and most startups
have no answer except "trust us." A full third-party security audit costs roughly $50,000 and takes
months — too slow and too expensive for a small team mid-sales-cycle, and it doesn't even solve the
underlying problem: they still can't prove ongoing compliance after the audit ends.

## 2. Who it's for

Small-to-mid AI/SaaS startups (roughly 5–50 engineers) actively selling into regulated buyers, stuck
in procurement because their prospect's compliance team wants continuous proof, not a one-time
audit or a verbal promise.

## 3. What it actually does

1. A one-line SDK decorator (`@foxy.audit(policy="hipaa_basic")`) wraps an existing LLM call.
2. The SDK creates a customer-keyed HMAC commitment of supported prompt and response values **locally, on the developer's own
   machine** — the raw text is discarded immediately and never transmitted anywhere.
3. Only commitments, a token estimate, a policy tag, event identity, and bounded operational metadata are sent onward. The local SQLite/WAL spool retries failed uploads.
4. Deterministic local rules grade evidence-supported metadata anomalies. An optional Gemini judge can add metadata-level analysis; unavailable evaluation is returned as unknown, not clean.
   the metadata for anomalies — unusually high token counts, prompt-injection patterns — and returns
   a verdict.
5. Each event is chained sequentially using a versioned canonical representation of its identity, commitments, metadata, and previous chain hash.
   Altering any historical row breaks every hash after it — an "avalanche effect" that makes
   tampering mathematically detectable, not just policy-forbidden.
6. The chain head can be anchored to a public blockchain (Sepolia), so tampering becomes checkable
   by an outside party — not just re-verifiable inside Foxy's own database.
7. A standalone, dependency-free verifier script lets anyone — auditor, buyer, skeptic — recompute
   and confirm the whole chain themselves, without trusting Foxy's servers at all.
8. A one-click "Compliance Passport" PDF export turns this into something a compliance officer can
   hand directly to an enterprise buyer's procurement team.

## 4. The actual novelty — say this exact sentence in every pitch

**Almost every competing AI governance / LLM observability tool works by ingesting your raw prompts
and responses into their own cloud to scan them — which means the compliance tool itself becomes a
new place sensitive data can leak.** Foxy Audit inverts that architecture completely: it
**cannot** see the raw data, not merely promises not to look at it. That's the difference between
"trust our access controls" (every competitor) and "there's nothing here to trust — verify it
yourself" (Foxy Audit). This is content-blind capture by architecture, not a formal zero-knowledge
proof. The current system proves integrity of submitted commitments and metadata; it does not prove
semantic safety of content that the evaluator never receives.

## 5. How to use it (developer's actual workflow)

```python
import os
from foxy_audit import FoxyClient

foxy = FoxyClient(api_key=os.getenv("FOXY_API_KEY"))

@foxy.audit(policy="hipaa_basic", agent="gpt-4o")
def call_llm(user_prompt: str):
    return real_llm_call(user_prompt)
```
That's the entire integration. Everything else — hashing, chaining, grading, storage — happens
automatically in the background, non-blocking, so it never adds meaningful latency to the host
application.

For the desktop pet: it runs alongside the developer's work, listening on a local UDP bridge, and
reacts visually (green/red) in real time as interactions are graded — giving instant ambient
feedback without needing to check a dashboard.

### Current evidence boundaries

The system is content-blind, not a formal zero-knowledge proof. It proves the integrity and
continuity of submitted commitments and metadata; it cannot prove semantic safety of content that
the evaluator never receives. The default upload path is asynchronous and durable, while
`audit_required=True` waits for a server receipt. Foxy Audit supports compliance evidence work but
is not legal advice, a certification, or an absolute compliance guarantee.

## 6. How to verify it's actually working as intended (not just running)

Running is not the same as working. These are the specific tests that prove the actual claims,
not just that the software executes:

**Test A — raw data never leaves the machine.**
Put a proxy (Burp Suite, Wireshark) between the SDK and the backend during a real call. Inspect the
outbound payload. **Pass condition:** only a hash, token count, and policy tag appear — never the
actual prompt/response text. If plaintext appears, the core claim is false.

**Test B — the AI Judge actually judges, not just rubber-stamps.**
Send one benign interaction and one clearly anomalous one (e.g., an injection-style prompt or an
artificially huge token count). **Pass condition:** the verdicts differ meaningfully, and the
`reason` field is specific to what happened, not boilerplate. If every event gets the same verdict
regardless of content, the judge isn't really judging.

**Test C — the hash chain and tamper detection (the single most important test).**
Export the ledger, run the standalone verifier — confirm it reports the chain intact. Then
**deliberately corrupt one historical row** (change one character in its stored hash) and re-run the
verifier. **Pass condition:** it must flag that exact row as tampered, and everything chained after
it as invalid too. If it still reports "intact" after your edit, the entire premise of the product
is broken. This was run live and confirmed working — see `foxy_live_demo.py` and
`foxy_demo_ledger.json` from this conversation for a reproducible, real terminal transcript of this
exact test passing.

**Test D — public anchoring is real, not just implemented-but-off.**
If anchoring is enabled, verify the chain head against the actual public chain (Sepolia), not just
against what Foxy's own database claims — this is the difference between "we say it's anchored" and
"you can independently confirm it."

**Test E — multi-tenant isolation.**
Seed two separate organizations. Using org A's credentials, attempt to retrieve org B's data through
every available route, including raw query paths. **Pass condition:** always rejected — never
returns another org's data.

**Test F — the one-command sanity check.**
Run `foxy doctor` — a single CLI command that checks backend connectivity, desktop pet detection, and
chain verification in one shot. If this passes, the basic wiring between all three tiers is
confirmed end to end without needing to run each test manually.

## 7. What is genuinely done as of this review

Verified directly against the actual code (not just documentation claims):

- **Local hashing and chaining logic** — real, correct, confirmed via live execution in this
  conversation, including tamper detection actually catching a deliberately corrupted row.
- **Real signup-to-credentials flow** — Stripe webhook now creates the API key and user account and
  sends a real email; this used to be broken (webhook created an org with no usable login) and is
  now fixed.
- **Row-Level Security genuinely enforced** — a non-superuser application role
  (`db_app_role: foxy_app`) is used at runtime via `SET LOCAL ROLE`, replacing an earlier setup where
  a superuser connection silently bypassed all tenant isolation.
- **Admin IP allow-list fails OPEN by design** — an empty allow-list *allows* all requests (with a
  loud one-time prod startup warning); staff login + MFA is always required regardless. Set
  `ADMIN_IP_ALLOWLIST` to office/VPN ranges to restrict by IP. It is deliberately not fail-closed so a
  dynamic operator IP can never lock every admin out.
- **The desktop pet's breach reaction is real** — it polls an actual `/v1/logs/breaches` endpoint
  rather than firing before any real grading has happened.
- **The desktop "Auditor Console" tiles pull real data** — polling `/v1/stats` and `/v1/verify`,
  rather than the earlier version, which synthesized a self-healing fake compliance score locally.
- **Legal pages exist** — `terms.html` and `privacy.html` are real, substantive documents, not
  placeholders — this used to not exist at all.
- **The sales-page checkout is real** — it calls an actual `/v1/billing/checkout-session` endpoint,
  replacing an earlier `setTimeout`-driven fake success animation.
- **SDK dead code removed** — the unused, disconnected `transport.py` path is gone.
- **A one-command health check (`foxy doctor`) exists** — checks backend, pet, and chain
  connectivity in a single call, exactly the kind of thing that turns four previously-disconnected
  systems into one visible confirmation.
- **A standalone, dependency-free verifier script exists and works** — anyone can independently
  recompute and confirm the hash chain without trusting Foxy's own servers.

## 8. What is still open (honestly, as of this review)

- **Packaging/installers don't exist yet** — no PyInstaller spec, no signed Windows exe, no notarized
  macOS build, no Linux AppImage. This is real, separate work (a full prompt for Claude Code to
  execute this was provided earlier in this conversation).
- **The SDK isn't actually published to PyPI yet** — `pip install foxy-audit` in the README doesn't
  currently work for an outside user; it needs to be tested cleanly and then published.
- **Secret rotation status is unconfirmed** — `.env.example` correctly shows only placeholders (as
  it should), but whether the previously-flagged live secrets (Brevo key, Stripe keys, and
  especially the funded Sepolia anchoring key) were actually rotated is an operational fact only the
  team can confirm, not something visible from the repo alone.
- **OpenTimestamps/Bitcoin anchoring was removed rather than finished** — this is the honest
  resolution (a test explicitly confirms its removal) rather than shipping a half-working feature,
  but it means anchoring today is EVM/Sepolia-only.
- **Windows/macOS native builds still need real code-signing and notarization credentials**, which
  only the team can provide — no amount of automation substitutes for an actual purchased
  certificate and Apple Developer ID.

## 9. Running it from source (developer / internal team)

```powershell
# Backend
cd backend
docker compose up --build -d
docker compose logs foxy-seed          # copy the API key printed here

# Desktop pet (separate terminal)
cd ..\desktop
pip install -r requirements.txt
python omni_fox.py

# SDK (separate terminal)
cd ..
pip install -e .\sdk
$env:FOXY_API_KEY = "paste-key-here"
```
This is the full stack running locally — backend, desktop pet, and SDK all talking to each other.
Nothing here requires the packaged installer; this is how the team itself should keep testing
during development.

## 10. Building the client-facing installers (once the team is ready to ship)

The desktop pet is PyQt6, which is genuinely cross-platform, but needs a **separate build per OS** —
there is no single binary that runs everywhere:

| OS | Tool | Output | Requirement to ship safely |
|---|---|---|---|
| Windows | PyInstaller (`--onefile --windowed`) | `.exe` + Inno Setup installer | Code-signing certificate, or SmartScreen flags it as untrusted |
| macOS | PyInstaller or `py2app` | `.app` in a `.dmg` | Apple notarization, or Gatekeeper blocks it outright |
| Linux | PyInstaller + AppImage | portable executable | No signing required — lowest-friction target |

Build all three from one GitHub Actions matrix workflow (`windows-latest`, `macos-latest`,
`ubuntu-latest`) triggered on version tags, so this never needs to be done by hand. The SDK ships
far more simply once published: `pip install foxy-audit` is OS-agnostic by nature.

**Recommended sequencing:** publish the SDK to PyPI first (cheap, immediate value), keep onboarding
as "run the pet from source" for your first few real design partners, and only invest in signed
installers once real users have actually hit the friction of "I don't want to run Python" —
building signed installers before that point solves a problem you don't have yet.

## 11. The client dashboard — what it's for and how it should feel to use

The dashboard is the browser-based "same data, different view" of everything the desktop pet shows
locally:
- **Ledger view** — a chronological feed of every graded interaction: timestamp, verdict, hash —
  never raw text.
- **Verify button** — recomputes the hash chain client-side (Web Crypto API) and flashes
  "MATCH (Verified)" or "TAMPERED" — this is the browser equivalent of the standalone verifier
  script, meant for a non-technical compliance officer to click without touching a terminal.
- **Policy configuration** — toggle which defenses are active (e.g., "Block PII," "Flag
  Injections") without needing a developer to redeploy anything.
- **Compliance Passport export** — the one-click PDF a compliance officer hands directly to an
  enterprise buyer's procurement team.
- **Keys/Settings** — API key management and account configuration.

**How to confirm the dashboard is actually working, not just displaying demo data:** log in, run a
real interaction through the SDK (Section 9), and confirm the exact same event appears in the
Ledger view within a few seconds — same verdict, same hash, as what the desktop pet showed locally.
If the dashboard shows something different from what actually happened, or shows generic/static
numbers regardless of real activity, that's a sync bug, not a working dashboard.

## 12. What a client actually experiences, end to end (once packaged)

1. Downloads the signed installer for their OS from your site.
2. Runs it — the desktop fox appears, prompts once for their API key (from the welcome email sent
   automatically after signup/checkout).
3. Adds one line (`@foxy.audit(...)`) to their own AI code, or hands it to their dev team.
4. Sees the fox react in real time as their app runs — green for clean, red with a plain-English
   reason for a real policy breach.
5. Logs into the web dashboard (same account, same data) whenever they want the fuller picture, or
   to generate a Compliance Passport for a buyer.
6. Can, at any point, export the ledger and run the independent verifier themselves — or hand it to
   their own security team — to confirm nothing has been altered, without ever needing to trust
   Foxy's servers.

That last point is the actual product: not "a dashboard that shows logs," but "a system you don't
have to take on faith."

## 13. The bottom line

The core cryptographic claim — that tampering with any historical record is mathematically
detectable — is real, verified, and reproducible; this document links directly to a live transcript
proving it. The remaining work is packaging, credentials, and go-to-market, not core engineering
risk. What's genuinely rare here, and worth saying plainly in any pitch: most products at this stage
either overclaim what they've built or under-document what's actually true — this project's own
internal status docs have consistently done neither, which is itself part of why the trust story
holds up under scrutiny.
