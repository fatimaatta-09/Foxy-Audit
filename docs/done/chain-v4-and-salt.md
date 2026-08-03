> ## Complete — 2026-08-04. Read this banner before the plan.
>
> | | Commit | What |
> |---|---|---|
> | H1 | `c2b2e6e` | chain V4 — the local verdict is bound and tamper-evident |
> | H2 | `2e924b9` | a per-event salt on top of the HMAC commitment |
>
> Backend **925 → 928** · SDK **→ 143** · verifier **→ 31** · one migration (0061,
> nullable, un-backfilled). CI and CD green on both.
>
> ### The brief was wrong about the problem, and that was the finding
>
> It asked to eliminate a "Privacy Paradox" in which the backend "sends the raw
> prompt to Gemini/OpenAI". **It cannot.** `worker._grade_one` builds the judge
> payload from hashes, token count, policy tag and pii_signals, and `AuditLog`
> has **no raw-text column at all**. The SDK already used customer-keyed HMAC
> rather than a bare digest, and `policy_engine` was already local-first for
> enforcement events.
>
> Built as written, the three "fixes" would have been **one no-op, one downgrade**
> (`SHA256(prompt+salt)` is length-extension vulnerable; HMAC exists to prevent
> exactly that) **and one security regression** — the literal formula
> `SHA256(prompt_hash + response_hash + verdict_hash + H_{n-1})` drops `org_id`
> and `seq`, making rows reorderable within a ledger and transplantable between
> tenants while still verifying.
>
> ### What the plan itself got wrong
>
> **The binding without the check.** The chain binds `verdict_hash`, not the
> verdict — so rewriting `local_verdict` in an export and leaving the digest alone
> still passed a pure chain recompute. As specified, V4 was **decorative**. The
> executor added a re-derivation of the digest from the exported body; proven at
> the gate on a real bundle, that tamper now exits 1 where it would have exited 0.
>
> **Five recompute sites the plan never named.** `verify.py` (×2), `anchor.py`
> (×2) and `passport.py` each rebuild a row's hash from its columns. Without
> `verdict_hash` threaded through, every V4 row would have read as **tampered**
> from `/v1/verify`, the Compliance Passport and the anchor cross-check.
>
> **A sidecar that could not be read.** The plan said the salt goes in "the
> sidecar the customer already owns" — but that file is a hand-written JSON object
> and the SDK can only append. Without the merge step H2 added, the round trip did
> not exist.
>
> ### Verified rather than reported
>
> **V1–V3 have not moved**: `origin/main`'s `chain.py` and the branch's loaded as
> separate modules, 144 historical inputs, byte-identical. **Commitments have not
> moved**: same technique on `hashing.py`, 52 (value, key) pairs with the salt
> omitted, unchanged. **Three implementations agree** — writer, verifier, and the
> vendored copy that ships to auditors — at every version.
>
> A golden-vector file written on a branch proves only that the branch agrees with
> itself. That is why both checks were run against the pre-change module.
>
> ### The gap H2 closed that nobody had asked about
>
> **CI never ran `pytest verifier`.** So `test_writer_and_verifier_agree_at_every_version`
> and `test_matches_backend_recipe` — the guards protecting three-way agreement on
> a hash chain — were not enforced anywhere automatic. H2 added the step.
>
> ### Open after this plan
>
> #59 (`verify_chain.py` has cried tamper since V2) · #60 (seven callers
> hand-enumerate every hashed column) · plus H2's own notes: the SDK's catch-all
> logs `str(exc)` (considered and deliberately left — narrowing it costs real
> debuggability, and no exception in that path carries the salt), `_record_salt`
> appends without locking, and `commitment_alg` is unconstrained at ingest.

---

# Bind the verdict into the chain, and salt the commitments

Planned 2026-08-04 against `main` @ `e8de6b6`.

## Context

The brief asked for three fixes to eliminate a "Privacy Paradox": salted hashing,
a local-first judge, and a verdict hash in the chain. **Three of its four premises
do not hold against this codebase**, and saying so is the useful part of this
plan — building all three as written would have meant one no-op, one downgrade,
and one security regression.

| The brief says | The code says |
|---|---|
| the backend "sends the raw prompt to Gemini/OpenAI" | **It cannot.** `worker._grade_one` (`worker.py:194`) builds the judge payload from `prompt_hash`, `response_hash`, `token_count`, `policy_tag`, `pii_signals` and event ids — nothing else. `AuditLog` has **no column** for prompt or response text. The server never receives it, so there is nothing to leak. |
| add `SHA-256(prompt + salt)` | The SDK already computes `HMAC-SHA-256(customer_key, canonical_json(value))` (`hashing.py:34`), with the key held client-side and never stored. Rainbow tables are already defeated, and naive `SHA256(prompt+salt)` is *weaker* — length-extension is exactly what HMAC exists to prevent. |
| refactor `policy_engine.py` to judge locally first | `backend/app/policy_engine.py` already exists and is already local-first for enforcement events: `worker.py:205` routes `blocked`/`redacted` to `policy_engine.evaluate_enforcement` and **never calls the judge**. The SDK also runs its policy check *before* the model call in `block`/`redact` mode. |
| bind a verdict hash into the chain | **True — this is the real gap.** `compute_chain_hash` enumerates every hashed field and no verdict appears in any version. |

### Owner decisions (asked and answered 2026-08-04)

1. **Build the verdict hash only.** The judge already sees no raw text; the
   policy engine is already local-first.
2. **Keep HMAC and add a per-event salt** on top of it — defence against an
   attacker who somehow learns the customer key.
3. **V4 = today's blob plus `verdict_hash`**, not the literal four-field formula.
   The literal version drops `org_id`, `seq`, `token_count` and `policy_tag`;
   without `seq` and `org_id` bound, a row could be reordered within a ledger or
   transplanted between tenants and still verify. The intent — the verdict is now
   tamper-evident — survives intact.

### The blocker the brief did not mention, and how it resolves

**The AI verdict does not exist when the chain hash is computed.** Ingest writes
the row and its hash synchronously (`routers/logs.py`); the judge grades
asynchronously afterwards, via the outbox. Hashing the AI verdict would mean
either re-hashing the row after grading — which invalidates every block after it
— or making ingest wait on a network call to an LLM.

`schemas.py:77-84` already documents this: *"the chain hash is fixed at ingest,
before any grade exists; the worker adds the verdict later with an UPDATE that
never touches `chain_hash`."*

**The verdict that gets chained is therefore the LOCAL, deterministic one.**
`policy_engine.evaluate` imports only `typing` and `.schemas` — no network, no
model, pure — so it can run synchronously at ingest. The AI judge's verdict stays
where it is: advisory metadata outside the chain. That is also the honest
division: the chain should bind what the system *decided*, not what a
non-deterministic model *opined* later.

---

## Phases

`H1` and `H2` are independent — different files, different surfaces — but both
touch the verifier, so they run **sequentially**.

| Phase | Branch | Scope |
|---|---|---|
| H1 | `feat/chain-v4-verdict` | V4: bind the local verdict into the chain |
| H2 | `feat/salted-commitments` | per-event salt on top of the HMAC |

Every phase: `git fetch origin && git worktree add ../wt-<phase> origin/main`.
Re-check `git merge-base --is-ancestor origin/main HEAD` **immediately before
pushing**, not at branch time. **Never spell the CI skip marker in a commit
body** — GitHub substring-matches the whole message and silently skips the deploy.

---

## H1 — Chain V4: the verdict is tamper-evident

### The formula

```
V4 event = { …every V3 field…, "verdict_hash": "<64 hex>" }
H_n      = SHA-256( canonical_json(V4 event) + H_{n-1} )
```

`verdict_hash = SHA-256(canonical_json(local_verdict))`, using the *same*
canonical form the chain already uses (`ensure_ascii=True, sort_keys=True,
separators=(",",":")`), so ordering can never move a hash.

### Files, and the order they must change

- **`backend/app/chain.py`** — add `CHAIN_VERSION_VERDICT_V4 = 4`; add
  `verdict_hash` to the event dict **only** under `chain_version >= 4`.
  **V1, V2 and V3 must stay byte-identical** — the file's own comment sets that
  rule ("Earlier V2 rows remain exactly byte-compatible so their historic hashes
  continue to verify") and every existing customer export depends on it.
- **`backend/app/routers/logs.py`** — call `policy_engine.evaluate(meta,
  policy_config)` before `compute_chain_hash`, hash the result, pass it in. The
  policy snapshot is already frozen here for V3; the verdict belongs beside it.
- **`backend/migrations/versions/0061_*.py`** — `local_verdict` (JSONB, nullable)
  and `verdict_hash` (String(64), nullable). **Nullable and un-backfilled**:
  existing rows are V1–V3 and must not acquire a field their hash never bound.
  Head is 0060 today; keep it linear.
- **`verifier/foxy_verify.py`** — mirror V4 at lines 43–65. **This file is a
  hand-written second implementation of the formula**, which `chain.py`'s
  docstring warns about; the two disagreeing *is* the classic hash-chain bug.
- **`backend/app/bundled/foxy_verify.py`** — re-copy. A byte-identity guard in
  `backend/tests/integration/test_export_bundle.py` fails until you do, and the
  bundle ships this file to auditors.
- **`backend/app/routers/logs.py`** — `_EXPORT_COLS` and `_export_row` gain
  `verdict_hash` and `local_verdict`. **Without this a V4 row cannot be verified
  from an export at all**, which would break the guarantee E2 just shipped.
- **`backend/app/schemas.py:77-84`** — that comment says no verdict field is
  hashed. V4 makes it false. Update it to draw the new line: the *local* verdict
  is chained, the *AI* verdict is not, and why.

### The judge is untouched

`worker.py` keeps writing `gemini_verdict` with an UPDATE that never touches
`chain_hash`. Do not chain it, do not make ingest wait for it.

---

## H2 — A per-event salt, on top of the HMAC

### What changes

`sdk/src/foxy_audit/hashing.py` — `commitment_hex(value, key, salt=None)`:
mix a fresh `secrets.token_hex(16)` per event into the HMAC input **canonically**
(e.g. `canonical_json({"v": value, "s": salt})`), never by naive concatenation.
`client.py:375` sets `commitment_alg = "hmac-sha256-salted"`; **`"hmac-sha256"`
and `"sha256-legacy"` keep their exact meaning** so every row already written
still verifies. That field exists for precisely this.

### Where the salt lives — the tension in the brief, resolved

The brief says the salt must never be stored in our database. It must also be
recoverable, or the customer permanently loses the ability to prove what a
commitment covers.

Both hold at once, because **the chain never needs the salt**.
`foxy_verify.py` recomputes `H_n` from the stored field values; it never
re-derives `prompt_hash` from plaintext. The salt is only needed for the
*optional* known-content check, which already runs off a **customer-owned
sidecar** — `--commitment-key` and `--events` (`foxy_verify.py:245-246`).

So: the salt is written **client-side only**, into the sidecar the customer
already owns, and never leaves their process. Nothing on our side changes.

- `verifier/foxy_verify.py` — `check_commitments` reads an optional per-event
  salt from the sidecar and applies it when `commitment_alg` says salted.
- **State the trade in the SDK docstring:** a customer who loses the sidecar
  loses known-content proof for those events. They keep chain verification, which
  is the tamper-evidence claim; they lose the ability to demonstrate *which*
  text a commitment covers.

---

## Verification

The command from the brief, plus the two that decide whether this is safe:

```bash
pytest backend/tests/test_chain.py -q          # the named gate
pytest verifier/ -q                            # the second implementation
pytest sdk/tests -q                            # H2

cd backend && DATABASE_URL=postgresql+psycopg://foxy:foxy@localhost:5433/foxy_pytest \
  python -m pytest tests/integration -q        # 928 after H1
```

The explicit `DATABASE_URL` is not optional — conftest defaults to 5432, Postgres
here listens on 5433, and without it every test dies inside `alembic upgrade
head` looking exactly like a migration fault.

**Three guards this work does not ship without:**

1. **Golden vectors for V1, V2 and V3.** Pin known `(inputs → chain_hash)` triples
   and assert they do not move. This is the only thing standing between a V4 field
   ordering mistake and every historical export failing to verify.
2. **Writer/verifier parity.** One test that feeds identical inputs to
   `chain.compute_chain_hash` and the verifier's copy at every version 1–4 and
   asserts equality. The two implementations exist for good reason and drift
   silently.
3. **A real round trip.** Export a V4 ledger as `format=bundle`, unzip it outside
   the repo, and run the shipped verifier against it — the same check that closed
   register #27. A V4 row that cannot be verified from a bundle has broken the
   product's central claim, and no unit test will say so.

**Quality gate — no salt, no key, in any log line.** Follow the existing
discipline in `gemini.py`, which logs the exception **type only** because the
provider key can appear inside `str(exc)`. Salts and commitment keys get the same
treatment: never in a log, never in an error `detail`, never in a traceback that
reaches a response.

---

## After each merge

1. `git push origin <sha>:refs/heads/main`. Pushing to `main` deploys.
2. Watch CD to green. If **no run appears at all**, check the commit message for
   the skip marker before suspecting GitHub.
3. Re-stamp `Backend`, `Database` and `Verifier` notes (`updated:`,
   `verified-against:`), and record the V4 formula in `Verifier\CLAUDE.md` —
   it is the note someone reads before touching the second implementation.
4. Dated entry in `Devlogs\2026-08-04.md`.
5. Update the root `CLAUDE.md` Alembic head after H1's migration.
