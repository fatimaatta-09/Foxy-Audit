# P5 — giving `enforcement_mode` a real consumer

**Plan of record** · 2026-07-29 · MAIN chat is the committer; executors build per this file.
Base: `main` = **`9d003cf`** (re-anchored 2026-07-31 at `9cd7a20`; `9d003cf` is deploy-docs only
and touches no anchor here. Originally written against `f6f5ed6`).
Owner decision, 2026-07-29: **give the field a real consumer** — not soften the copy, not remove
the control.

> ### Re-anchored 2026-07-31 — the whole P6 punch-list shipped in between
>
> Fourteen commits landed between this plan's last revision and now
> (`docs/plans/dashboard-punchlist-p6.md`, all six phases). **Every anchor below was re-read out of
> `origin/main` before this revision**, not carried forward. What actually moved:
>
> | | was | now |
> |---|---|---|
> | next migration | `0057` | **`0059`** — `0057_user_avatar`, `0058_per_org_judge_model` both landed |
> | `routers/policies.py` schema default | `:42` | `:41` (assignment `:174` → `:214`, audit `:260`) |
> | `worker.py` `_grade_one` / breach branch | `:190` / `:235` | `:192` / `:237` |
> | dashboard enforcement select | `:2150` | `:2388` (note `:2389`, `loadPolicy` `:3917`, save default `:4074`) |
> | dashboard file size | 615,574 B | **664,514 B** — use `git cat-file -s`, never `wc -c` (CRLF) |
>
> Unchanged and still valid: `models.py:297`, `policy_snapshot.py:28-31,:52`, both send paths
> (`org_notifications.py:73-81,:106`, `user_notifications.py:164-179`), `account_actions` still
> records `enforcement_mode` on every `policy.update`, and `sdk/client.py:453`.
>
> Nothing in P6 conflicts with this plan. P6f (per-org judge model) touched the same router and
> established the `model_fields_set` guard for *"an omitted key keeps the stored value"* — reuse
> that idiom rather than inventing one. P6c's `0057` is why the number moved.

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

**This plan therefore had a copy decision baked into it. It is answered** — see "Owner decision"
below: relabel in the UI only, already shipped in `98e1399`.

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
(`org_notifications.py:63` already reads `risk_score`). No new query and no new column are needed
for the consumer itself; the one migration this plan ships (`0059`) exists only to move the
default, not to feed the seam.

**Do not put the check anywhere else.** Grading itself must stay identical across all three modes —
the verdict is evidence, it goes into the hash chain and the export, and a setting that changed the
*grade* would make two orgs' evidence incomparable. This field changes **what we do about** a
breach, never what the breach *is*.

## Phase A — the consumer

Resolution: the org's **live** `enforcement_mode`, read at send time.

> ### ⚠ THE DEFAULT IS `block`, NOT `flag` — corrected 2026-07-30
>
> `models.py:297` carries `server_default="block"` and `policies.py:41` defaults the schema to
> `"block"`. **Every existing organisation is already stored as `block`**, not because anyone chose
> it but because it is the column default — the same fact that forced `sdk_enforcement` to be a
> separate nullable column in P4 §B.
>
> An earlier version of this plan asserted `flag` was the default and the majority case. It is
> neither. Building against that claim would have shipped a production incident: the compatibility
> test would have guarded the wrong value, and on launch **every existing org would begin receiving
> severe, un-batchable notifications** — a mass alert storm, from a feature nobody enabled.
>
> Caught by an executor while shipping P4's TASK 4, not by review.

> ### OWNER DECISION, 2026-07-30 — migrate the default, then escalate
>
> Chosen from the three options below: **`block` becomes a deliberate opt-in first, and only then
> is it allowed to change email behaviour.** The naming question is already settled by shipped
> code — `foxy-audit-premium.html:2388` relabels in the UI only (Urgent / Notify / Silent) with the
> stored values untouched, which was option 2. Nothing left to decide there.

### A.1 — make `block` a deliberate choice (migration `0059`)

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

`account_actions` is why this is safe to do at all: `policies.py:257-262` has recorded every
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

| site | today | after | task |
|---|---|---|---|
| `models.py:297` `server_default` | `"block"` | `"flag"` (via `0059` `alter_column`) | 1 |
| `routers/policies.py:41` schema default | `"block"` | `"flag"` | 1 |
| `desktop/policy_data.py:121, :221` `_choice` fallback | `"block"` | `"flag"` | 1 |
| `desktop/test_d7_policy.py:32, :41` | asserts `"block"` | asserts `"flag"` | 1 |
| `sdk/src/foxy_audit/org_policy.py:48` comment | "defaults to `block`" | describe the new default + why it moved | 1 |
| `foxy-dashboard/foxy-audit-premium.html:4074` | `\|\|'block'` | `\|\|'flag'` | **2** |

The dashboard token is deliberately held for TASK 2 so the two tasks share **no file** — the
dashboard is TASK 2's alone, everything else is TASK 1's. `desktop/policy_data.py:121` is the
one that must not slip: `put_body` runs an unset field through `_choice(..., "block")`, so a
desktop save would quietly write `block` back over the migrated default.

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

`notify_on_breach = "digest"` currently sends *nothing*. Verified on `origin/main`: the only two
reads that gate behaviour are `org_notifications.py:81` and `user_notifications.py:177`, both
`!= "immediate"` → return, and `send_weekly_digests` is driven by the per-user
`notify_weekly_digest` preference, not by this field at all. `digest` is behaviourally identical
to `none`. That is what `block` escalates: a value that today silently drops breach mail starts
delivering it immediately for orgs that deliberately asked for the loudest setting. `"none"` is an
explicit "do not email me" and is never bypassed — an escalation that overrides an off switch is a
dark pattern, not a feature.

> ### ⚠ NEW DEFECT, found while re-anchoring 2026-07-31 — `digest` is mislabelled on both surfaces
>
> Both products tell the customer this value batches their mail:
>
> * `foxy-audit-premium.html:2441` — `<option value="digest">batch — hourly digest</option>`
> * `desktop/policy_data.py:69` — `("digest", "batch — hourly digest")`
>
> **There is no hourly digest, and there never has been.** Choosing it means receiving no breach
> email, ever. This is the same class of defect as the one this plan exists to close, it sits on
> the same page, and §A.2 makes it load-bearing: `block` is defined as *escalating* `digest`, so
> shipping A over a label that lies about the state being escalated from would make the new copy
> wrong on arrival.
>
> **In scope for §B, as a relabel only.** `notify_on_breach` is inside `foxy-policy-v1`
> (`policy_snapshot.py:54`) — the stored value must not be removed or renamed, exactly as with
> `enforcement_mode`. Display text only. Fix both surfaces to the *same* string; the desktop
> comment at `policy_data.py:60-61` claims its options are "quoted verbatim from the web's
> `<option>` text" and cites `html:1319-1332`, which is now wrong about both the text and the
> line numbers.

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

Ships **after** A is merged, off fresh `origin/main`. Dashboard file + desktop label tuples only.

1. **`foxy-audit-premium.html:2389-2394`** — the `#polEnforcementNote` currently reads *"Recorded,
   not yet acted on: … nothing in the grading pipeline reads it today."* That sentence becomes
   false the moment A lands. Replace it with what each mode does, one concrete line each, in the
   shape P2 §7.4 asked for — **state the consequence, not the concept**. The three consequences are
   the §A.2 table and nothing more: Silent stops the emails, Notify is the ordinary path, Urgent
   also mails you when you have chosen batching. Keep the two sentences that are still true and
   still load-bearing: this is about a breach *the judge has already found*, never about whether it
   happens, and the SDK setting below is the only control that can stop a call.
2. **`foxy-audit-premium.html:2378-2387`** — the HTML comment above the select ends *"matches what
   the planned consumer will actually do with each value: severe and un-batchable, ordinary
   notification, suppressed."* Two of those three are now wrong (`block` escalates batching, it is
   not "severe"; there is no un-batchable path). Rewrite it to describe the shipped consumer and
   point at this plan.
3. **Silent is not invisible — say so.** `routers/account.py:859-875` backfills a `kind="breach"`
   in-app notification from the ledger, independent of email, and P6b made those rows open a detail
   dialog. That is what makes "Silent — record only" honest rather than a hole: the finding is still
   in Alerts, in the ledger and in the export. One clause, not a paragraph.
4. **The `digest` relabel**, both surfaces, identical string — see the ⚠ box in §A.2.
   `foxy-audit-premium.html:2441` and `desktop/policy_data.py:69`.
5. **Resync the desktop enforcement labels.** `desktop/policy_data.py:62-64` still carries the
   pre-`98e1399` prevention language — `"block on breach — highest protection"`,
   `"flag only, allow through"` — which the web replaced with Urgent / Notify / Silent. Standing
   owner rule: **web wins on any style or copy conflict.** Bring them across verbatim and fix the
   stale `html:1319-1332` cite in the comment above them.
6. **`foxy-audit-premium.html:4074`** — the save-path default `||'block'` → `||'flag'`, the one
   token held back from A.1 so the two tasks share no file.

No naming change, no migration, no wire change: the owner's decision was relabel-in-the-UI-only and
that shipped in `98e1399`. `foxy-policy-v1` is untouched by all of the above.

## Owner decision — ANSWERED 2026-07-30 (kept for the record)

**`block` cannot block. What should the control be called?** → **relabel in the UI only**, and that
is already shipped (`foxy-audit-premium.html:2388`, commit `98e1399`). The stored values, the wire
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
python -m pytest sdk/tests -q
PYTHONDONTWRITEBYTECODE=1 python -m pytest desktop -q   # FROM THE REPO ROOT
```

The shared test DB is currently at head `0058`. A stale branch that downgrades it to make itself
pass is the branch being wrong, not the database — that exact symptom preceded the near-miss on
`0bfe3ee`, where a branch cut before P6f would have deleted 860 lines of it.

Every guard must fail when the rule it protects is removed — re-break each:

1. **`flag` is unchanged** — an org on `flag` produces exactly the notices it produced before, and
   after `0059` that is the whole un-chosen population.
2. **`monitor` suppresses both notice paths** — org-level *and* per-seat.
3. **`monitor` still writes the verdict, the `AuditEvent` and the chain entry.** This is the one
   that matters: suppression must never touch evidence.
4. **Machine contracts fire in all three modes**, including `monitor`: `enqueue_grading`, and the
   org's own `notify_webhook_url` POST.
5. **`block` escalates `digest` → sent, and leaves `none` silent.** Both halves asserted.
6. **The verdict is identical across all three modes** for the same input. Assert on the persisted
   verdict, not on a notification count.
7. **`0059` leaves recorded evidence alone** — existing `audit_logs.event_metadata.policy_snapshot`
   values and their `policy_snapshot_hash` are byte-identical before and after.
8. **`0059` does not overwrite a deliberate `block`** — an org with a `policy.update` action
   recording `block` still reads `block` afterwards.

## MAIN ↔ EXECUTOR protocol

The loop, fixed: MAIN writes the prompt → the owner relays it → the executor pushes a branch →
MAIN second-checks the code → MAIN pushes to `main` by SHA → MAIN updates the vault → MAIN issues
the next prompt. **MAIN does not build.**

1. Every message ends with a paste-ready block for the other side, both directions.
2. `TASK <n> — P5 §<x>` · branch `feat/p5-<slug>` · report opens `TASK · branch · SHA · DONE|BLOCKED`.
3. Prompts are self-contained; a fresh chat with no history starts from the block alone.
4. `[skip ci]` on every commit — note it skips `deploy.yml` too. **Deploys are currently blocked on
   the Actions quota** anyway; MAIN dispatches `deploy.yml` once the allowance resets, and `main`
   is ahead of production until then.
5. **Nobody touches the working tree while an executor is building.** MAIN reviews in an isolated
   `git worktree` and merges by direct SHA push (`git push origin <sha>:refs/heads/main`) — never
   by checking a branch out in the shared tree, and never by editing `main`.
6. **Branch off fresh `origin/main` and rebase before pushing.** TASK 2 waits for TASK 1 for this
   reason: the two touch `desktop/policy_data.py`, and a branch cut too early silently reverts
   merged work. This is not theoretical — it caught a branch that would have deleted P6f.
7. **UI work loads `ui-ux-pro-max` first, then `frontend-design`.** That is TASK 2 only; TASK 1
   touches no UI.
8. MAIN's merge gate: fast-forward-safe over `origin/main` · `node --check` every inline
   `<script>` in changed HTML · scope grep · no-fake-data grep · no-secret grep · single Alembic
   head · the `code-review` skill.
9. After every merge MAIN syncs the vault: a dated devlog entry, plus the affected section note
   with its `updated:` and `verified-against:` refreshed. Noticed-but-not-fixed →
   `Worth Noting — Issues`; Claude got it wrong → `Where Claude Was Wrong`.
10. `ponytail` only where it is provably output-neutral. **Never** on a hash, a verdict, or the
    wire contract — which rules it out of most of this plan.
