# Admin console — a page-by-page refinement pass

Planned 2026-08-04 against `main` @ `6a09434`.

## Context

The owner wants the admin console (`foxy-adminpage/index.html`) to stop looking
templated — "remove the AI slop" — **while keeping the theme and the liveliness
exactly as they are**, page by page across all 13.

### ⚠ First: the vault's checkboxes are stale, and they hid how little is left

The two admin punch-lists in the vault still show open items. Checked against the
code, nearly all of them are already done:

| Vault item | Says | Actually |
|---|---|---|
| Remove the duplicate page-name line | unchecked | **done** — `.pagehead` now holds only the watermark |
| Orange corner glow + "Foxy" watermark per page | unchecked | **done** — `--fox-glow` re-tuned per theme, `.pg-wm` with a light-mode opacity and a 620px rule |
| #6 the chips look AI-generated | open | **largely done** — round-two P3 replaced bordered translucent chips with solid fills, `border:0`, 1px shadow |
| #8 explain the Leads page | open | answered in the 2026-08-02 devlog; never was work |
| **#4 the `OK` chip is hard to see on the health hero cards** | open | **REAL.** Measured: the chip fill sits at **1.25:1** against a hero face. A UI component needs **3.0:1**. P3 gave it a boundary shadow but did not fix the fill. |

**So the leftover list is one item.** The real request is the broader one, and
this plan is scoped to that — with #4 as a known, measured starting point rather
than the whole job.

**Update the two vault lists as part of this work.** Checkboxes that lie are
worse than no checkboxes: they sent me looking for work that shipped days ago.

### Owner decisions (asked and answered 2026-08-04)

1. **Page by page, all 13.**
2. **Token values may change; the character may not.** The warm-orange identity,
   the soft-UI depth and the motion all stay. A token's *value* may be re-tuned
   where measurement demands it — #4 cannot be fixed otherwise.
3. **Copy may be rewritten — never factual claims.** Labels, microcopy, empty
   states and error wording are in scope. Numbers, statuses, product claims and
   anything legal are not; flag those instead.

---

## How `impeccable` is used here

**Mode: `Operate`.** The skill's own classification for admin, dashboards,
settings and tools: *scanability, consistency, native expectations and the real
usage scene outrank expression; brand lives in precise details.* This is the
governing frame, and it is the argument against the temptation to make an
operations console expressive. Staff read this surface at speed, under load,
often to answer "is something wrong right now".

**This is `refinement`, not `redesign`.** The skill draws the line explicitly:
refinement keeps the incumbent identity, behaviour, copy and everything outside
scope. The owner's "keep the theme and the liveliness" is exactly that. **Never
split the difference into polish on a discarded look** — either a detail is
preserved or it is deliberately replaced, never half-restyled.

### Per phase, in order

```bash
node .claude/skills/impeccable/scripts/context.mjs --target foxy-adminpage/index.html
```

Once per session, cwd at the repo root. Follow its directives; do not rerun it.
If it reports `CONTEXT_STALE`, **report it and move on** — the skill forbids
repairing drift as a side effect of a design task.

| Step | Command | For |
|---|---|---|
| 1 | `critique <page>` | UX heuristics, hierarchy, cognitive load — *what is actually wrong*, not what I guessed |
| 2 | `audit <page>` | a11y, responsive, performance — the measurable half |
| 3 | `layout` / `clarify` / `polish` | fix what 1 and 2 found |
| 4 | `reference/craft-floor.md` | **load immediately before editing UI.** Not during planning. It carries the quality floor and the absolute bans |

> **⚠ Bounded passes, not a loop.** The skill is explicit: build fully, inspect
> **once** with a batched round covering desktop and mobile together, fix
> everything it shows in **one** batch, confirm with at most one more round, then
> **stop**. Thirteen pages of open-ended self-QA would cost more than the work.

### What "AI slop" concretely means — so it can be found and removed

`critique` will surface these; naming them makes the brief real:

- **Uniform everything.** One radius, one shadow, one spacing step everywhere, so
  nothing is primary and the eye has no path.
- **Every card the same weight** — a bento grid where the most important tile
  looks exactly like the least.
- **Decorative iconography** — an icon per card because cards have icons.
- **Hedge-y microcopy** — "Manage your settings here", "View your data below".
- **Status colours that do not encode status**, or that all read alike at a glance.
- **Empty states that say "No data"** instead of what to do next.
- **Effects applied evenly** rather than where they mean something — the glow, the
  depth and the gradients should mark hierarchy, not decorate uniformly.

---

## Phases

**13 pages, one 486 KB file.** Pages are grouped by kinship so shared components
are fixed **once**, not thirteen times.

| Phase | Branch | Pages |
|---|---|---|
| **A0** | `feat/admin-system` | **no page** — the shared component layer. Must land first. |
| A1 | `feat/admin-overview` | `overview`, `health` — the bento grid and the hero cards (**#4 lives here**) |
| A2 | `feat/admin-orgs-data` | `orgs` (+ its 7 drill-downs), `data` |
| A3 | `feat/admin-signals` | `traffic`, `security`, `audit` |
| A4 | `feat/admin-money` | `revenue`, `campaigns` |
| A5 | `feat/admin-pipeline` | `leads`, `inbox` |
| A6 | `feat/admin-staff-settings` | `staff`, `settings` |

**A0 first, then A1–A6 strictly sequential.** They all edit the same file.

### A0 — the shared layer

Everything downstream inherits it, so doing it first is what stops six phases
each inventing their own chip.

- **#4, properly.** The chip fill is 1.25:1 against a hero face. Fix the *fill*,
  not only the boundary. Then measure **every** status chip against **every**
  hero face, both themes, and record the worst pair.
- The button family — primary, secondary, ghost, danger — and their hover,
  active, disabled and `:focus-visible` states.
- Tables, forms, empty states, loading states.
- The spacing and radius scale: make it a scale with steps that mean something.

---

## The rule that has already nearly wiped this file

> **Every phase edits the SAME single file, so a stale base silently reverts
> already-merged work.** This nearly destroyed the whole Phase-A tooltip/mask/
> palette system once, when a colour-pass branch turned out to be based on a
> pre-Phase-A commit.

1. **Starting:** `git fetch origin && git worktree add ../wt-<phase> -b feat/<name> origin/main`.
   Never off a stale local `main`, a sibling branch, or yesterday's base.
2. **Finishing:** `git fetch origin` **again**, then `git rebase origin/main`.
   Resolve conflicts to **preserve already-merged work** — never re-introduce
   something a prior phase removed (a killed animation, a restrained button, a
   contrast fix). **When unsure whose a hunk is, keep the prior merge's version.**
3. **Before pushing:** `git merge-base --is-ancestor origin/main HEAD` must pass,
   **and** grep for a marker from the previous phase to prove it survived.

MAIN additionally checks the **three-dot** diff (`origin/main...<branch>`) at the
gate — a two-dot diff on a stale branch shows the previous phase as deletions.

---

## Editing traps specific to this file

- **Never rename a token.** JS reads them at runtime via inline `style=""` and
  `_cssvar()`. A rename compiles fine and blanks every chart.
- **10 lines are ≥2000 characters** (base64 favicon, logo, 7 woff2 fonts; longest
  29,353). **Bash `grep` treats the file as binary** and floods. Filter by line
  length in Python, or read the `<style>` block via PowerShell UTF-8.
- **Zero hardcoded hex in component rules.** The five literals live only in token
  definitions. A bare `#` grep can never be 0 — the invariant is *no hex in a
  component rule*.
- **`.kpi::before` is the accent bar.** An element has one `::before`; do not
  claim it for something else.
- The file boots to a **login gate** on `file://` — a bare open shows nothing.
  Render through the sampler harness (below).

---

## Verification

```bash
# both <style> blocks balance — they are 7/7 and 422/422 today
python -c "import re,io; s=io.open('foxy-adminpage/index.html',encoding='utf-8').read(); \
  print([(b.count('{'),b.count('}')) for b in re.findall(r'<style[^>]*>(.*?)</style>',s,re.S)])"

node --check <each of the 2 inline scripts>
python -m pytest foxy-adminpage/ -q          # 123 guards today — do not let it drop
```

**Screenshots, both themes, and look at them.** The file gates on
`/admin/v1/auth/me`, so build a sampler from its real `<style>` blocks plus its
own component classes rather than opening the file. Use
`--force-prefers-reduced-motion` (infinite animations eat the virtual-time budget
and the capture never settles), an **absolute** `--screenshot` path, and
**never `taskkill` chrome by name** — it closes the owner's real browser.

**Measure a fill against its background, not only its ink.** This surface shipped
a chip whose text cleared 4.5:1 while the pill itself sat at **1.01:1** against
the card behind it and dissolved. #4 is the same failure, still live at 1.25:1.

**Guards are the deliverable, not the paperwork.** 123 today. Every phase adds
guards for what it fixed, and **each new guard must be made to fail on purpose
before it is trusted** — this surface has shipped a guard that asserted a
constant against itself.

---

## After each merge

1. `git push origin <sha>:refs/heads/main`. Pushing to `main` deploys.
2. Watch **CI and CD** to green. CD green does not mean CI passed.
3. Re-stamp `Admin Console\CLAUDE.md` (`updated:`, `verified-against:`) with the
   traps the phase found — not a changelog.
4. Dated entry in `Devlogs\2026-08-04.md`.
5. **Tick the stale checkboxes** in `changes Admin website (DONE).md` and
   `Admin issues new.md`, and note what was already done before this plan began.
6. Noticed-but-not-fixed → `Worth Noting — Issues`. Got it wrong →
   `Where Claude Was Wrong`.
