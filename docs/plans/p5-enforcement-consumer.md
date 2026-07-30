# P5 — giving `enforcement_mode` a real consumer

**Plan of record** · 2026-07-29 · MAIN chat is the committer; executors build per this file.
Base: `main` = `f6f5ed6`. Owner decision, 2026-07-29: **give the field a real consumer** —
not soften the copy, not remove the control.

---

## Why this exists

P4 made `confidence_threshold` real and shipped `sdk_enforcement` for pre-call enforcement.
`enforcement_mode` (`block | flag | monitor`) is still read by nothing, and the Policy page now
says so **in the product**. That copy is honest and it is also an admission that a shipped control
does nothing. This plan closes the gap by making the field do something, so the page can stop
apologising for it.

## The naming problem — read before designing anything

**`enforcement_mode` cannot block.** By the time it would be read, the model call has already
happened, the response is already back in the customer's process, and the judge is grading
*metadata* asynchronously in the worker. There is nothing left to block.

Pre-call enforcement already exists and is a different field: `sdk_enforcement`
(`observe | redact | block`), resolved in the SDK before the model is called, strictest-wins.
That one really does block. Two fields, two vocabularies, one of them lying about its own tense.

So the honest thing this field can control is **the response to a breach the judge finds**.
`block` in a post-hoc grading context can only mean "treat this as severe", and a control named
`block` that does not block is exactly the class of lie P4 spent three phases removing.

**This plan therefore has a copy decision baked into it, and the owner must make it** — see
"Owner decision required" below. Build nothing until it is answered.

## Where it plugs in — verified

`backend/app/worker.py:_grade_one` writes the verdict and then, at the *only* branch that already
reacts to severity:

```python
if verdict.policy_breach:
    org_notifications.enqueue_breach_notice(row, verdict)   # org-level
    user_notifications.enqueue_breach_alert(row, verdict)   # per-seat fan-out
webhook_delivery.enqueue_grading(...)                       # fires on EVERY graded row
```

That `if` is the seam. The verdict carries `policy_breach`, `decision`, `reason`, `risk_score`
(`org_notifications.py:63` already reads `risk_score`). Everything needed is in scope; no new query,
no new column, no migration.

**Do not put the check anywhere else.** Grading itself must stay identical across all three modes —
the verdict is evidence, it goes into the hash chain and the export, and a setting that changed the
*grade* would make two orgs' evidence incomparable. This field changes **what we do about** a
breach, never what the breach *is*.

## Phase A — the consumer

Resolution: the org's `enforcement_mode`, defaulting to today's behaviour when unset.

> ### ⚠ THE DEFAULT IS `block`, NOT `flag` — corrected 2026-07-30
>
> `models.py:297` carries `server_default="block"` and `policies.py:42` defaults the schema to
> `"block"`. **Every existing organisation is already stored as `block`**, not because anyone chose
> it but because it is the column default — the same fact that forced `sdk_enforcement` to be a
> separate nullable column in P4 §B.
>
> An earlier version of this plan asserted `flag` was the default and the majority case. It is
> neither. Building against that claim would have shipped a production incident: the compatibility
> test would have guarded the wrong value, and on launch **every existing org would begin receiving
> severe, un-batchable notifications** — a mass alert storm, from a feature nobody enabled.
>
> Caught by an executor while shipping TASK 4, not by review.

| mode | verdict written | breach notices | webhooks |
|---|---|---|---|
| `monitor` | yes, unchanged | **suppressed** | still fire |
| `flag` | yes, unchanged | per existing preferences | still fire |
| `block` | yes, unchanged | sent, and marked severe so digest batching cannot delay them | still fire |

Three rules that are not negotiable:

1. **`block` must be byte-identical to today**, because `block` is what every existing org is
   stored as. Today nothing reads this field, so *every* org currently gets ordinary notification
   behaviour. If `block` starts meaning "severe and un-batchable", the default path changes for the
   entire customer base at once.
   **Decide before building:** either `block` keeps today's ordinary behaviour and only `monitor`
   changes anything, or the column is migrated to a deliberate default first. A test must prove an
   org that has never touched the setting sees no change on the day this ships.
2. **Webhooks always fire, in every mode.** They are a machine contract a customer has built
   against; silently dropping them because of a UI setting breaks integrations invisibly. Only
   *notifications to humans* are modulated.
3. **`monitor` suppresses delivery, never recording.** The verdict, the `AuditEvent`, and the chain
   entry are all written regardless. Monitor means "do not email me", not "do not look".

Read the mode where the policy config is already loaded in `_grade_one` — do not add a second
fetch, and do not read it inside the notification threads (they receive plain values across the
queue boundary by design; `org_notifications.py:56` documents why).

## Phase B — the page stops apologising

Once A is real, replace the "recorded but not acted on" block with what the modes actually do,
one concrete line each, in the shape P2 §7.4 asked for: state the consequence, not the concept.

Whatever the owner decides about naming lands here too.

## Owner decision required — do not start without it

**`block` cannot block. What should the control be called?**

- **Keep the names, fix the copy** — the values stay `block|flag|monitor` (no migration, no wire
  change, `judge_policy_config` untouched), and the page explains that these describe the *response*
  to a finding. Cheapest, but the word "block" still reads as prevention to anyone who does not read
  the caption.
- **Relabel in the UI only** — the stored values stay, the labels become e.g. Alert / Record /
  Silent. The evidence snapshot and `foxy-policy-v1` are unaffected because only display text
  changes. Honest to a reader, but the API and the export still say `block`.
- **Rename the field** — most honest, and the most expensive: it is in `OrgPolicy`, the admin
  console, the account export, and **the tamper-evident evidence snapshot**. `foxy-policy-v1` must
  stay structurally stable or new snapshots stop being comparable with old ones, so this likely
  means a new version of the snapshot schema. Do not attempt casually.

## Hard rules

- **Grading is never modified.** Same metadata in, same verdict out, in all three modes.
- The evidence snapshot keeps the field and its current key. Any rename is a separate, explicit
  decision about `foxy-policy-v1`, not a side effect of this plan.
- Notification threads must keep receiving plain values across the queue — nothing ORM- or
  session-bound crosses that boundary.
- Never log `str(exc)` from an authenticated call — exception type name only.
- No fake/placeholder data; honest empty states.

## Verification

```bash
cd backend && python -m pytest tests/integration -q     # one chat at a time; shared DB
python -m pytest foxy-dashboard/ -q
PYTHONDONTWRITEBYTECODE=1 python -m pytest desktop -q
```

Every guard must fail when the rule it protects is removed — re-break each:

1. **`flag` is unchanged** — an org on `flag` produces the same notices as before the change.
2. **`monitor` suppresses both notice paths** — org-level *and* per-seat.
3. **`monitor` still writes the verdict, the `AuditEvent` and the chain entry.** This is the one
   that matters: suppression must never touch evidence.
4. **Webhooks fire in all three modes**, including `monitor`.
5. **Unset behaves as `flag`** — an org that never touched the setting sees no change.
6. **The verdict is identical across all three modes** for the same input. Assert on the persisted
   verdict, not on a notification count.

## MAIN ↔ EXECUTOR protocol

1. Every message ends with a paste-ready block for the other side, both directions.
2. `TASK <n> — P5 §<x>` · branch `feat/p5-<slug>` · report opens `TASK · branch · SHA · DONE|BLOCKED`.
3. Prompts are self-contained; a fresh chat with no history starts from the block alone.
4. `[skip ci]` on every commit. **Deploys are currently blocked on the Actions quota** — MAIN
   dispatches `deploy.yml` once the monthly allowance resets, and `main` is ahead of production
   until then.
