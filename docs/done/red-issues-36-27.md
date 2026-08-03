> ## Complete — 2026-08-03. Read this banner before the plan.
>
> **Both red issues are closed.** Three phases plus two corrections, CI and CD
> green on each.
>
> | | Commit | What |
> |---|---|---|
> | E1 | `e34e909` | #36 backend — the condition, and the upgrade that clears it |
> | E2 | `56840d6` | #27 — the verifier ships inside the export |
> | — | `6e1f988` | CI fix: stop scanning the bundled verifier for audit actions |
> | E3 | `134c7c1` | #36 UI — the lock, and the plan chooser that ends it |
> | — | `6ef5de5` | #52 — name a verifier command a customer can run |
>
> Backend **907 → 925** passed · dashboard **310 → 321** · desktop **768 → 775**.
> One migration in the whole plan: none. Every column already existed.
>
> ### The finding that changed what #36 was
>
> The owner chose *lock, and require an upgrade*. **There was no upgrade to
> require.** `evaluation_offer_id` was written twice and cleared nowhere;
> `/v1/billing/checkout-session` carried no org identity; `_handle_checkout` only
> ever *provisioned*. So an expired-evaluation customer who did exactly what the
> product asked **paid money and got a second, empty workspace** while the
> original — holding every audit event they had — stayed locked forever.
>
> Closing #36 meant building the exit door. The banner was the easy half.
>
> E3 then found the defect was wider still: two signed-in surfaces were posting
> the anonymous checkout, one of them the credits-exhausted banner's own CTA.
>
> ### What this plan got wrong
>
> **The export carve-out.** I specified an "Export your evidence" action on the
> locked overlay so an expired org would not be stranded, having assumed
> `/v1/logs/export` was reachable while locked. It is not in `_GATE_EXEMPT` and
> never has been. Making it real would have widened the gate for *every* locked
> condition — a change to a shipped lock, made through a UI phase, that nobody
> asked for. **Withdrawn** (`665d1e9`), filed as #49.
>
> **The drift guard's home.** The plan said `pytest verifier/` would catch a
> vendored copy drifting. CI does not run `pytest verifier/` — it runs six
> specific paths and that is not one. The guard would have been **silent**, which
> is the exact failure vendoring introduces. Moved to `backend/tests/integration/`.
>
> **The E1 test list.** Backend phase, backend tests — so I ran those and merged.
> `test_the_reason_vocabulary_matches_the_backends` spans both surfaces and sat
> **red on `main` for three commits**, two of them deploys, because CI runs 2 of
> the 11 dashboard guard files. Filed as #55.
>
> ### Verified rather than reported
>
> #27's close was proven by round trip, not by test: a real bundle unzipped into
> a folder where `git rev-parse` says *not a git repository*, run under a stock
> interpreter with no venv — `[OK] chain intact`, `exit=0`; then one field edited
> — `[FAIL] CHAIN BROKEN at seq 3`, `exit=1`.
>
> ### Open after this plan
>
> #49 (locked orgs cannot export) · #51 (nothing renders the passport) · #52 →
> closed · #53 (the CLI round-trips the server) · #54 (stale README output) ·
> #55 (CI runs 2 of 11 dashboard guards) · #56 (members see no upgrade control) ·
> #57 (the upgrade is not audited) · #58 (desktop cannot branch on a 402 code).
>
> **The register stands at 47 open, 2 closed.** These were the two that made a
> claim the product could not keep; the rest are contained.

---

# The two red issues — #36 and #27

Source: `Worth Noting — Issues.md`. Planned 2026-08-03 against `main` @ `73b575c`.

## Context

The register holds **43 open entries**. (I previously said eleven — that counted
only what this session added. The correction matters because it changes what
"finish these" means.) Two are 🔴, and the owner scoped this plan to those two:

- **#36** — an evaluation offer never expires cleanly; the org ends up unable to
  capture anything.
- **#27** — a third party has no way to obtain the verifier, while the Compliance
  Passport promises independent verification in writing on every copy.

Both are product-credibility issues rather than bugs: each makes a claim the
product cannot currently keep. The remaining ~41 (18 🟡, 23 🟢) stay in the
register for a later sweep.

### Owner decisions (asked and answered 2026-08-03)

1. **#36 — lock, and require an upgrade.** Not "restore the free tier". The
   evaluation is the whole relationship; when it ends, the org is blocked and
   told to upgrade.
2. **#27 — ship the verifier inside the export.** Not a public repo, not a
   download URL. The auditor who has the evidence gets the tool with it.

### ⚠ The finding that reframes #36: there is no upgrade to require

The owner's answer only works if upgrading actually releases the org. It does
not. Verified against `origin/main`, not the local checkout (which was 6 commits
stale — worth stating, because two of my reads came from it before I noticed):

- **`evaluation_offer_id` is written twice and cleared nowhere.** Set at
  `billing.py:247` (judge signup) and `:904` (redeem). Read at `logs.py:169`,
  `account.py:205`, `billing.py:890`. No path in the codebase ever unsets it, so
  `logs.py:169`'s evaluation branch is entered **forever**, whatever the org
  later pays.
- **`POST /v1/billing/checkout-session` is unauthenticated and org-blind.** Its
  Stripe metadata is `{"foxy_plan": ...}` (`billing.py:328`) — no org identity.
  Compare `billing.py:750`, the card-on-file session, which *does* send
  `{"foxy_org_id": ...}`. The pattern exists; the upgrade path does not use it.
- **`_handle_checkout` only ever provisions a NEW org.** It looks the customer up
  by `stripe_customer_id` and returns `already_provisioned` if found
  (`billing.py:450-454`). An evaluation org that never paid has no
  `stripe_customer_id`, so the lookup misses and the webhook **creates a second
  organisation with a second user row** — leaving the original, with all its
  audit evidence, locked permanently.

So today an expired-evaluation customer who does exactly what the product asks
pays money and gets a different, empty workspace. **Making the exit door exist is
the work; the banner is the easy half.**

### The assumption I am making, stated so it can be overruled

"Lock" is implemented as: **capture stays blocked** (today's behaviour, now
explained) **and the dashboard renders D4's blocking overlay** with an Upgrade
CTA — but **the export path stays reachable**, added to the overlay as a
secondary action.

That last clause is mine, not the owner's. The reason is D1's own stated
principle, already shipped in `billing_state.py`: *"`cancelled` does not lock the
dashboard… someone who left can still read and export the evidence they already
paid for. Leaving is not owing."* An expired evaluation is closer to leaving than
to owing. Stranding an auditor's evidence behind a paywall is also the exact
shape of thing this product exists to argue against.

> ### ⛔ WITHDRAWN 2026-08-03, after E1 (`e34e909`)
>
> **The export carve-out is dropped. E3 does not build it.** The E1 executor
> found the mechanics I had assumed: `auth._GATE_EXEMPT` is
> `("/v1/auth/", "/v1/billing/", "/v1/account/preferences")` — `/v1/logs/export`
> is **not** in it, so a locked org's dashboard cannot export at all. That is not
> new behaviour; `incomplete` and `past_due` have always worked this way.
>
> Making my carve-out real means adding `/v1/logs/export` to the exempt tuple,
> which would **also unlock export for every other locked condition** — a change
> to D1's shipped lock, smuggled in through a UI phase, that nobody asked for.
> The owner chose the harder option deliberately; widening the gate to soften it
> is not a UI decision to make on their behalf.
>
> The evidence is not stranded either way: export over the **SDK Bearer key**
> still works (verified live in E1), and everything returns on upgrade. Filed as
> a register entry rather than dropped, so the concern survives the decision.

---

## Phases

Both phases edit `backend/app/routers/logs.py` (different functions), so they run
**sequentially** rather than risk the merge. E3 consumes E1's contract.

| Phase | Branch | Scope |
|---|---|---|
| E1 | `feat/eval-expiry-exit` | #36 backend — the condition, and the upgrade that clears it |
| E2 | `feat/verifier-in-export` | #27 — the export bundle + the passport sentence |
| E3 | `feat/eval-expiry-ui` | #36 UI — the locked state, on dashboard and desktop |

Every phase: `git fetch origin && git worktree add ../wt-<phase> origin/main`.
Re-check `git merge-base --is-ancestor origin/main HEAD` **immediately before
pushing**, not at branch time. `ui-ux-pro-max` then `frontend-design` on E3.
**Never spell the CI skip marker in a commit body** — GitHub substring-matches
the whole message and silently skips the deploy.

---

## E1 — The evaluation exit (backend)

### 1. Route the condition through `billing_state`, not around it

`logs.py:169-186` raises `evaluation_expired` and `evaluation_credits_exhausted`
inline. That is the same three-places-disagree shape D1 fixed for subscriptions —
and the evaluation gate was the one it left out, so `/v1/billing/access` cannot
report it and the dashboard cannot explain it.

- Move both conditions into `billing_state.capture_block()`
  (`backend/app/billing_state.py:226`), preserving the **existing order**:
  `trial_expired` → `subscription_inactive` → `credits_exhausted` → the
  evaluation pair. That order is asserted elsewhere; do not reorder it.
- Add `EVALUATION_EXPIRED` / `EVALUATION_CREDITS_EXHAUSTED` to the reason
  vocabulary beside `TRIAL_EXPIRED` (`billing_state.py:69-79`). Same strings as
  the 402 body's `code`, so E3 needs one switch.
- Add expiry to `dashboard_lock()` so `locked` is true for it. **Credits
  exhausted must NOT lock** — that org is inside a live offer and can still read
  its own evidence; only the expiry of the window locks.
- `describe()` picks it up for free once the above is in place.

Keep `logs.py` raising the 402 from `capture_block`'s return, exactly as D1 left
it — the router should not regain its own copy of the rule.

### 2. Make upgrading release the org

This is the half that does not exist. Three changes, and they are ordered:

- **`POST /v1/billing/checkout-session` needs an authenticated variant that
  carries the org.** Follow `billing.py:750`'s existing pattern —
  `metadata={"foxy_org_id": str(org.id), "foxy_plan": canonical_plan}`. Do not
  change the anonymous acquisition flow; it is what the sale page uses and it
  works. This is a second door for a signed-in org, not a rewrite of the first.
- **`_handle_checkout` must branch on `foxy_org_id`.** When present, *upgrade
  that org* rather than provisioning: set `plan_tier`, `monthly_log_quota =
  settings.quota_for(plan_tier)`, `stripe_customer_id`,
  `stripe_subscription_id`, `subscription_status="active"` — and **clear the
  evaluation fields** (`evaluation_offer_id`, `evaluation_credit_limit`,
  `evaluation_credits_used`, `evaluation_ends_at`). Without that clear, `logs.py`
  keeps entering the evaluation branch and the money changed nothing.
- **Do not delete the `EvaluationRedemption` row.** `models.py:94` carries
  `UniqueConstraint("org_id")` — one redemption per org, *ever*. That row is the
  record that this org already had its offer; clearing the org's live fields is
  what ends the regime, not erasing the history. (This is the constraint I got
  wrong in P4; read the table, not the feature.)

### 3. What `plan_tier` says while locked

A granted offer sets `plan_tier="premium"`, and nothing resets it, so an expired
org still *reads* premium — which silently grants premium seat limits and anchor
cadence (`config.py:249`, `:259`). Leave the value alone: correcting it to
`"free"` would restore the free quota and let capture resume, which is the
opposite of the owner's decision. **Say so in a comment**, because the next
reader will see `premium` on a locked org and assume it is a bug.

### Tests

Each state maps to the right `reason` · an expired evaluation locks the dashboard
· credits-exhausted does **not** lock · capture is refused after expiry ·
**an org that upgrades can capture again** (the regression test that matters) ·
the anonymous checkout flow still provisions a new org unchanged · the
`EvaluationRedemption` row survives the upgrade · the gate order in
`capture_block` is unchanged.

```bash
cd backend && DATABASE_URL=postgresql+psycopg://foxy:foxy@localhost:5433/foxy_pytest \
  python -m pytest tests/integration -q
```

The explicit `DATABASE_URL` is not optional — conftest defaults to 5432, Postgres
here listens on 5433, and without it every test dies inside `alembic upgrade
head` looking exactly like a migration fault (register #33).

**No migration.** Every column already exists.

---

## E2 — The verifier, inside the export

`GET /v1/logs/export` (`logs.py:423`) returns one JSON or CSV file. Add
`format=bundle` returning a **ZIP** — `zipfile` + `io.BytesIO`, both stdlib, no
dependency:

```
foxy-audit-export.zip
├── foxy-audit-logs.json     the existing JSON body, byte-for-byte
├── foxy_verify.py           the standalone verifier (289 lines, no deps)
└── VERIFY.txt               two commands: python foxy_verify.py foxy-audit-logs.json
```

### The distribution problem, and why it is not obvious

**`verifier/` never reaches the backend image.** `backend/Dockerfile` does
`COPY . .` from the `backend/` build context; the verifier lives at the repo
root. Two ways out, and the trade is real:

- **Vendor a copy** into `backend/app/` — self-contained, works everywhere,
  **risks drift** from `verifier/foxy_verify.py`.
- **Mount it** the way `foxy-dashboard/` is mounted (`docker-compose.prod.yml:106`,
  which documents exactly this "lives outside the build context" pattern) — no
  copy, but a deploy-time failure mode that only appears in production.

**Recommend vendoring, with a guard that makes drift impossible to merge:** a
test asserting the shipped copy is **byte-identical** to `verifier/foxy_verify.py`.
CI catches a divergence deterministically; a missing mount does not announce
itself. Overrule if you would rather not carry the duplicate.

**If the verifier cannot be read, fail the request** — 503 with a reason. Do not
emit a bundle silently missing the tool it exists to deliver; an auditor
discovering the gap later is worse than a download that refuses now.

### The rest of the claim

- **`ExportJob.type` is `String(32)`**, so recording `"bundle"` needs **no
  migration**. The model's own docstring notes the server keeps no file archive
  and a re-download re-runs the producer — the bundle must respect that and build
  in memory.
- **The passport sentence must name it.** `routers/passport.py`'s "How to verify
  this independently" currently tells the reader to run a verifier it never
  locates; a template comment marks the exact sentence. Point it at the bundle.
- **`verifier/README.md` exists** — reuse its wording for `VERIFY.txt` rather
  than writing a third description of the same two commands.
- Keep `date_from` / `date_to` working for `bundle` exactly as for `json`.

### Tests

The ZIP contains all three members · the JSON member is byte-identical to what
`format=json` returns for the same query · the vendored verifier is
byte-identical to `verifier/foxy_verify.py` · a missing verifier yields 503, not
a short bundle · **end-to-end: unzip, run `python foxy_verify.py` on the JSON
member, and assert it verifies.** That last one is the only test that proves the
issue is actually closed — the rest prove plumbing.

---

## E3 — The locked state for an expired evaluation (UI)

Consume E1. **No new lock logic.** D4 (`4fe611c`) already built all of this.

- Add `evaluation_expired` (and the credits case) to `copyFor`'s reason table in
  `foxy-dashboard/foxy-audit-premium.html`. Expiry gets the overlay and an
  **Upgrade** CTA; credits-exhausted gets the banner, not the overlay.
- The upgrade CTA must POST **`/v1/billing/upgrade-session`** — the authenticated
  route E1 added (`billing.py`, admin-only, sends `foxy_org_id`). **Not**
  `/v1/billing/checkout-session`: that one is anonymous and org-blind, and the
  webhook would provision a second empty workspace while the original stays
  locked. That is the whole defect #36 turned out to be.
- **No export action on the overlay** — withdrawn, see the banner above.
  `/v1/logs/export` is not gate-exempt and must not be made so here.
- **Desktop follows** (`desktop/dashboard.py:3078`, `:4293`): register #42 says it
  handles 402 by surfacing the message as a toast. Under the standing *web wins*
  rule it should show the same explained state. Add the audit label for any new
  action to `desktop/settings_admin.py`'s `AUDIT_LABELS` — a missing label has
  broken CI twice.

### Traps this file already has

- **Never point live text at `--muted2`** — 3.01:1 dark, 2.71:1 light, both under
  AA. Use `--muted`. `test_p1_contrast` exists to catch exactly this.
- **Do not declare a new `@media(prefers-reduced-motion:reduce)` block.**
  `test_p1_contrast` reads the *first* one in the file as the register of
  everything that loops; an earlier one silently becomes the register and the
  real checks stop running with nothing going red (register #43).
- The overlay is `z-index:9990` against `#authGate`'s `9999`. Keep that ordering —
  a signed-out visitor is not a locked customer.
- Extend the **existing two** `fetch` patches; do not add a third.

---

## Verification

```bash
# every <style> block, not the first — the dashboard has FOUR
python -c "import re,io; s=io.open('foxy-dashboard/foxy-audit-premium.html',encoding='utf-8').read(); \
  [print(i, b.count('{'), b.count('}')) for i,b in enumerate(re.findall(r'<style[^>]*>(.*?)</style>', s, re.S),1)]"

node --check <each extracted inline script>
python -m pytest foxy-dashboard/ -q          # 311 today — do not let it drop
pytest desktop                               # from the REPO ROOT; CI invokes it that way
```

**E2 needs the round trip, not just tests:** download a real bundle from a
running stack, unzip it in a directory that is *not* the repo, and run the
verifier against the logs it shipped with. If that works from a bare folder, a
stranger can do it, which is the entire point of #27.

**E3 needs screenshots in both themes, and look at them.** Use
`--force-prefers-reduced-motion` or infinite animations eat the virtual-time
budget and the capture never settles. `--screenshot` needs an absolute path.
Never `taskkill` chrome by name — it closes the owner's real browser.

**E1 deserves a live check.** Seed an org with an expired evaluation, confirm
capture is refused and `/v1/billing/access` explains why, then run the upgrade
and confirm capture resumes. That last step is the whole issue.

---

## After each merge

1. `git push origin <sha>:refs/heads/main`. Pushing to `main` deploys.
2. Watch CD to green. If **no run appears at all**, check the commit message for
   the skip marker before suspecting GitHub.
3. Move #36 / #27 to **Resolved** in `Worth Noting — Issues` with the SHA — the
   register's own rule 4. Do not delete them.
4. Re-stamp the affected area notes (`Backend`, `Dashboard`, `Verifier`,
   `Compliance Passport & Evidence`) with `updated:` / `verified-against:`.
5. Dated entry in `Devlogs\2026-08-03.md`.
