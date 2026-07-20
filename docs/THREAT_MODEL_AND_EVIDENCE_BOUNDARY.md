# Threat Model and Evidence Boundary

This document defines what Foxy Audit can demonstrate and what it cannot. It is
part of the product contract for customers, auditors, and judges. Foxy Audit is
cryptographic compliance evidence, not a certification and not a guarantee that
an AI system is safe.

## System boundary

The deployed system has four relevant trust zones:

1. **Customer host:** the customer's application, LLM client, model provider
   connection, and the Foxy SDK. The raw prompt and response exist here because
   the customer's application needs them. The SDK hashes them in-process and
   sends hash-only telemetry after the wrapped function returns.
2. **Foxy API:** authenticates the customer's API key and accepts hashes plus
   metadata. It never receives the raw prompt or response from the SDK contract.
3. **Foxy database and worker:** stores the sequential ledger, metadata, policy
   configuration, usage rollups, and the AI judge verdict. The worker sends only
   structural metadata to Gemini: opaque hashes, token count, policy tag, local
   PII signal categories, policy configuration, and a seven-day aggregate history.
4. **Optional public anchor:** when explicitly configured and successfully
   confirmed, the service publishes a chain root to an external EVM contract.
   The anchor is an additional external timestamp/integrity signal, not a
   substitute for the ledger or a claim that the underlying interaction was safe.

The desktop pet receives a best-effort local UDP status signal. It is a user
interface and is not an evidence source.

## Evidence flow

```text
raw prompt/response
        |
        | customer host: SHA-256 + local PII pattern detection
        v
hashes + metadata ---------------------> Foxy API
                                              |
                                              v
                                     sequential hash chain
                                              |
                       +----------------------+---------------------+
                       |                                            |
                 verifier/export                              optional anchor
                       |                                            |
                 auditor evidence                      public-chain receipt
```

The backend recomputes each chain link from the stored row fields and the
previous link. A changed historical row causes a verification mismatch from the
changed sequence onward. A confirmed public anchor adds an independently held
chain-root checkpoint; without that anchor, the database remains the authority
for the stored chain and a privileged database operator could rewrite and
recompute it.

## Security goals

Foxy Audit is designed to provide these properties when the SDK, API key, TLS,
database controls, and deployment are operated correctly:

- **Content minimization:** the Foxy API and worker do not need the raw prompt or
  response to create a ledger record or produce the current metadata-only judge
  verdict.
- **Modification evidence:** a modification, deletion, insertion, or reorder in
  the stored chain is detectable by verification, provided the attacker cannot
  also replace the trusted verification root or the external anchor receipt.
- **Tenant isolation:** API-key authentication, explicit organization filters,
  and database row-level controls prevent one customer from reading another
  customer's ledger through the customer API.
- **Attributable records:** the chain binds organization, sequence, hashes,
  token count, policy tag, and model/agent attribution when supplied. It does not
  prove who wrote the original model response.
- **Operational transparency:** failed grading, stale worker state, and anchor
  failures are represented as states that operators can inspect instead of being
  presented as successful evidence.

## What the evidence proves

For a verified record, an auditor can establish that:

- the presented prompt and response hashes match the values committed for that
  sequence, if the auditor independently recomputes those hashes from the
  original content;
- the record's committed metadata is consistent with its preceding and
  following chain links;
- the presented export matches the chain verification result at the time of the
  check;
- a confirmed public anchor matches the recorded chain root and sequence, when
  an external anchor is actually present and independently checked;
- the SDK's local PII detector reported the stored categories before the event
  was submitted, if those categories are present in the record.

These are integrity and provenance signals for captured events. They are not a
certification of the customer's model, prompts, responses, or organization.

## What the evidence does not prove

Foxy Audit must not claim any of the following from a clean chain alone:

- **Complete capture:** an uninstrumented model call, a failed decorated call,
  a disabled SDK, a crashed host, or a dropped best-effort dispatch may have no
  record. The current SDK records after a successful wrapped function return and
  its telemetry upload is asynchronous.
- **Safe content:** the server cannot inspect content it never receives. Hashes
  prove equality with content supplied later, not that the content was benign,
  lawful, accurate, non-toxic, or free of secrets.
- **Confidentiality of a low-entropy input:** plain SHA-256 is not encryption.
  An attacker who already suspects a short prompt or response can hash guesses
  and compare them. Customers must still protect the original content and API
  key.
- **Model authorship or causality:** a hash and an optional `agent` label do not
  prove which model generated a response, that the model was called only once,
  or that the response caused a later business action.
- **AI-judge correctness:** the Gemini result is an advisory classification over
  metadata and policy settings. It does not see the text and is not a human
  audit, legal opinion, or regulatory determination. Unavailable-judge behavior
  is visible through the configured fail-open/fail-closed policy.
- **Immutability without an independent root:** a database administrator with
  sufficient access can rewrite both rows and locally recomputed hashes unless
  an independently trusted export, root, or public anchor exists.
- **Deletion or erasure of all related data:** hashes, metadata, usage records,
  access logs, and backup copies can remain subject to the deployment's retention
  and legal policies. A workspace soft-delete is not automatically proof of
  cryptographic erasure.

## Threats and controls

| Threat | Current control | Residual risk / required customer action |
| --- | --- | --- |
| A Foxy API operator reads raw prompts | Raw text is not part of the SDK payload or worker judge input | The customer host, LLM provider, logs, traces, and debugger may still contain raw text |
| An attacker modifies a ledger row | Sequential links and `/v1/verify` detect chain divergence | Use independent exports or confirmed public anchors for stronger external evidence |
| A customer API key is stolen | Keys are stored hashed server-side and can be rotated/revoked | Treat the key as a secret; rotate immediately after exposure and scope access by environment |
| A compromised customer host changes the SDK | SDK runs inside the customer's trust zone | Foxy cannot prove calls made before instrumentation, after bypass, or by a modified host |
| A network attacker intercepts telemetry | Production deployment must use TLS and restricted CORS | Verify the deployed certificate, endpoint, and API-key handling; never use HTTP in production |
| Gemini is unavailable or gives an invalid result | Worker retries, dead letters, and configured fallback behavior | Do not treat an unavailable or fail-open verdict as a clean substantive review |
| A public anchor is missing or stale | Anchor status is visible and operational alerts exist | Describe anchoring as optional until a real provider and receipt are confirmed |
| Hash equality leaks a guessed short input | SHA-256 is transparent and independently verifiable | Do not use hashes as a replacement for encryption or raw-data access controls |

## Customer deployment requirements

Before using Foxy Audit for regulated workloads, the customer should:

1. Decorate every model boundary and test exception, retry, timeout, streaming,
   and fallback paths. Maintain an independent application metric for total model
   calls and compare it with Foxy captured records.
2. Keep raw prompt/response data inside the customer's approved environment and
   prevent application logs, traces, crash reports, and analytics from copying it
   unintentionally.
3. Store the Foxy API key in a secret manager, use separate keys per environment,
   and rotate keys during incident response.
4. Treat the policy tag, PII categories, token count, model label, and timestamps
   as potentially sensitive metadata even though the raw text is absent.
5. Export and independently verify the ledger at the customer's audit boundary.
   Configure and test public anchoring only when an external checkpoint is part
   of the customer's evidence requirement.

## Foxy operator release checks

The Foxy operator must be able to show, using the deployed system rather than a
mock response:

- a real SDK event whose request contains hashes but no raw text;
- the event in the customer dashboard and a successful chain verification;
- a failed or unavailable grader represented as pending, failed, or configured
  fail-closed rather than silently marked as a successful content review;
- a finite judge offer that stops new capture at expiry or credit exhaustion;
- the actual anchor provider, chain, transaction, or an honest `not configured`
  state;
- the deployment's migration version, backups, TLS, secret rotation, and alert
  checks before production claims are made.

Any marketing, sales, or hackathon statement should use the narrower terms
**hash-only telemetry**, **tamper-evident chain**, **metadata-only AI judge**, and
**optional public anchoring** unless a stronger claim has been independently
tested and documented.
