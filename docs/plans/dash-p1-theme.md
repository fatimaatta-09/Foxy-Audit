# Dashboard P1 — Theme & design system

**Plan of record** · 2026-07-29 · MAIN chat is the committer; executors build per this file.
Base: `main` = `46e0055`. Branch: `feat/dash-p1-theme`. **Do this plan first and land it whole** —
P2 restyles every page and P3 touches flows; both sit on the token layer defined here.

**Requests source:** `G:\My Drive\Life\03 Projects\Foxy Audit\Dashboard\Changes Dashboard (Clarified for Claude).md`
**Approved mockups:** [theme spec](https://claude.ai/code/artifact/5e1c8283-2c13-464a-91c7-a76c54d0e5bd) ·
[home page](https://claude.ai/code/artifact/4dd926bd-eecb-4889-b3f3-5aeaf0aa4eaf)
**Owner references:** `image 13` (target look) · `img theme design 1` (wordmark + bloom) · `img 3` (glyphs,
colour) · `img 12` (icon style) · the soft-UI screenshot sent 2026-07-28.

---

## Context

The owner's complaints — "dark mode looks bad, the brownish hue is wrong", "use a single consistent style,
drop the others", and the accent feeling flat — have **two measurable root causes**, not a dozen taste
problems.

**Cause 1 — nothing in the palette is neutral.** Measured across the dark ramp:

| token | hex | saturation | hue |
|---|---|---|---|
| `--bg` | `#0e0c0a` | **16.7%** | 30° |
| `--bg2` | `#161310` | 15.8% | 30° |
| `--surf` | `#1c1815` | 14.3% | 26° |
| `--surf2` | `#221d18` | 17.2% | 30° |
| `--surf3` | `#2a241d` | 18.3% | 32° |
| `--line` | `#322b23` | 17.6% | 32° |
| `--ink` | `#f7f1e8` | **48.4%** | 36° |
| `--muted` | `#8c8174` | 9.4% | 32° |

A true neutral is 0% saturation. Every one of these is a **desaturated orange**. At high lightness that
reads as warm paper — which is exactly why the owner likes light mode and hates dark. At 5% lightness the
identical tint reads as mud. It also explains why the fox orange looks weak: **it is competing with a
background made of its own hue.**

**Cause 2 — six styles.** The file ships `data-theme` light|dark × `data-skin` original|clay|glass = **6
combinations over 52 tokens**. That *is* the inconsistency the owner is seeing.

**Outcome:** one skin, two themes, a neutral ground, and a soft-UI surface language that matches the
reference — with the orange finally the only saturated thing on the page.

## Owner decisions (2026-07-28, locked)

1. **One skin, keep BOTH themes.** Owner likes light, hates dark — fix dark, don't delete it.
2. **Skeuomorphic / soft UI**, per the reference screenshot — *not* the old hard-offset "clay".
3. **Barely-warm neutrals, 3–4% saturation** — not pure neutral, not cool.
4. **Decorative colour on KPI cards** (owner overrode "colour = meaning only").
5. **Rim glint = light travelling around the border**, not a flash across the face.

## ⚠ The cost nobody should discover in CI

`desktop/foxy_tokens.py::WEB` is a **byte-for-byte port of the web's dark `:root`**, and the desktop's 754
tests assert those hexes. Changing the web theme *requires* re-porting to desktop (§15). "Web wins" only
holds if the port actually follows.

**Current state:** branch `feat/dash-theme-v2` has §1 already applied and uncommitted. Either continue on
it or revert and redo on a clean `feat/dash-p1-theme`.

---

## 1 · The neutral ramp

All files: `foxy-dashboard/foxy-audit-premium.html` `:root` (line ~24).
Generated at **3.5% saturation, hue 30°** — warm enough to feel like Foxy, far below the 9–18% causing mud.

```
--bg:      #141312      (was #0e0c0a, 16.7% → 5.3%)
--bg2:     #171616
--surf:    #1b1a19      (was #1c1815, 14.3% → 3.8%)
--surf2:   #1e1d1c
--surf3:   #242322
--line:    #312f2e      (was #322b23, 17.6% → 3.2%)
--ink:     #f4f4f3      (was #f7f1e8, 48.4% → 4.3%)
--ink2:    #ccc8c4
--muted:   #938b83
--muted2:  #69625b
```

**Verified contrast on `--surf`:** ink **15.79:1** · ink2 **10.45:1** · muted **5.18:1** · fox **6.68:1**.
`--muted2` is **2.90:1 on purpose** — it is the disabled-only colour and WCAG 1.4.3 exempts inactive
controls. This matches the convention already pinned in `desktop/test_d15_contrast.py`; keep the two
consistent or the desktop audit starts lying.

**Do not** hand-tune individual values afterwards. If one needs to move, regenerate the ramp so the whole
thing stays on one saturation.

## 2 · Collapse six combinations to two

- Delete `html[data-skin="glass"]` and its dark variant (~line 134–160) **and** the `original` branch;
  promote one treatment to the base.
- Dead tokens to remove once glass is gone: `--blur`, `--rim`, `--glassfx`, `--surf-overlay`, `--gloss`,
  and the `backdrop-filter` block.
- Remove the **skin picker** from Settings and its persistence key.
- Keep `data-theme` light|dark and its toggle.
- Grep for `data-skin` afterwards — the boot script at line ~10 sets it, and the desktop's
  `foxy_tokens.py` docstring references the "original" skin by name.

## 3 · Elevation — soft UI replaces hard offsets

Today: `--clay: 9px 9px 0 rgba(0,0,0,.55)` — a **hard sticker shadow**. The reference is dual-light soft UI.
Two tokens, one the inverse of the other:

```
--lt: rgba(255,250,245,.05);   --dk: rgba(0,0,0,.66);        /* dark theme */
--raise:    -7px -7px 15px var(--lt),  7px 7px 17px var(--dk);
--raise-sm: -4px -4px  9px var(--lt),  4px 4px 10px var(--dk);
--raise-lg:-12px -12px 26px var(--lt), 12px 12px 28px var(--dk);
--sink:     inset -5px -5px 11px var(--lt), inset 5px 5px 12px var(--dk);
--sink-sm:  inset -3px -3px  6px var(--lt), inset 3px 3px  7px var(--dk);
```

Light theme flips the light source values (`--lt` near-white at .95, `--dk` a warm grey).

- **`--raise` is the default** for anything you would pick up: cards, buttons, tiles, the rail.
- **`--sink` only for genuinely recessed things**: text inputs, toggle tracks, segmented-control troughs,
  progress rails.
- `:active` swaps raise → sink. **No layout shift** — do not also translate or change padding.
- Map the existing `--shadow` / `--shadow-lg` / `--shadow-sm` / `--shadow-press` contract onto these so
  call sites keep working.

## 4 · Surfaces must converge

Soft UI only reads right when card and page are **near the same value** — the form comes from the light,
not a fill step. The `--bg → --surf3` range above is deliberately tighter than today's. Do not widen it back
out to "make cards visible"; that is what the shadows are for.

## 5 · The page wordmark (signature)

Giant, low-opacity, bleeding off the **right** edge, vertically centred on the hero. Never centred
horizontally. Carries the page's own name:

`FOXY` (home) · `THREATS` · `LEDGER` · `VERIFY` · `POLICY` · `EXPORT` · `ACCESS` · `BILLING` · `SETTINGS`

Dark: `opacity:.045` on `--ink`. Light: ~`.06` on the ink colour. `aria-hidden="true"`,
`user-select:none`, `pointer-events:none`, `white-space:nowrap`. Must not create horizontal overflow —
the hero clips it.

## 6 · The corner bloom

Orange radial bloom in the hero's top-right. **Re-tuned per theme, not ported:**
- Dark: `radial-gradient(60% 80% at 100% 0%, rgba(255,122,46,.16), transparent 70%)`
- Light: same geometry at ~`.10` — a warm wash, because a glow on white reads as a printing smudge.

## 7 · The travelling rim light

Owner-specified. Light runs **around** the card border. Implementation that works everywhere:

1. Card is `position:relative; overflow:hidden; padding:1.6px`.
2. A `.beam` child, ~190% of the card, `conic-gradient` with a bright arc, absolutely centred,
   `animation: orbit 5.5s linear infinite` (rotate transform — universally supported).
3. A `.face` child at `z-index:1` with the card's own background covers the middle, leaving only the
   ~1.6px ring visible.

- **Phase-offset each card** (`animation-delay: -1.4s / -2.8s / -4.1s`) so they don't pulse in unison.
- Hover accelerates 5.5s → 1.9s.
- Entire effect disabled under `prefers-reduced-motion`.

## 8 · The duotone glyph set

From `img 3` / `img 12`: **two layers** — a filled plate at ~18% opacity beneath a 1.6px stroked line at
~50%, bottom-right, bleeding off the corner at ~120px.

Rules so the set stays a system: one 24-unit grid · one stroke weight (1.6) · round caps and joins ·
`currentColor` only (never a literal) · same optical mass per glyph.

**Four exist** in the mockup (shield-tick, chart-nodes, document-check, lock). **~20 remain** — one per
page and per card type: threats, ledger, verify, policy, export, access, billing, settings, notifications,
key, invoice, chain, anchor, agent, quota, member, webhook, session, digest, passport.

## 9 · Colour policy

Decorative colour on KPI cards **as the owner asked** — drawn from:
```
--dec1 #5b8cff (blue)   --dec2 #9b8cff (violet)   --dec3 #ff6aa8 (pink)
```
**Red, amber and green stay reserved for status.** A decorative card must never be mistakable for a breach
warning. This is a deliberate constraint on the owner's instruction and the reason the warning cards still
read instantly beside four coloured ones. Card faces use a 145° linear gradient between the hue and a
darker stop.

## 10 · Level colours

Judge sensitivity (also used by P2 §2.7):
```
Low #5b8cff (blue) · Medium #ffc83d (amber) · High #ff4d4d (red) · Ultra #a06bff (purple)
```
Owner specified blue/red/purple; **amber is the missing step** between calm and alarm. Selected level gets
a coloured ring, a level bar fills to match, and the description text swaps — so the setting reads before
the word is parsed.

## 11 · Button glint

Fires on **release**, not while held. `:active` ends the instant the pointer lifts, which is why a
`:active::after` animation appears broken. Correct shape:

```js
btn.addEventListener("click", () => { btn.classList.remove("glinting");
  void btn.offsetWidth; btn.classList.add("glinting"); });
btn.addEventListener("animationend", () => btn.classList.remove("glinting"));
```
The reflow read is what allows re-triggering on consecutive clicks.

## 12 · Charts

- **Never `preserveAspectRatio="none"`** — it scales x and y independently, distorting stroke widths and
  squashing the curve. This is what made the first mockup's chart look wrong.
- Measure the container's real pixel width, set `viewBox` to match, redraw on `resize`.
- **Catmull-Rom spline → cubic bézier** for the line, so it is smooth rather than a jagged polyline.
- Axis labels **outside** the plot area (they collided with it before).
- Y-axis scaled to the data's actual range, not a reflexive 0–100.
- Endpoint marker with a slow pulse; crosshair + tooltip reading the real value on hover.

## 13 · Motion discipline

Motion only where it **reports something**: the endpoint pulse means "this is now"; the crosshair answers a
question; the count-up draws the eye to a number. **Nothing loops decoratively** — that is precisely what
makes a dashboard feel vibecoded, which the owner explicitly rejected. Everything behind
`prefers-reduced-motion`.

## 14 · Focus, contrast, and the soft-UI trap

Soft UI leans on shadow instead of borders, so control-vs-background contrast is inherently low — the known
weakness of this style, and it matters more on a compliance product than on a dribbble shot.

- **Depth is decorative and never the only signal** that something is interactive.
- Every text/icon pair ≥ 4.5:1 (large/UI ≥ 3:1).
- Visible `:focus-visible` on every control — a shadow change alone is not a focus indicator.
- **Add `foxy-dashboard/` contrast tests** mirroring `desktop/test_d15_contrast.py`, which caught six real
  failures on the desktop side. Parse the rendered CSS, not literals — a test comparing two constants can
  never notice a regression.

## 15 · Re-port to desktop (do not skip)

1. Re-port the new dark ramp into `desktop/foxy_tokens.py::WEB`, byte-for-byte.
2. Run the desktop suite: `PYTHONDONTWRITEBYTECODE=1 python -m pytest desktop -q` **from the repo root**.
3. Update the D15a/D15b pins asserting old hexes (`test_d15_contrast.py`), and re-verify every ratio.
4. Expect the exit-127/139 harness flake ~1 run in 6 — **re-run, do not "fix"**; it is closed as won't-fix
   and the conftest fix measured 5 crashes/10 runs against 0/12 without it.

---

## Verification

```bash
# every inline <script> in the 4,027-line single-file SPA
node --check <each extracted inline script>

# desktop must still be green after the re-port
PYTHONDONTWRITEBYTECODE=1 python -m pytest desktop -q     # from repo root, 754 tests
```

- Contrast: every text/surface pair ≥4.5:1 in **both** themes; `--muted2` still <4.5 and still
  disabled-only.
- Responsive: 375 / 768 / 1024 / 1440 — no horizontal body scroll, wordmark never causes overflow.
- Keyboard: visible focus on every control; tab order matches visual order.
- `prefers-reduced-motion`: rim light, glint, pulse and count-up all stop.
- Grep `data-skin` returns nothing but the removal commit.
- **Re-break each new guard** to prove it fails, then restore. Run verification loops with
  `PYTHONDONTWRITEBYTECODE=1` — same-length hex swaps inside one second reuse stale bytecode and produce
  fake failures.
- Push to `main` deploys production — watch the CD run to green.

## MAIN ↔ EXECUTOR protocol

1. **Every message ends with a prompt for the other side**, both directions, no exceptions. If nothing is
   queued the block says exactly that.
2. `TASK <n> — P1 §<x>` · branch `feat/dash-p1-theme` · report opens
   `TASK <n> · <branch> · <SHA> · DONE|BLOCKED`.
3. Prompts are **self-contained** — repo path, branch base, commands, hard rules. A fresh chat with no
   history must be able to start from the block alone.
4. MAIN's poller only while an executor is building; gate = FF-safe · scope grep · no-fake-data ·
   no-secret · `node --check` · desktop suite · guards re-broken · merge by SHA push · watch deploy.
5. **Stale tasks are deleted on merge, in the same action.**
