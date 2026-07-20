# Priority Implementation Plan

This is the remaining-work plan. Completed work is deliberately not listed as
pending.

## Completed on `feat/judge-redemption-access`

The judge evaluation offer is implemented on its own branch: non-secret
redemption link, server-side code validation, finite 2,000 event credits by
default, 30-day expiry, one redemption per email, capped campaign capacity,
Premium workspace provisioning, dashboard allowance visibility, and hard capture
enforcement. It has not been merged into `main` or deployed yet.

The threat model and evidence-boundary document is also complete. It records the
real hash-only data flow, the metadata-only judge boundary, the limits of
completeness and confidentiality claims, and the production checks required
before calling the system audit-ready.

Staff campaign management is also complete: viewer listing, superadmin
creation with a one-time plaintext code response, operator revocation, hashed
codes, redemption counters, finite capacity, and audited mutations. The
deployment-configured offer remains as a backward-compatible fallback.

## P0: required before a public or enterprise launch

1. Deploy migrations `0035` and `0036`, set production secrets, and run the real
   SDK-to-dashboard-to-verifier test described in
   `JUDGE_ACCESS_AND_REDEMPTION.md`.
2. Use a managed PostgreSQL service with backups, point-in-time recovery, least
   privilege database roles, and tested restore instructions.
3. Rotate all production secrets, configure domain/TLS, restrict CORS to the
   deployed customer origins, and protect the admin host with its dedicated
   cookie secret and an IP or VPN allow-list.
4. Configure real OpenAI and/or Gemini credentials only if their judge paths are
   implemented and tested in the deployed environment. Do not claim an AI judge
   is active from configuration alone.
5. Configure external anchoring only if a funded provider, contract, alerting,
   and end-to-end verification have been tested. Otherwise describe anchors as
   optional.

## P1: enterprise purchase readiness

1. Add SSO/SAML, SCIM provisioning, customer-managed retention controls, and
   export/deletion procedures with audit logs.
2. Convert billing tiers and credits into a signed product catalogue, then test
   Stripe Checkout and webhooks using separate test and production accounts.
3. Finish the admin ops roadmap, including health, dead letters, anchoring, and
   alert actions with integration tests.

## P2: product proof and go-to-market

1. Publish a short, real demonstration recorded against the deployed service:
   SDK capture, dashboard record, verifier result, and an honest anchor status.
2. Run design-partner pilots in a single regulated workflow at a time, measure
   time-to-evidence and audit-review effort, and turn confirmed outcomes into
   case studies only with customer approval.
