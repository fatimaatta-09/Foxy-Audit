# Dashboard P2 — Structure, navigation & page content

**Plan of record** · 2026-07-29 · MAIN chat is the committer; executors build per this file.
Branch: `feat/dash-p2-structure`. **Depends on P1** (`docs/plans/dash-p1-theme.md`) — every surface here is
styled by P1's token layer. Building P2 first means building it twice.

**Requests source:** `G:\My Drive\Life\03 Projects\Foxy Audit\Dashboard\Changes Dashboard (Clarified for Claude).md`
**Home-page reference:** [approved mockup](https://claude.ai/code/artifact/4dd926bd-eecb-4889-b3f3-5aeaf0aa4eaf)
**Primary file:** `foxy-dashboard/foxy-audit-premium.html` (single-file SPA, ~4,027 lines).

---

## Context

The dashboard shows too much on some pages, too little on others, different things at different screen
sizes, and repeats itself constantly — the page name appears twice, tables scroll forever, and most pages
open with a one-line blurb that tells the reader nothing.

The owner's underlying complaint is that it reads as **AI-generated**: explanatory filler nobody asked for,
cards that state the obvious, and copy that sounds like it is selling the feature back to the person
already using it. Most of this plan is **deletion**.

**Governing rule for this plan:** if a line of text explains what the reader can already see, it goes.

---

## 1 · Global chrome

**1.1 · Nav icon alignment.** Sidebar icons sit visually off-axis. Normalise to one optical grid — equal
box size, centred glyphs, consistent stroke weight (P1 §8).

**1.2 · Top bar format.** Exactly: `Foxy Audit │ <Page name> ●` with the amber dot. Nothing else.

**1.3 · Delete the duplicate page-name line.** Every page currently repeats its own name below the top bar.
The top bar already says it. Remove sitewide.

**1.4 · Delete the one-line info blurbs** from every page. These are the single biggest contributor to the
AI-generated feel.

**1.5 · Sidebar notification panel** + a full notifications page. Unread indicator on the rail icon. The
page is paginated (§3). Panel shows recent; page shows all with filters.

## 2 · Responsive consistency

The owner's exact complaint: *"different screen sizes show different info — some crowded, some too empty."*

- **One information set at every breakpoint.** Cards reflow; they never disappear.
- Test at **375 / 768 / 1024 / 1440**.
- No horizontal body scroll at any width. Wide content (tables, charts) scrolls **inside its own container**.
- The rail collapses to a bottom bar or drawer under ~820px — but every destination stays reachable.

## 3 · Pagination everywhere

One component, one position, one behaviour, on: **ledger · export history · daily usage · notifications ·
audit log · login history · API keys** — anything that scrolls.

- Show `1–20 of N`. A count you can trust beats infinite scroll.
- Keyboard operable, `aria-current="page"` on the active number.
- Server-side where the endpoint already supports it (`/v1/logs` takes `page` + `limit`).

## 4 · Home — de-crowd

**4.1 · Emergency and critical only.** Everything else moves to its own page. Home answers "is anything
wrong right now", not "here is everything we know".

**4.2 · Every major box is clickable → its sidebar page.** Owner-requested. `role="button"`,
keyboard-activatable, cursor change, and a real hover state.

**4.3 · Delete "114 events logged · 0 pending grading"** (owner-flagged screenshot).

**4.4 · Capture coverage rebuilt** per `img 2` and P1 §12 — smooth spline, gridlines, hover readout. The
current one is the page's worst element.

**4.5 · Bottom table becomes "Needs your attention"** — blocked/redacted rows only, ~3 rows, with a link to
the full ledger. It is not a mini-ledger.

**4.6 · KPI cards carry a trend line** ("▲ 12% vs last week", "1 expires in 6d") so a number says whether
it is good news without opening the page.

## 5 · Ledger page

**5.1** Rewrite the AI-sounding copy the owner flagged (`image 3`).
**5.2** **Verified state animates** (`img 4`): ring sweeps → turns green → tick draws. Respect
reduced-motion (instant final state, no animation).
**5.3** Pagination per §3.
**5.4** Row focus ring — rows are keyboard-reachable and currently show nothing.

## 6 · Verify page

Owner flagged seven separate problems here; it is the worst page in the product.

**6.1** Rename the control to **"Anchor now"**, strip the surrounding explanation (`imag 5`).
**6.2** Delete the useless headline (`img 6-1`).
**6.3** Delete the blockchain paragraph — *"Each receipt records your ledger head on a public blockchain…"*
(`img 7`).
**6.4** Delete the flagged block (`img 8`).
**6.5** **Two controls do the same thing** (`img 9`) — either remove one or make them genuinely different.
Decide which, and say so in the commit.
**6.6** Hero card wastes vertical space (`img 10`) — tighten.
**6.7** Redesign the AI-looking card (`img 11`) and fix its orientation.

## 7 · Policy page

**7.1 · Move judge sensitivity and alert controls to Settings** (`img 17`). They are not policy.
**7.2 · Sensitivity becomes presets** — **Low / Medium / High / Ultra**, colour-coded per P1 §10. The raw
"tokens per interaction" number is **not user-editable**.
**7.3 · Cut the filler** — enforcement mode loses "highest protection", confidence threshold loses its
explanatory paragraph.
**7.4 · Explain what the modes actually do.** The owner could not tell whether changing them had any
effect. That is a **product failure, not a copy problem** — each mode needs a concrete one-liner stating
what changes ("Blocks anything scoring 40+"), and the UI should show the consequence.
**7.5 · Each of the two settings gets its own card.**
**7.6 · Judge model pickers list real available models** per provider (`img 17-1`) — e.g. Gemini variants,
OpenAI variants. Read the actual supported list from the backend; do not hardcode a guess.
**7.7 · Replace the flagged card** (`IMG 16`) with the privacy-policy content, restyled.

## 8 · Export page

**8.1** Restyle the default browser date picker (`img 18`) to match the theme.
**8.2** Redesign the passport card as a **credit-card treatment** (`img 19`).
**8.3** Delete *"Passports are generated on demand and open in a new tab…"*.
**8.4** Fix the stray orange line on the flagged button (`img 20`).
**8.5** **Fix the JSON export emitting a single line of info** — this is a real bug, not styling. Verify the
export actually contains the full record set.
**8.6** Pagination on export history (§3).

## 9 · Access page

**9.1** Colour the top four cards (P1 §9 palette).
**9.2** **Delete the duplicate "new key" button** — keep the top one, remove the lower one on the
"Your API Key" card.
**9.3** "Regenerate 2FA" uses a default dropdown — restyle it, **and clarify what it does**. The owner could
not tell. If its purpose is unclear to the person who commissioned it, the label is wrong.
**9.4** "Connect the SDK" gains a blog link (both dashboard and sales site). Buttons now; the post is
written after the site is done.

## 10 · Billing page

**10.1** Add colour (currently none at all).
**10.2** **"Upgrade plan" gets its own in-dashboard page** showing real upgrade options. It must **not**
bounce to the marketing site's pricing page.
**10.3** Pagination on daily usage (§3).
**10.4** Promote the buried info block (`Changes Dashboard-1`) into a prominent part of the page.

## 11 · Settings — restructure

**11.1 · Single column.** Nothing side by side. The owner's exact instruction.
**11.2 · Real sections**, each visually separated:
- **Account & identity** — profile, org ID (show/hide), email
- **Security & 2FA** — password, MFA, sessions, devices, login history
- **Notifications** — every toggle, and each one must actually work (P3 §6)
- **Judge & policy** — the controls moved from Policy (§7.1)
- **Danger zone** — delete account, revoke everything

**11.3** Fix the empty space around "Change password".
**11.4** Org ID gets show/hide.
**11.5** **Sensitive reveals require 2FA first** — password change and org ID reveal both gate on step-up.
Backend already gates seven endpoints this way; reuse it, do not invent a second path.

## 12 · The Compliance Passport PDF

Owner's verdict on the current output: *"looks AI-generated, not like a professional company document — no
branding, no verification signature, no stamps."* It is also the product's **strongest sales asset**, so
this matters more than its size suggests.

**12.1** Foxy wordmark and brand header.
**12.2** An issued-by block — org, period, generated-at, generated-by.
**12.3** A verification signature and seal/stamp treatment.
**12.4** The verify URL + chain hash, so a third party can independently check it.
**12.5** Page numbering and a proper cover.
**12.6** **Deterministic content only.** This is evidence. Every number comes from the existing
`passport.py` counters; nothing is generated prose. (If the Copilot narrative from
`docs/plans/foxy-copilot-agents.md` lands later, it sits in a clearly-labelled "Summary (generated)" block
and never replaces a computed figure.)

---

## Verification

- `node --check` every inline `<script>` in changed HTML.
- **Responsive matrix**: 375 / 768 / 1024 / 1440 × every page — the *same information* at each, no
  horizontal body scroll.
- **Deletion audit**: grep for the removed strings ("events logged", "generated on demand", the blockchain
  paragraph) — zero hits.
- Pagination present and keyboard-operable on all seven surfaces in §3.
- Every Home KPI routes to the right page by mouse **and** keyboard.
- JSON export contains the full record set (§8.5) — assert against a known fixture, not by eye.
- Passport PDF renders with branding, signature and stamps; every figure traceable to `passport.py`.
- No fake/placeholder data anywhere; honest empty states.
- Push to `main` deploys production — watch the CD run to green.

## MAIN ↔ EXECUTOR protocol

1. **Every message ends with a prompt for the other side**, both directions. If nothing is queued, say so
   explicitly.
2. `TASK <n> — P2 §<x>` · branch `feat/dash-p2-structure` · report opens
   `TASK <n> · <branch> · <SHA> · DONE|BLOCKED`.
3. Prompts are self-contained — a fresh chat with no history starts from the block alone.
4. Gate: FF-safe · scope grep · no-fake-data · no-secret · `node --check` · responsive pass · merge by SHA
   push · watch deploy.
5. Stale tasks are deleted on merge, in the same action.
6. **Split by page, not by change type** — one branch per page keeps the diff reviewable and the scope grep
   meaningful.
