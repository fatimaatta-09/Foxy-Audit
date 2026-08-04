# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

**Primary: the engineer who installs the SDK.** Mid-sprint, adding audit to an AI
feature. Their job is to make every model call recorded without slowing the app
down or leaking anything. They add `@foxy.audit(policy, mode, agent)` to a model
call and then live in the dashboard's Home, Ledger and Verify pages, mostly
asking *"is capture actually working, and can I see the gaps?"* Success for them
is one line of integration and no operational surprise.

Two further audiences are real and must keep working, but do not outrank the
engineer when the two conflict:

- **The compliance officer**, who does not write code and has to answer for the
  system. Lives in the Compliance Passport, Verify, Policy and Export. Their job
  is producing something defensible to an auditor.
- **The external auditor or regulator**, who never logs in. Receives an export or
  a Passport and must be able to check it **without trusting Foxy**. The
  standalone verifier and the export bundle exist for this person specifically.

Internally, Foxy staff operate a separate admin console (viewer < operator <
superadmin) — an operations surface, not a customer one.

## Product Purpose

Foxy Audit produces **tamper-evident evidence that an AI system behaved as
claimed**, for organisations in regulated industries (healthcare, finance,
legal) who must answer for what their models did.

A Python SDK computes commitments locally and ships only bounded metadata to a
per-tenant, hash-chained ledger. Customers export that ledger and verify it
themselves. Success is an auditor accepting the evidence **without having to
trust Foxy**, and an engineer never having to think about it after the first
line.

## Positioning

**Two claims that are one mechanism: we cannot see your data, and you can prove
we did not alter it.**

Neither half is worth much alone — privacy without verifiability is a promise,
and verifiability without privacy is surveillance. Together they are hard for a
neighbouring product to copy truthfully, because each requires giving something
up:

- **Content-blindness.** Raw prompts and responses never leave the customer
  process. Only customer-keyed HMAC-SHA-256 commitments and bounded metadata
  reach the server; there is **no raw-text column anywhere** in the schema, and
  the AI judge grades metadata alone and is told so in its system instruction.
- **Independent verifiability.** Every event is bound into a per-org SHA-256
  hash chain that a stranger can recompute offline from an export, using a
  dependency-free verifier **shipped inside the export bundle itself**. The
  chain head can optionally be anchored to a public EVM chain.

The claim is deliberately **falsifiable**: the export contains everything needed
to prove Foxy wrong.

## Operating Context

Three web surfaces plus two non-web deliverables:

| Surface | For |
|---|---|
| `foxy-dashboard/` — app.foxyaudit.tech | the customer: capture coverage, ledger, verify, policy, export, billing |
| `foxy-adminpage/` — admin.foxyaudit.tech | Foxy staff operations, 13 pages, role-gated |
| `foxy-sale-page/` — foxyaudit.tech | 23 pages: marketing, pricing, legal |
| **Compliance Passport** | a PDF handed to an auditor; carries the chain head and how to check it |
| **Export bundle** | `foxy-audit-export.zip` — the ledger, the verifier, and instructions, together |

Plus a PyQt6 desktop companion (`desktop/`, internal name `omni_fox`) that
mirrors the dashboard and reacts to live audit events, and a public trust badge.

**The engineer's real scene:** the SDK sits in their request path. It must never
block on Foxy — a durable SQLite spool absorbs outages — and in `block` or
`redact` mode it runs a local policy check *before* the model call.

## Capabilities and Constraints

- **Enforcement happens locally, before the model call.** `mode="block"|"redact"`
  runs the SDK's own policy check (PHI/PII, secrets, prompt-injection) and
  blocks or redacts *before* the request leaves; otherwise it records after.
- **The chain is versioned and append-only.** V1–V4 all still verify; V4 binds a
  `verdict_hash` so the **local, deterministic** verdict is tamper-evident. The
  AI judge's verdict is advisory and deliberately **not** chained — it does not
  exist when the hash is computed.
- **The judge is per-tenant BYOK** (Gemini and/or GPT-5.6), keys encrypted at
  rest under a KEK. **Losing the KEK makes every stored key unrecoverable.**
- **Tenant isolation is enforced in Postgres** via row-level security, not only
  in application code.
- Plans: free (500 events/mo), pro (25k), max (250k), premium/guardian
  (unlimited by contract). Customer roles and a separate staff role ladder.
- **Undecided:** `require_card_on_file` ships `false`; turning it on is a
  business decision, not a deploy.

## Brand Commitments

- **Name and voice:** Foxy Audit. Plain, specific, measured. The product's own
  writing avoids hedging and states figures.
- **The fox** is the identity — including a desktop companion character.
- **Warm orange** is the primary colour across all surfaces.
- **No emoji as UI marks** — the sale page states this rule for itself.

## Evidence on Hand

**Real:** a working hash chain with an independently runnable verifier; the
export bundle; Compliance Passport generation; a published PyPI SDK; live
deployments of all three web surfaces; optional Sepolia anchoring.

**Deliberately absent, and future work must not fabricate it:**

> There are **no customers, testimonials, case studies, logos or usage figures**
> to cite. `foxy-sale-page/reviews.html` states this in the product's own words —
> *"We're a trust product, so we won't fake this… No invented quotes, no
> stock-photo customers"* — and commits that real reviews will appear **attributed
> and verifiable**. Design partners are being onboarded now.

This is the project's hardest content rule: **no fake or placeholder data, in
code or UI. Honest empty states only.**

## Product Principles

1. **The claim must be falsifiable.** Ship the means to prove us wrong with the
   evidence itself — that is why the verifier travels inside the export.
2. **Evidence outranks convenience.** A customer can recover from a declined
   card; they cannot recreate the calls their agents made yesterday. Capture
   keeps working through billing failures.
3. **Never see what we do not need.** Content-blindness is structural, not a
   policy — there is no column to leak.
4. **A confident wrong answer is worse than a visible failure.** An empty table
   under a header claiming rows, a status light that is always red, a
   "loading…" that never resolves — each is worse than an error.
5. **Say what is not known.** Unverified stays unverified; absent evidence is
   stated rather than invented.

## Accessibility & Inclusion

No customer-specific standard has been set. In practice the project holds itself
to WCAG AA for text and 3:1 for UI components, measured rather than asserted —
contrast is recomputed from token values in the test suites on both the dashboard
and the admin console, and a **fill is measured against its background, not only
its ink**.

**Known gap:** no `@media (forced-colors)` support on the admin console, where the
design language is shadow-based and several controls carry no border.
