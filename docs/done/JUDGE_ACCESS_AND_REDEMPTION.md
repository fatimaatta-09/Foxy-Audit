# Judge Access and Redemption

This branch adds a real, time-limited judge evaluation offer. It does not add
sample evidence, bypass authentication, or make a public code reusable without
limits.

## What a judge receives

- A non-secret redemption link: `https://foxyaudit.tech/?redeem=judge`.
- A separately shared evaluation code.
- A newly provisioned Premium workspace, a one-time SDK API key, and a password
  setup email.
- The normal setup path for the SDK, dashboard, and desktop companion.

The offer is 2,000 Foxy audit-event credits for 30 days by default. One submitted
prompt/response record consumes one Foxy audit-event credit. These are not
OpenAI, Gemini, or other model-provider tokens. There is no card on file and no
automatic charge.

For ongoing judge, partner, or customer-specific campaigns, a superadmin can
create an evaluation campaign from the admin console. The code is HMAC-hashed
at creation time and is returned only in that creation response; the Campaigns
page and database never expose it again. Viewers can see status, capacity, and
redemption counts. Operators can revoke new redemptions without shortening
already-issued trial access. The original environment-configured offer remains
available as a migration-safe fallback.

## What happens when access ends

Capture is a hard stop: once the offer expires or its event allowance is used,
`POST /v1/logs/batch` returns HTTP 402 and writes no new records. Existing audit
records remain visible, exportable, and independently verifiable. The product
never deletes or fabricates evidence to enforce an evaluation limit.

## Deploy the offer safely

Set these deployment-only variables. Never commit the code, put it in the
redemption URL, or show it in a public demo video.

```env
JUDGE_OFFER_CODE=<high-entropy-secret-code>
JUDGE_OFFER_ID=devpost-2026-judges
JUDGE_OFFER_CREDITS=2000
JUDGE_OFFER_DAYS=30
JUDGE_OFFER_MAX_REDEMPTIONS=25
```

An empty `JUDGE_OFFER_CODE` disables redemptions. The database stores only an
HMAC-derived email fingerprint for one-redemption-per-email enforcement, never
the code or the email in the redemption record. Invalid, reused, and exhausted
codes intentionally return the same response so the public signup route does
not disclose campaign activity.

Before sharing the link:

1. Apply migrations `0035_judge_evaluation_offers` and
   `0036_evaluation_campaigns` to the deployed database.
2. Either configure the variables in the hosting provider's secret manager, or
   create a campaign in the admin console as a superadmin. Deploy this exact
   branch before sharing the link.
3. Redeem one disposable test email, use the returned API key to capture a real
   interaction, then confirm the record in the dashboard and `/v1/verify`.
4. Confirm the Billing page displays the finite allowance and that a capture
   beyond the allowance returns HTTP 402.
5. Share the link and the code through separate judge instructions. Revoke a
   database campaign from the Campaigns page, or disable the environment offer
   by clearing `JUDGE_OFFER_CODE`.

## Judge test path

1. Open the redemption link and enter the supplied code and an email address.
2. Save the one-time API key shown on the setup page and set a dashboard password
   from the email invitation.
3. Follow the SDK quick-start using a real prompt/response pair. Foxy Audit
   hashes the pair locally before it is submitted; do not use sensitive real data
   in a public evaluation.
4. Open the dashboard to inspect the resulting chain record and remaining event
   allowance.
5. Use the verifier or `GET /v1/verify` to confirm the chain. Export the ledger
   if an offline check is preferred.

Public-chain anchoring must be described as optional unless EVM anchoring has
been configured and tested against the deployed environment. The redemption
offer itself does not claim that anchoring is enabled.
