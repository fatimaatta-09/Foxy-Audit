# Foxy Audit — Admin console: Liquid Glass + Skeuomorphism redesign & liveliness

## Context
The 5-phase admin build shipped, but it's **structure without soul**. On dark mode the surfaces are a muddy
brown (`--bg:#140E09`), panels sit at almost the same value as the background (`--surf` is only `.40` alpha and
leans entirely on `backdrop-filter`), hairlines/rim are `.10` white (no edge definition) — flat and dead
(confirmed by the owner's screenshot). It also *feels* static: **charts render fully-drawn with no draw-in**, no
loading skeletons, **timid 1px hovers**, instant sub-tab swaps, type-agnostic toasts. The owner wants:
**(1) liveliness**, **(2) replace glassmorphism with authentic Liquid Glass**, **(3) refine the second skin into
real Skeuomorphism**, **(4) fix the dark palette** (light is already good).

**Decisions locked:** Authentic Liquid Glass (refraction + motion), **Warm-charcoal** dark base, **Polished &
professional** liveliness. This is an **admin-surface, single-file, FE-only** change — no BE, no DB.

## How the executing agent should work
Extend the **single file** `foxy-adminpage/index.html` only. **First action: `Skill: ui-ux-pro-max`** — query
`color` (validate contrast ≥4.5:1 on the new dark ramp), `style` (glass/skeuo), `animation` (motion timing). All
inline & **CSP-safe** (no CDN/library). Preserve the **token-driven, orthogonal** architecture — light = `:root`,
dark = `html[data-theme="dark"]`, skins = `html[data-skin="clay"]`; **no component may special-case a
skin/theme.** `node --check` every inline `<script>` block. Relabel the two skins in the picker (Settings ~L1268
+ top bar) to **"Liquid Glass"** and **"Skeuomorphism"** but keep internal `data-skin` values `glass`/`clay` (no
localStorage migration). Exit bar: **both skins × light/dark render cleanly across every page.**

> **Token names are load-bearing.** `--glass`, `--surf`, `--safe-bg`, `--line`, `--muted`, `--ink`, `--fox`, etc.
> are consumed inside JS template-literal `style=""` strings and by `chart()` via `_cssvar()`. **Never rename an
> existing token**; new tokens must have sane fallbacks.

---

## ⚠️ Critical technical guidance (read before coding)
1. **DO NOT animate `feTurbulence`/`feDisplacementMap`.** Animating `baseFrequency`/`scale` re-rasterizes the SVG
   filter every frame and destroys FPS. Keep the turbulence **static & seeded**. The "living" feel comes from a
   **compositor-only** CSS transform sheen (`liquidSheen`) + the pointer-tracked specular highlight — never the SVG.
2. **`backdrop-filter:url(#…)` support is inconsistent.** Gate it behind `@supports`; default `--refract` to empty
   so unsupported engines keep a clean plain frost (never a vanished panel).
3. **Refraction must never touch dense data.** `--glass-data` (used by `.clay:has(.tbl)`) must omit `url()` so
   IDs/hashes/numbers stay crisp.
4. **Lower blur** 26px→16px; the lens + specular carry the effect, not heavy blur.
5. Everything animated sits behind `prefers-reduced-motion` (a global guard already exists at ~L679–682; add
   explicit `animation:none` on the living sheen so it parks at its start frame).

---

## Orientation (verified line numbers in `foxy-adminpage/index.html`)
- Pre-paint IIFE 10–16 · `:root` glass-light 39–114 · glass-dark 117–168 · clay-light 176–194 · clay-dark 195–211
  · clay orb-calm 213 · body gradient/orbs/`drift`/grain 216–259.
- `.clay` 409–420 · `.kpi` 425–436 · `.chip` 439–450 · `.tbl` 453–466 · `rise` stagger 468–479 · `.btn`
  (hardcoded `#FF8A3D/#2A1204/#1E0E02`) 482–506 · `.cin` 508–515 · fauxselect (hardcoded `.sel`) 521–543 ·
  voxswitch (hardcoded `#FF8A3D`) 545–558 · segmented (hardcoded active text) 560–565 · charts 567–579 · modal
  580–587 · annbar 588–594 · toast 632–641 · reduced-motion guard 679–682.
- JS: `toast()` 1606 · toast markup `#toast` 1315 · `_cssvar/_chartPalette` 1622–1623 · `chart()/_chartXY/
  _chartDonut` 1637–1739 · `_bindChartTT` 1627–1636 · `go()` 1744–1752 · `setTheme/setSkin` 1954–1966. KPI values
  set via `$(id).textContent` at ~15 sites (880–884, 1027–1030, 1204…).

---

## Phase A — Foundation tokens

**A0. New shared tokens** (add once in `:root`, ~after L113):
```css
/* spacing scale (4px base) */
--s-1:4px; --s-2:8px; --s-3:12px; --s-4:16px; --s-5:20px; --s-6:24px; --s-7:32px; --s-8:40px;
/* interaction state (skins override) */
--hover-y:-3px; --press-y:1px; --press-scale:.988;
/* primary/semantic gradient endpoints + on-color (kills hardcoded hex) */
--fox-hi:#FF8A3D; --on-pri:#2A1204; --danger-hi:#EF4444; --safe-hi:#22C55E;
/* Liquid-Glass specular + refraction (clay sets these to none) */
--spec:rgba(255,255,255,.90); --spec-soft:rgba(255,255,255,.42);
--glass-tint:rgba(255,255,255,.10); --refract:url(#foxGlassRefract);
```
**Motion tokens:** `--dur-fast:.12s --dur:.18s --dur-slow:.34s --ease-out:cubic-bezier(.22,1,.36,1)
--ease-spring:cubic-bezier(.34,1.56,.64,1)`.

**Glass-light (`:root`) deltas** (light is good — only these): `--rim`→`rgba(255,255,255,.92)`;
`--glass`→`var(--refract) saturate(180%) blur(16px)`; add `--spec/--spec-soft/--glass-tint`.

**`prefers-color-scheme` boot** (edit IIFE L12–15) — OS-dark boots dark when no stored pref, without writing storage:
```js
var stored=localStorage.getItem('foxy_admin_theme');
var t=stored||((window.matchMedia&&matchMedia('(prefers-color-scheme:dark)').matches)?'dark':'light');
```

---

## Phase B — Fix the DARK palette → warm charcoal (biggest complaint)
Replace `html[data-theme="dark"]` (117–168) with a **luminance-separated warm-charcoal ladder** (depth from
elevation + shadow, not blur):
```css
html[data-theme="dark"]{
  --bg:#16130F; --bg2:rgba(18,15,12,.55);
  --surf:rgba(74,66,56,.30); --surf-solid:rgba(54,47,40,.68); --surf2:rgba(96,86,74,.16);
  --surf3:rgba(255,138,74,.16); --surf-elev:rgba(88,79,68,.60); --surf-over:rgba(102,92,80,.74);
  --head-bg:rgba(40,35,30,.74);
  --line:rgba(255,248,240,.11); --line2:rgba(255,248,240,.20); --rim:rgba(255,252,247,.14);
  --spec:rgba(255,255,255,.22); --spec-soft:rgba(255,255,255,.10); --glass-tint:rgba(255,255,255,.04);
  --grain:.20;
  --ink:#F6EFE6; --ink2:#E6DBCC; --muted:#BEAE9A; --muted2:#A2917C;   /* muted2 ≥4.5 (was ~4.0) */
  --fox:#FF8636; --fox2:#FFAD6E; --fox3:#FFC79A; --foxdeep:#7C2D12;    /* fox2 role-flip FIXED: light text-orange */
  --fox-glow:rgba(255,134,54,.50); --fox-hi:#FFA25A; --on-pri:#241000;
  --blue:#5AB0FF; --blue2:#3AA0FF;
  --accent:#FF8636; --accent-ink:#FFC79A; --accent-soft:rgba(255,138,74,.20);
  --safe-bg:#22C55E; --safe-tx:#052E16; --safe-hi:#34D77F;
  --breach-bg:#F87171; --breach-tx:#2A0505; --danger-hi:#F98A8A;
  --warn-bg:#F59E0B; --warn-tx:#2A1D00; --info-bg:#3AA0FF; --info-tx:#06121F;
  --ok:#4ADE80; --ok-soft:rgba(34,197,94,.18);
  --danger:#FCA5A5; --danger-soft:rgba(248,113,113,.18);
  --warnc:#FBBF24; --warn-soft:rgba(245,158,11,.20);
  --infoc:#93C5FD; --info-soft:rgba(59,130,246,.20);
  --neutral:#BEAE9A; --neutral-soft:rgba(255,255,255,.07);
  --shadow:rgba(0,0,0,.55);
  --clay:0 1px 0 rgba(255,244,232,.06),0 14px 34px rgba(0,0,0,.55),inset 0 1px 0 var(--rim);
  --clay-sm:0 1px 0 rgba(255,244,232,.05),0 8px 20px rgba(0,0,0,.48),inset 0 1px 0 var(--rim);
  --clay-press:inset 0 2px 8px rgba(0,0,0,.55);
  --elev-hi:0 22px 46px rgba(0,0,0,.60),inset 0 1px 0 var(--rim); --elev-btn:0 10px 22px rgba(0,0,0,.48);
  --ring:0 0 0 3px rgba(255,134,54,.48); --focus:0 0 0 3px color-mix(in srgb,var(--fox) 42%,transparent);
  --gate-overlay:rgba(8,5,3,.64);
  --glass:var(--refract) saturate(170%) blur(16px); --glass-sm:saturate(160%) blur(10px);
  --glass-data:saturate(150%) blur(14px);   /* NO url() — crisp data */
}
html[data-theme="dark"] body{
  background:radial-gradient(1100px 560px at 50% -28%, rgba(255,140,60,.10), transparent 60%),
             linear-gradient(165deg,#1A1712,#111 74%);
}
```
Audit `--fox2`-as-text uses (`.dock-user`, `.topbtn:hover`, `.kpi.hot .kval`, `.modal-x:hover`) — now
`#FFAD6E` ≈ 8.8:1 on `--surf-solid`, good.

---

## Phase C — Liquid Glass skin (rebuild `glass` default)

**C1. The CSP-safe filter** — place once as first child of `<body>` (~after L685). Pure primitives only (no
`feImage`/external/`data:` inside), so CSP-clean:
```html
<svg width="0" height="0" aria-hidden="true" focusable="false" style="position:absolute;width:0;height:0;overflow:hidden">
  <filter id="foxGlassRefract" x="-15%" y="-15%" width="130%" height="130%" color-interpolation-filters="sRGB">
    <feTurbulence type="fractalNoise" baseFrequency="0.009 0.014" numOctaves="2" seed="7" stitchTiles="stitch" result="turb"/>
    <feGaussianBlur in="turb" stdDeviation="1.1" result="soft"/>
    <feDisplacementMap in="SourceGraphic" in2="soft" scale="15" xChannelSelector="R" yChannelSelector="G"/>
  </filter>
</svg>
```
A uniform low-`scale` displacement is only *perceptible where the backdrop has contrast* (panel edges over the
orbs/other panels) — so the lens appears at the rims automatically, no ring-mask needed. `sRGB` avoids costly
gamma conversion.

**C2. Panel layering** (replace `.clay` 409–420):
```css
.clay{position:relative;background:var(--surf);
  -webkit-backdrop-filter:var(--glass);backdrop-filter:var(--glass);
  border:1px solid var(--line);border-radius:var(--r-lg);box-shadow:var(--clay);isolation:isolate;}
.clay::before,.clay::after{content:'';position:absolute;inset:0;border-radius:inherit;pointer-events:none;z-index:-1;}
/* specular edge rim + living sheen (compositor-cheap, NOT the SVG) */
.clay::before{
  background:linear-gradient(var(--glass-tint),transparent 42%),
    radial-gradient(120% 80% at var(--mx,30%) var(--my,0%), var(--spec-soft), transparent 60%);
  box-shadow:inset 0 1px 0 var(--spec), inset 0 0 0 1px rgba(255,255,255,.04);
  mix-blend-mode:screen;animation:liquidSheen 16s ease-in-out infinite alternate;}
@keyframes liquidSheen{from{transform:translate3d(-6%,-3%,0)}to{transform:translate3d(6%,4%,0)}}
/* data-dense tier: crisp, NO refraction */
.clay:has(.twrap),.clay:has(.tbl),.clay:has(.inbox-row){background:var(--surf-solid);--glass:var(--glass-data);}
.clay:has(.tbl)::before{opacity:.5}
```

**C3. `@supports` gate + fallbacks + reduced-motion:**
```css
:root{--refract: ;}     /* default OFF → --glass = "saturate() blur()" only */
@supports (backdrop-filter:url(#foxGlassRefract)) or (-webkit-backdrop-filter:url(#foxGlassRefract)){
  html[data-skin="glass"]{--refract:url(#foxGlassRefract);}
}
@supports not ((backdrop-filter:blur(1px)) or (-webkit-backdrop-filter:blur(1px))){
  :root{--surf:rgba(255,255,255,.86);--surf2:rgba(255,255,255,.7)}
  html[data-theme="dark"]{--surf:rgba(70,62,53,.92);--surf2:rgba(90,80,70,.5)}
}
@media (prefers-reduced-motion:reduce){.clay::before{animation:none}}
```
Dock/topbar/modal already read `var(--surf)`/`var(--glass)` → they inherit the frost + dark ramp automatically.

---

## Phase D — Skeuomorphism skin (refine `clay`)

**Clay-light** (176–194) and **clay-dark** (195–211) shadow tokens — layered bevel + real depression:
```css
html[data-skin="clay"]{
  --bg2:#F1E3D4;--surf:#FBF3EB;--surf-solid:#FEF8F2;--surf2:#F1E3D4;--surf3:#FBE3D0;--head-bg:#F6EADE;
  --surf-elev:#FDF6EF;--surf-over:#FFFBF6;--line:rgba(124,74,26,.15);--line2:rgba(124,74,26,.24);
  --rim:rgba(255,255,255,.95);--grain:.10;
  --glass:none;--glass-sm:none;--glass-data:none;--refract:none;--spec:none;--spec-soft:none;--glass-tint:transparent;
  --clay:0 1px 0 rgba(255,255,255,.75),0 2px 4px rgba(140,84,30,.10),0 12px 26px rgba(140,84,30,.16),inset 0 1px 0 var(--rim),inset 0 -3px 6px rgba(140,84,30,.09);
  --clay-sm:0 1px 0 rgba(255,255,255,.65),0 6px 14px rgba(140,84,30,.13),inset 0 1px 0 var(--rim),inset 0 -2px 4px rgba(140,84,30,.07);
  --clay-press:inset 0 2px 5px rgba(140,84,30,.22),inset 0 4px 9px rgba(140,84,30,.12),inset 0 -1px 0 rgba(255,255,255,.5);
  --elev-hi:0 22px 42px rgba(140,84,30,.22),0 4px 10px rgba(140,84,30,.14),inset 0 1px 0 var(--rim),inset 0 -3px 8px rgba(140,84,30,.10);
  --elev-btn:0 10px 18px rgba(140,84,30,.20),inset 0 1px 0 var(--rim);
  --hover-y:-2px;--press-y:1px;--press-scale:.99;
}
html[data-skin="clay"][data-theme="dark"]{
  --bg2:#1E1712;--surf:#241C15;--surf-solid:#2A2119;--surf2:#322619;--surf3:#3D2E1E;--head-bg:#221A13;
  --surf-elev:#2E251C;--surf-over:#352A20;--line:rgba(255,248,240,.08);--line2:rgba(255,248,240,.15);
  --rim:rgba(255,244,230,.08);--grain:.10;
  --clay:0 1px 0 rgba(255,240,225,.05),0 2px 5px rgba(0,0,0,.5),0 14px 30px rgba(0,0,0,.6),inset 0 1px 0 var(--rim),inset 0 -3px 7px rgba(0,0,0,.5);
  --clay-sm:0 1px 0 rgba(255,240,225,.04),0 8px 18px rgba(0,0,0,.5),inset 0 1px 0 var(--rim),inset 0 -2px 4px rgba(0,0,0,.4);
  --clay-press:inset 0 2px 7px rgba(0,0,0,.6),inset 0 4px 10px rgba(0,0,0,.4),inset 0 -1px 0 rgba(255,240,225,.05);
  --elev-hi:0 22px 40px rgba(0,0,0,.62),inset 0 1px 0 var(--rim),inset 0 -3px 8px rgba(0,0,0,.5);
  --elev-btn:0 10px 20px rgba(0,0,0,.5),inset 0 1px 0 var(--rim);
}
```
**Clay-only surface treatments** (add after L213) — glossy top highlight + faint SVG grain + real press:
```css
html[data-skin="clay"] .clay{position:relative;isolation:isolate}
html[data-skin="clay"] .clay::before{content:'';position:absolute;inset:0;border-radius:inherit;pointer-events:none;z-index:-1;
  background:linear-gradient(180deg,rgba(255,255,255,.55),transparent 40%);mix-blend-mode:normal;animation:none;box-shadow:none;}
html[data-theme="dark"][data-skin="clay"] .clay::before{background:linear-gradient(180deg,rgba(255,244,230,.06),transparent 38%);}
html[data-skin="clay"] .clay::after{content:'';position:absolute;inset:0;border-radius:inherit;pointer-events:none;z-index:-1;
  background-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='120' height='120'%3E%3Cfilter id='c'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.9' numOctaves='2'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23c)'/%3E%3C/svg%3E");
  background-size:120px 120px;opacity:.05;mix-blend-mode:soft-light;}
html[data-skin="clay"] .btn:active,html[data-skin="clay"] .kpi:active,html[data-skin="clay"] .dock-item:active{
  box-shadow:var(--clay-press);transform:translateY(var(--press-y))}
```
(The `data:image/svg+xml` background is already used at L256, proving CSP permits it.)

---

## Phase E — Liveliness (Polished; all behind `prefers-reduced-motion`)

**E0. Reduced-motion helper** (near `_cssvar`, ~1622):
```js
var _RM=matchMedia('(prefers-reduced-motion:reduce)'); function reduced(){return _RM.matches;}
```

**E1. Chart draw-in** — CSS (add to chart block ~579):
```css
@keyframes drawLine{to{stroke-dashoffset:0}}
@keyframes barGrow{from{transform:scaleY(0)}to{transform:scaleY(1)}}
@keyframes arcPop{from{opacity:0;transform:scale(.6)}to{opacity:1;transform:none}}
@keyframes fadeIn{to{opacity:1}}
.chart-draw path.cseries{stroke-dasharray:1;stroke-dashoffset:1;animation:drawLine .9s ease forwards}
.chart-draw path.carea{opacity:0;animation:fadeIn .6s .4s ease forwards}
.chart-draw rect.chart-dpt{transform-box:fill-box;transform-origin:bottom;animation:barGrow .55s cubic-bezier(.22,1,.36,1) both}
.chart-draw path.chart-dpt{transform-box:fill-box;transform-origin:center;animation:arcPop .5s ease both}
@media (prefers-reduced-motion:reduce){.chart-draw path.cseries,.chart-draw rect.chart-dpt,.chart-draw path.chart-dpt,.chart-draw path.carea{animation:none;stroke-dashoffset:0;opacity:1;transform:none}}
```
JS: line path gets `class="cseries" pathLength="1"` (L1696); area path `class="carea"` (L1693); bars append
`animation-delay:(bi*0.03)s` (L1708); donut arcs `animation-delay:(i*0.05)s` (L1726); in `chart()` after
`el.innerHTML=…` add `if(!reduced()) el.classList.add('chart-draw');`.
**KPI count-up** helper (route the ~15 `textContent` KPI sites through one `putKpi(id,val,fmt)` wrapper):
```js
function countUp(el,to,fmt){el=(typeof el==='string')?$(el):el;if(!el)return;fmt=fmt||num;to=Number(to)||0;
  if(reduced()){el.textContent=fmt(to);return;}
  var from=0,t0=performance.now(),D=650;
  (function step(now){var k=Math.min(1,(now-t0)/D),e=1-Math.pow(1-k,3);
    el.textContent=fmt(Math.round(from+(to-from)*e));if(k<1)requestAnimationFrame(step);})(t0);}
```

**E2. Skeleton loaders:**
```css
.skel{position:relative;overflow:hidden;border-radius:8px;background:var(--surf2)}
.skel::after{content:'';position:absolute;inset:0;transform:translateX(-100%);
  background:linear-gradient(90deg,transparent,var(--spec-soft),transparent);animation:shimmer 1.3s infinite}
@keyframes shimmer{100%{transform:translateX(100%)}}
.skel-kpi{height:64px}.skel-line{height:12px;margin:7px 0}.skel-chart{height:180px}
@media (prefers-reduced-motion:reduce){.skel::after{animation:none;opacity:.4}}
```
```js
function skeleton(el,kind,n){el=(typeof el==='string')?$(el):el;if(!el)return;
  var one={kpi:'<div class="skel skel-kpi"></div>',row:'<div class="skel skel-line"></div>',chart:'<div class="skel skel-chart"></div>'}[kind||'row'];
  el.innerHTML=Array(n||3).fill(one).join('');}
```
Call before each `await api(...)`; the loader overwrites on success.

**E3. Tactile hover/press** (token-driven; both skins differ for free):
```css
.btn:hover{background:var(--surf);border-color:var(--line2);transform:translateY(var(--hover-y));box-shadow:var(--elev-btn)}
.btn:active{transform:translateY(var(--press-y)) scale(var(--press-scale));box-shadow:var(--clay-press)}
.dock-item:hover:not(.active){transform:translateY(var(--hover-y))}
.topbtn:hover{transform:translateY(var(--hover-y))}
```

**E4. Page inner-content stagger** on `go()`:
```css
.page.active>*{animation:rise .34s both;animation-delay:calc(var(--i,0)*45ms)}
@media (prefers-reduced-motion:reduce){.page.active>*{animation:none}}
```
```js
if(!reduced()){var p=$('page-'+page);[].forEach.call(p.children,function(c,i){c.style.setProperty('--i',i);});}
```

**E5. Typed toasts + banner** — toast markup (L1315) → `<div id="toast"><span id="toastIcon"></span><span id="toastMsg"></span></div>`:
```css
#toast{display:flex;align-items:center;gap:10px;opacity:0;border-left:3px solid var(--tc,var(--fox));
  transition:transform .28s cubic-bezier(.22,1,.36,1),opacity .28s}
#toast.show{transform:translate(-50%,0);opacity:1}
#toast.success{--tc:var(--ok)}#toast.error{--tc:var(--breach-bg)}#toast.warn{--tc:var(--warnc)}#toast.info{--tc:var(--info-bg)}
#toastIcon{width:16px;height:16px;color:var(--tc,var(--fox));flex-shrink:0}
.annbar{animation:annIn .3s ease both}
@keyframes annIn{from{opacity:0;transform:translateY(-6px)}to{opacity:1;transform:none}}
@media (prefers-reduced-motion:reduce){.annbar{animation:none}}
```
```js
function toast(m,type){var t=$('toast');t.className='';if(type)t.classList.add(type);
  $('toastIcon').innerHTML=_toastIcon(type);$('toastMsg').textContent=m;
  t.classList.add('show');clearTimeout(t._t);t._t=setTimeout(function(){t.classList.remove('show');},3200);}
```

**E6. Signature interaction — pointer-tracked specular** (reuses the `::before`; feels precise/alive, not gimmicky):
```js
if(matchMedia('(hover:hover) and (pointer:fine)').matches && !reduced()){
  var raf=0;
  document.addEventListener('pointermove',function(e){
    if(raf)return;raf=requestAnimationFrame(function(){raf=0;
      var c=e.target.closest('.clay');if(!c)return;var r=c.getBoundingClientRect();
      c.style.setProperty('--mx',((e.clientX-r.left)/r.width*100).toFixed(1)+'%');
      c.style.setProperty('--my',((e.clientY-r.top)/r.height*100).toFixed(1)+'%');});
  },{passive:true});
}
```
Coarse-pointer / reduced-motion users never attach it → sheen falls back to the slow `liquidSheen` drift.

**Spacing scale — where to apply** (rhythm, not every padding): `.grid`/`.bento`/`.split` gap → `var(--s-4)`;
`.kpis`/section margin-bottom → `var(--s-5)`; `.pagehead` → `var(--s-6)`; `.panel-h` → `var(--s-3)`. Leave
chip/th/td micro-paddings (density-mode owns them, L595–598).

---

## Hardcoded-color cleanup (do all six; grep must return zero after)
Using A0 tokens: `.btn.pri` (494–496,499) → `linear-gradient(150deg,var(--fox-hi),var(--fox));color:var(--on-pri)`
and **delete** the `html[data-theme="dark"] .btn.pri{color:#1E0E02}` rule; `.btn.danger` (500) →
`linear-gradient(150deg,var(--danger-hi),var(--destructive))`; `.btn.safe` (502) →
`linear-gradient(150deg,var(--safe-hi),var(--ok))`; `.fauxselect-opt.sel` (543) →
`linear-gradient(150deg,var(--fox-hi),var(--fox));color:var(--on-pri)`; voxswitch checked track (557) →
`var(--fox-hi)/var(--fox)`; `.segbtn[aria-pressed="true"]` (564) → `color:var(--on-pri)` and delete the dark
override (565). **Verify:** grep the `<style>` for `#FF8A3D|#2A1204|#1E0E02|#EF4444|#22C55E` → 0 matches.

---

## Verification
- **Visual matrix (exit bar):** 2 skins × {light,dark} = 4 combos render cleanly across **every** page (Overview,
  OPS, Orgs/Org-360, Traffic, Staff, Data, Inbox, Settings, Revenue, Security, Audit, Leads). Screenshot each;
  dark must no longer look muddy/flat.
- **backdrop-filter:url() support:** open in the real target browsers; toggle a panel — frost must NEVER disappear
  (the `@supports` gate protects it). DevTools → Rendering to force-disable and confirm the fallback.
- **Perf:** DevTools Performance while scrolling Overview + an Orgs table; no long paint/composite; Paint-flashing
  must NOT light panel interiors on hover. (Turbulence static; `--glass-data` has no `url()`.)
- **Contrast (dark, both skins):** axe/Lighthouse — `--fox2` ≈8.8:1, `--muted2` ≈4.9:1, `--danger/--ok` ≥4.5,
  `--on-pri` on orange high. Spot-check `.kfoot`, chips, `.linklike`, `th`.
- **Data-tier:** a `.clay:has(.tbl)` panel's computed `backdrop-filter` is `saturate() blur()` only (no `url()`);
  text crisp; specular `::before` at .5 opacity.
- **Reduced-motion:** with the OS flag set, charts render final-frame, sheen/turbulence parked, skeletons static.
- **CSP:** load with console open — zero CSP violations. Filter uses only turbulence/blur/displacement.
- **Regressions:** `node --check` clean on both `<script>` blocks. FE-only → backend suite unaffected (a quick
  green run is optional reassurance).

## Branch & merge
Branch `feat/admin-liquid-glass` off `origin/main`; keep the diff inside `foxy-adminpage/index.html`; PR to `main`.
On merge CI/CD redeploys `admin.foxyaudit.tech/admin/` — hard-refresh. (This machine may need SSH for push.)

---

## Paste-ready prompt for the executing Claude
```
You're in the Foxy Audit repo. Redesign the STAFF/OPS CONSOLE look-and-feel (the single file
foxy-adminpage/index.html) following docs/ADMIN_LIQUID_GLASS_REDESIGN.md — read that file first; it's the source
of truth (verified line numbers, concrete token tables, copy-ready CSS/JS, the CSP-safe SVG technique, risks, and
verification). This is FE-only: do NOT touch the backend, the customer dashboard (foxy-dashboard/), or the
marketing site.

FIRST ACTION: Skill: ui-ux-pro-max — query `color` (validate contrast ≥4.5:1 on the new dark ramp), `style`
(glass/skeuo), `animation` (timing). Keep it in the loop.

GOAL (owner's words): add liveliness; REMOVE glassmorphism and apply authentic Liquid Glass; refine the second
skin into real Skeuomorphism; FIX the dark palette (light mode is already good). Locked decisions: authentic
Liquid Glass (refraction + motion), WARM-CHARCOAL dark base, POLISHED & professional liveliness.

CRITICAL (do not violate):
- Preserve the token-driven, orthogonal architecture (light=:root, dark=[data-theme=dark], skins=[data-skin]).
  NO component special-cases a skin/theme. Never RENAME an existing token (they're read by JS style="" strings and
  chart() via _cssvar). Relabel the picker to "Liquid Glass"/"Skeuomorphism" but KEEP data-skin values glass/clay.
- Everything inline & CSP-safe (no CDN/library). Liquid Glass refraction = one inline <svg><filter> with
  feTurbulence+feGaussianBlur+feDisplacementMap, referenced via backdrop-filter.
- DO NOT animate feTurbulence/feDisplacementMap (re-rasterizes every frame, kills FPS) — keep it static/seeded;
  the living feel comes from the compositor-only CSS liquidSheen transform + pointer-tracked specular.
- Gate backdrop-filter:url() behind @supports; default --refract empty so unsupported browsers keep clean frost.
  --glass-data (data-dense panels) must have NO url() so table text stays crisp.
- Everything animated behind prefers-reduced-motion. Add prefers-color-scheme boot so OS-dark boots dark.
- Do the full hardcoded-color cleanup (6 sites) — grep <style> for #FF8A3D|#2A1204|#1E0E02|#EF4444|#22C55E must
  return zero.

Build in the doc's phase order: A (foundation tokens + spacing/motion + prefers-color-scheme), B (warm-charcoal
dark palette — the biggest fix), C (Liquid Glass skin), D (Skeuomorphism skin), E (liveliness: chart draw-in +
count-up, skeletons, tactile press, page/tab stagger, typed toasts, pointer specular signature).

VERIFY before the PR (don't just claim it): the 4-combo visual matrix (2 skins × light/dark) across EVERY page,
screenshotted, dark no longer muddy; backdrop-filter:url() fallback (frost never vanishes); perf while scrolling
(no paint-flash on panel interiors); dark contrast ≥4.5:1 (axe/Lighthouse); data-tier panels have no url() lens;
reduced-motion parks all motion; zero CSP violations; node --check clean on both <script> blocks.

WORKFLOW: branch feat/admin-liquid-glass off origin/main, diff stays inside foxy-adminpage/index.html, PR to main.
This machine may need SSH for git push. Start by reading docs/ADMIN_LIQUID_GLASS_REDESIGN.md + the token blocks in
foxy-adminpage/index.html, then give me your Phase A+B plan before coding.
```
