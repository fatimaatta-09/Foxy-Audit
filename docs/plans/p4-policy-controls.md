# P4 — Making the policy controls real

**Plan of record** · 2026-07-29 · MAIN chat is the committer; executors build per this file.
Base: `main` = `13022f3`. Independent of P1/P2/P3 — this is backend + SDK, not the SPA.

---

## Context

P2 §7.4 set out to "explain what these modes actually do" because the owner reported he *"could not
tell whether changing them had any effect."*

That turned out to be literally true. Two Policy controls are **inert**:

```
enforcement_mode      block | flag | monitor
confidence_threshold  high  | balanced | low
```

Both are stored on `OrgPolicy`, shown in the dashboard and admin console, included in the account
export, and **copied into the tamper-evident evidence snapshot** — and read by nothing that grades
or enforces. Verified: zero references in `policy_engine.py`, `worker.py`, `judge.py`, `gemini.py`,
`openai_judge.py`, `judge_routing.py`, or the SDK.

Corroborating evidence that this was already known: `admin_data.py:77` lists the
editable-and-effective set as exactly the four that *do* work
(`pii_detection`, `prompt_injection`, `regulated_data_mode`, `max_token_threshold`).

**The product is not broken.** Enforcement is real — it happens in the SDK decorator, client-side,
before the model call (`sdk/src/foxy_audit/client.py:157`). What is broken is that the dashboard
presents a *second, disconnected* enforcement knob for something already controlled in code. A
customer can set "monitor" in the dashboard while their SDK says `mode="block"`, and block correctly
wins — silently contradicting the UI.

**Outcome:** both controls become real. The dashboard stops lying.

## Owner decisions (2026-07-29, locked)

1. **Wire `enforcement_mode` for real** — not label it, not remove it.
2. **Precedence: strictest wins.** `observe < redact < block`. The org policy can only *tighten*.
3. **Fail posture: last known policy, never block the caller.** Async fetch, disk cache, cold start
   falls back to local config.
4. **Keep both fields in the evidence snapshot**; fix the docstring instead (assigned to P2 —
   `foxy-policy-v1` must stay structurally stable or new snapshots stop being comparable with old).

## Why "strictest wins" is the whole security design

Making enforcement remotely controllable creates a remote **kill-switch**: today `mode="block"`
lives in the customer's code and nothing over the network can weaken it. Naive central control
means a compromised dashboard account could set every SDK deployment to `monitor` and silently
disable blocking across an entire estate, from a browser.

Strictest-wins removes that by construction. The worst a compromised dashboard can do is make
everything block — loud, safe, and immediately obvious. **No configuration exists that weakens
enforcement remotely.** This property is the reason the feature is shippable at all, and it must be
tested directly, not assumed.

---

## Phase A — `confidence_threshold` (server-side only, small)

This one needs no SDK change at all. It is a **judge tuning parameter**, and the judge already
receives the policy config.

- `gemini.py:_build_system_prompt(policy_config, …)` and `openai_judge.py:62` already take the org's
  `policy_config` dict and build the grading prompt from it. `confidence_threshold` is simply never
  read.
- Wire it into both prompts so it changes how conservatively the model grades:
  `high` = fewer false positives, only flag when confident · `balanced` = default ·
  `low` = catch every edge case.
- `policy_snapshot.py` already carries the value, so the evidence record becomes *true* the moment
  the judge honours it — no schema change.

**Test it by behaviour, not by string.** Assert the built prompt differs across the three settings
*and* that a stubbed judge run produces different verdicts on a borderline case. A test that only
greps the prompt for the word "high" passes even if the model ignores it.

---

## Phase B — `enforcement_mode` (SDK, the real work)

### B1 · Move mode resolution out of the closure

`client.py:204` computes `effective_mode` **at decoration time** and captures it in the closure:

```python
effective_mode = str(mode if mode is not None else self.cfg.mode or "observe").strip().lower()
```

Decorators are applied at import, so this freezes for the process lifetime. Resolution must move
**inside the wrapper** so each call can consult the cached org policy. Both the sync and async
wrappers are affected. This is the change most likely to break existing behaviour — the per-call
cost must stay negligible (a dict read, not a lock or a parse).

### B2 · Fetch the policy — async, never in the hot path

- The SDK already has everything needed: `requests` as a dependency, a background dispatcher thread
  (`dispatch.py:AsyncDispatcher`), and a durable spool. **Reuse the dispatcher's thread**; do not add
  a second one.
- `GET /v1/policies` accepts the SDK Bearer key — `resolve_org` (`auth.py:196`) takes *either* the
  key or a session, so no new endpoint and no auth change is needed.
- Refresh on a TTL (start at 5 minutes, configurable). Never on the call path.

### B3 · Cache it on disk

Beside the spool (`cfg.spool_path`), so a restarted process honours org policy immediately rather
than reverting to local defaults for the first TTL. Same durability argument as the spool itself.
Cache the fetched mode plus a fetched-at timestamp; a stale cache is still better than no policy.

### B4 · Strictest-wins resolution

```
RANK = {"observe": 0, "redact": 1, "block": 2}
effective = max(local_mode, org_mode, key=RANK.get)
```

Where `local_mode` is the existing decorator-arg → client-config → `"observe"` chain, unchanged.
If there is no cached org policy, `effective == local_mode` — today's behaviour exactly.

### B5 · Fail posture

- No cache and no successful fetch → local config. **The wrapped function is never delayed and never
  fails because of a policy fetch.**
- Fetch failure → keep serving the last known policy, log at debug, retry on the next tick.
- Log the *type* of any exception only — `dispatch.py` already follows this, and the API key is a
  Bearer header here rather than a URL param, but the habit should hold.

### B6 · Observability

A customer must be able to tell *why* a call was blocked. When the org policy tightens the mode,
say so in the block message and in the emitted event's decision metadata — otherwise a developer
sees `FoxyPolicyBlocked` on code that says `observe` and has no way to discover the cause.

### B7 · Release

The SDK is published — 1.2.0 is on PyPI. This needs a version bump, and **one `v*` tag releases
both the SDK wheel and the desktop binaries**, so `VERSION`, `sdk/pyproject.toml` **and**
`sdk/src/foxy_audit/__init__.py` must all carry the same number (`check-version` validates only the
first two — the third is the one that silently ships wrong).

---

## Phase C — the dashboard tells the truth

Small, and it lands only after A and B are real.

- Policy page copy states what each mode actually does, and that the effective mode is the stricter
  of this setting and the SDK's.
- `judge_models` (added by P2 §7.6) already reports the real model per provider — leave it.
- Coordinate with whoever holds `foxy-audit-premium.html` at the time; P2 owns it until its plan
  merges.

---

## Hard rules

- **No configuration may weaken enforcement remotely.** B4 is the guarantee; test it explicitly.
- **Never block the caller.** The SDK's contract is that auditing is invisible to the host app's
  latency and never a source of failure. A policy fetch must not change that.
- **Content-blindness** — `/v1/policies` carries no interaction content, so this is safe, but no
  prompt or response text may enter any new code path.
- **Never log a key or an exception message from an authenticated call** — type name only.
- The evidence snapshot keeps both fields. Phase A makes `confidence_threshold` true; Phase B makes
  `enforcement_mode` true. Neither changes `foxy-policy-v1`.

## Verification

```bash
cd backend && python -m pytest tests/integration -q     # clear stray backends first
python -m pytest sdk/tests -q
PYTHONDONTWRITEBYTECODE=1 python -m pytest desktop -q    # /v1/policies has a desktop consumer
```

Each guard must fail when the rule it protects is removed — re-break every one:

1. **Strictest-wins, both directions** — org=`observe` + local=`block` must still block. This is the
   kill-switch test and the most important assertion in the plan.
2. **Never blocks** — with the policy endpoint stubbed to hang, a decorated call still returns within
   its normal budget.
3. **Cold start** — no cache, no network: behaviour is byte-identical to today.
4. **Cache survives restart** — a new process honours org policy before its first fetch completes.
5. **Stale beats absent** — fetch failing after a successful one keeps the old policy, not the default.
6. **Per-call resolution** — changing the cached policy changes the next call's behaviour without
   re-importing (proves B1 actually moved out of the closure).
7. **`confidence_threshold` changes verdicts** — behaviour, not prompt-string greps.
8. **Desktop unaffected** — 754 tests green; `/v1/policies` gained keys, not renames.
9. **Attribution** — a call blocked because the org tightened says so.

## MAIN ↔ EXECUTOR protocol

1. Every message ends with a paste-ready block for the other side, both directions, no exceptions.
2. `TASK <n> — P4 §<x>` · branch `feat/p4-<slug>` · report opens `TASK · branch · SHA · DONE|BLOCKED`.
3. Prompts are self-contained; a fresh chat with no history starts from the block alone.
4. `[skip ci]` on every commit — the repo is private and near its Actions limit. **MAIN must run
   `gh workflow run deploy.yml --ref main` after each merge**, because `[skip ci]` skips the deploy too.
5. Phase A and Phase B are separate branches. A is server-only and small; B changes a published SDK.
   Do not bundle them.
