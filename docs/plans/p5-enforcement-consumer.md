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

> ### OWNER DECISION, 2026-07-30 — migrate the default, then escalate
>
> Chosen from the three options below: **`block` becomes a deliberate opt-in first, and only then
> is it allowed to change email behaviour.** The naming question is already settled by shipped
> code — `foxy-audit-premium.html:2150` relabels in the UI only (Urgent / Notify / Silent) with the
> stored values untouched, which was option 2. Nothing left to decide there.

### A.1 — make `block` a deliberate choice (migration `0057`)

Today `block` is what an org is stored as because it is the column default, not because anyone
chose it. Escalating email for that population would be an alert storm from a feature nobody
enabled. So the default moves first:

```sql
UPDATE org_policies p SET enforcement_mode = 'flag'
 WHERE p.enforcement_mode = 'block'
   AND NOT EXISTS (SELECT 1 FROM account_actions a
                    WHERE a.org_id = p.org_id
                      AND a.action = 'policy.update'
                      AND a.detail->>'enforcement_mode' = 'block');
```

`account_actions` is why this is safe to do at all: `policies.py:201` has recorded every
`policy.update` with the chosen `enforcement_mode` in its JSONB `detail`, so an org that
*deliberately* selected `block` is distinguishable from one that merely inherited it, and a real
choice is never overwritten.

**This does not contradict migration `0056`.** That migration's docstring refuses to rewrite
`enforcement_mode` because its values "are already recorded inside historical policy snapshots that
have been handed to auditors". That objection is about *recorded evidence*, and it is honored: this
UPDATE touches only the live `org_policies` row. `audit_logs.event_metadata.policy_snapshot` is
never read, never written, never rewritten — a guard test asserts existing snapshots and their
hashes are byte-identical after the migration runs.

The server default moves with it, or new orgs land on `block` again and the opt-in evaporates:

| site | today | after |
|---|---|---|
| `models.py:297` `server_default` | `"block"` | `"flag"` (via `0057` `alter_column`) |
| `routers/policies.py:42` schema default | `"block"` | `"flag"` |
| `foxy-dashboard/foxy-audit-premium.html:3674` | `\|\|'block'` | `\|\|'flag'` |
| `desktop/policy_data.py:121, :221` `_choice` fallback | `"block"` | `"flag"` |
| `desktop/test_d7_policy.py:32, :41` | asserts `"block"` | asserts `"flag"` |
| `sdk/src/foxy_audit/org_policy.py:48` comment | "defaults to `block`" | describe the new default + why it moved |

Also in scope, because it is the same class of lie this plan exists to kill:
`sdk/src/foxy_audit/client.py:453` tells a blocked developer the block came from
`enforcement_mode=block`. **It did not** — the SDK reads `sdk_enforcement`
(`org_policy.py:40` says so explicitly). The message names the wrong field and sends them to the
wrong control. Fix the string to say `sdk_enforcement`.

### A.2 — the consumer

| mode | org + per-seat breach emails | `notify_webhook_url` POST | webhook subscriptions | verdict / chain |
|---|---|---|---|---|
| `monitor` | **suppressed**, whatever `notify_on_breach` says | unchanged (still fires) | still fire | unchanged |
| `flag` | today's behaviour — only when `notify_on_breach == "immediate"` | unchanged | still fire | unchanged |
| `block` | `"digest"` is escalated to immediate; `"immediate"` unchanged; **`"none"` still means none** | unchanged | still fire | unchanged |

`notify_on_breach = "digest"` currently sends *nothing* — both senders early-return unless the value
is `"immediate"`, and `send_weekly_digests` is driven by a per-user preference, not by this field.
That is what `block` escalates: a value that today silently drops breach mail starts delivering it
immediately for orgs that deliberately asked for the loudest setting. `"none"` is an explicit
"do not email me" and is never bypassed — an escalation that overrides an off switch is a dark
pattern, not a feature.

Three rules that are not negotiable:

1. **`flag` must be byte-identical to today**, and after `0057` `flag` is what the un-chosen
   population is stored as. A test must prove an org that has never touched the setting sees no
   change in notification behaviour on the day this ships.
2. **Machine contracts always fire, in every mode.** `webhook_delivery.enqueue_grading` is already
   unconditional — leave it that way. `org_notifications.send_breach_notice` also POSTs the org's
   `notify_webhook_url`; under `monitor` the **email** is skipped and that POST still happens, so
   the early return has to be split rather than widened. Only *notifications to humans* are
   modulated.
3. **`monitor` suppresses delivery, never recording.** The verdict, the `AuditEvent`, and the chain
   entry are all written regardless. Monitor means "do not email me", not "do not look".

**Where to read the mode — corrected 2026-07-30.** An earlier version of this plan said to read it
in `_grade_one` and *not* in the notification threads. That was wrong on both counts.
`org_notifications.send_breach_notice:80` and `user_notifications.send_breach_alert:175` already
`db.get(OrgPolicy, oid)` at send time, deliberately: *"a tenant who turns breach notices off
between the grade and the send should not receive one."* Gate there. It costs no new query, it
follows the documented precedent, and it is semantically right — delivery follows the tenant's
**current** setting, not one frozen into an evidence snapshot months ago. The queue boundary rule is
untouched: `enqueue_*` still copies plain values and nothing ORM-bound crosses it.

`judge_policy_config` must keep dropping `enforcement_mode` on its projection line. Nothing about
this field may reach a judge.

## Phase B — the page stops apologising

Once A is real, replace the "recorded but not acted on" block with what the modes actually do,
one concrete line each, in the shape P2 §7.4 asked for: state the consequence, not the concept.

Whatever the owner decides about naming lands here too.

## Owner decision — ANSWERED 2026-07-30 (kept for the record)

**`block` cannot block. What should the control be called?** → **relabel in the UI only**, and that
is already shipped (`foxy-audit-premium.html:2150`, commit `98e1399`). The stored values, the wire
contract and `foxy-policy-v1` are untouched. The options as they were put:

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

1. **`flag` is unchanged** — an org on `flag` produces exactly the notices it produced before, and
   after `0057` that is the whole un-chosen population.
2. **`monitor` suppresses both notice paths** — org-level *and* per-seat.
3. **`monitor` still writes the verdict, the `AuditEvent` and the chain entry.** This is the one
   that matters: suppression must never touch evidence.
4. **Machine contracts fire in all three modes**, including `monitor`: `enqueue_grading`, and the
   org's own `notify_webhook_url` POST.
5. **`block` escalates `digest` → sent, and leaves `none` silent.** Both halves asserted.
6. **The verdict is identical across all three modes** for the same input. Assert on the persisted
   verdict, not on a notification count.
7. **`0057` leaves recorded evidence alone** — existing `audit_logs.event_metadata.policy_snapshot`
   values and their `policy_snapshot_hash` are byte-identical before and after.
8. **`0057` does not overwrite a deliberate `block`** — an org with a `policy.update` action
   recording `block` still reads `block` afterwards.

## MAIN ↔ EXECUTOR protocol

1. Every message ends with a paste-ready block for the other side, both directions.
2. `TASK <n> — P5 §<x>` · branch `feat/p5-<slug>` · report opens `TASK · branch · SHA · DONE|BLOCKED`.
3. Prompts are self-contained; a fresh chat with no history starts from the block alone.
4. `[skip ci]` on every commit. **Deploys are currently blocked on the Actions quota** — MAIN
   dispatches `deploy.yml` once the monthly allowance resets, and `main` is ahead of production
   until then.
