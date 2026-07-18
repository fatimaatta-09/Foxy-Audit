# Foxy Audit — Marketing site (`foxy-sale-page/`)

The public marketing site served at **https://foxyaudit.tech**. Static HTML/CSS/JS — no
build step. This doc explains how it's structured, how every page connects, and what
was built.

---

## 1. The model — "card-first"

The homepage (`index.html`) is deliberately just **header + a 3-D card carousel +
footer**. Every topic is a **card**; clicking a card opens a **modal** (short blurb +
"Open →" button) that deep-links to a **dedicated page**. Nothing else scrolls on the
homepage — all the long-form content lives on the linked pages.

A **corner fox** (bottom-right, animated from the sprite atlas) greets first-time
visitors — "Click any card…" — dismissible and remembered in `localStorage`.

---

## 2. The 13 cards (carousel order)

Defined in the `SLIDES` array in `index.html`. Order follows a buyer-journey narrative
(what → how → proof → deliverable → product → pricing → demo → resources → legal):

| # | Card | Opens |
|---|------|-------|
| 1 | @foxy.audit() | `/sdk.html` |
| 2 | How it works | `/how-it-works.html` |
| 3 | Hash Chain | `/hash-chain.html` |
| 4 | Active-Defense Judge | `/judge.html` |
| 5 | Verify Chain | `/verify-page.html` |
| 6 | Compliance Passport | `/passport.html` |
| 7 | Desktop app | `/desktop.html` |
| 8 | See Pricing | `/pricing.html` |
| 9 | Book a demo | `/book-a-demo.html` |
| 10 | Documentation | `/docs.html` |
| 11 | About us | `/about.html` |
| 12 | Contact us | `/contact.html` |
| 13 | Legal & Trust | `/legal.html` |

To reorder: move the objects inside `SLIDES` (pure data). The hero **"See pricing"**
button fast-forwards the carousel to whichever card has `isPricing: true` (order-independent).

---

## 3. Pages

| File | Purpose |
|------|---------|
| `index.html` | Homepage — carousel, corner fox, cookie banner, lead/signup/contact modals. Self-contained (inline CSS/JS). |
| `sdk.html`, `hash-chain.html`, `verify-page.html`, `passport.html`, `judge.html` | The 5 feature pages (one per feature card). |
| `how-it-works.html` | The SDK → hash → grade → chain → verify pipeline. |
| `pricing.html` | Free 7-day trial, Pro, Max, custom enterprise + FAQ. Paid CTAs open the existing checkout flow. |
| `contact.html` | Support: email + Help Center + enterprise inquiries + per-plan SLA + note form. |
| `desktop.html` | The desktop pet + **real download buttons** (Windows `.exe`, Linux AppImage). |
| `about.html` | Mission + founders (Fatima Rehman, CEO; Ali Kamran, CTO). Names/roles only — no invented bios. |
| `docs.html` | "Docs coming soon" placeholder. |
| `legal.html` | Hub linking the legal pages. |
| `partnerships.html` | Partner program: hero, benefits, 3 tracks, how-it-works, application form. |
| `reviews.html` | Honest "reviews coming soon" — **no fabricated testimonials** (trust product). |
| `book-a-demo.html` | Live hash-chain demo + demo-request form (reCAPTCHA v3). |
| `welcome.html` | Post-signup onboarding: accept Terms → shown-once API key → SDK install → download → dashboard. |
| `terms.html`, `privacy.html`, `cookie-policy.html`, `acceptable-use.html`, `report-abuse.html` | Legal pages (older self-contained style). |
| `fox-reveal.html` | Legacy standalone fox animation (not linked from nav). |

---

## 4. Shared shell — `site.css`

All the card pages (not `index.html`, which is self-contained) link **`/site.css`** for
a consistent shell + components: tokens, topnav, footer, page hero (`.phead`/`.sec-eb`/
`.sec-h`), glass cards (`.gcard`), price cards, steps/flow, code card, SLA table, note
form, CTA band, buttons. Each page then adds only its own content (and a small `<style>`
block for anything page-specific, namespaced to avoid cascade collisions).

**Design tokens** (dark, warm near-black + orange):

```
--bg #0e0c0a   --surf #1c1815   --line #322b23
--ink #f7f1e8  --muted #8c8174  --fox #ff7a2e (accent)
--safe-bg #3ddc84  --warn-bg #ffc83d  --guard-bg #c9b7ff
font: Poppins (--disp / --mono)
```

**Header/footer** are copied into each page (static site, no includes). The footer is
multi-column: Product · Company (incl. Partnerships + Reviews) · Legal & Trust.

---

## 5. Forms → backend → database

Every form posts to the backend API at **`https://app.<host>/v1/…`** (the JS resolves
`window.FOXY_API || 'https://app.' + hostname`). CORS on the backend allows the
`foxyaudit.tech` origin. All submissions **persist to Postgres**:

| Form | Endpoint | Table / effect |
|------|----------|----------------|
| Partnerships apply | `POST /v1/leads` `source=partnership` | `marketing_leads` → admin inbox |
| Contact / support note | `POST /v1/leads` `source=support` | `marketing_leads` |
| Enterprise "Contact us" | `POST /v1/leads` `source=enterprise` | `marketing_leads` |
| Book a demo | `POST /v1/leads` `source=demo` (reCAPTCHA v3) | `marketing_leads` |
| Free signup | `POST /v1/signup` | `organizations` + `users` + `api_keys` |
| Welcome terms / cookie banner | `POST /v1/consent` | `consent_events` |
| Page-view beacon (consent-gated) | `POST /v1/track` | `traffic_events` |

**Priority sources** (`enterprise`, `demo`, `partnership`) also **email the founders**
(`foxyaudit@gmail.com` + any superadmins) on arrival and sort to the top of the admin
inbox — subject e.g. "🔴 Priority Partnership — <name>".

The **admin ops console** (`admin.foxyaudit.tech` → Inbox) reads these leads back.

---

## 6. Assets

| File | Use |
|------|-----|
| `logo.png` | Brand mark — favicon + topnav + footer on every page (served at `/logo.png`). |
| `ultimate_fox_spritesheet.png` | The corner-fox animation atlas (index.html). |
| `favicon.svg`, `og-image.png`, `fox-sprite.png` | Legacy / social-card / sprite assets. |

Installer downloads (`FoxyAudit-Setup.exe`, `FoxyAudit-x86_64.AppImage`) are **not** in
this folder — they're served from an out-of-git dir on the VM at `/download/` (see
`deploy/nginx-foxyaudit.conf`); the release workflow scp's them there on a version tag.

---

## 7. Cookies & consent

Geo-aware banner (GDPR opt-in for `Europe/*`, CCPA opt-out for `America/*`, else GDPR).
Choice stored in the `foxy_consent` cookie + `localStorage`; gates the first-party,
cookieless `/v1/track` analytics beacon (no ads, no third-party trackers). Server-side
audit row via `/v1/consent`. See `cookie-policy.html`.

---

## 8. Deploy

Static files are served by the VM's **host nginx** from
`/home/devops/foxy-audit/foxy-sale-page` (see `deploy/nginx-foxyaudit.conf`). Publishing:

```
git push origin <branch>:main      # → CI + CD
```

CD SSHes to the VM, `git reset --hard origin/main`, and restarts the stack; nginx serves
the updated files immediately. **Hard-refresh** after a deploy to clear the cached page.

---

## 9. Conventions

- **No fake/demo data** — ever. No fabricated testimonials, partner logos, metrics, or
  bios. Placeholder pages say so honestly ("coming soon").
- **Token-driven** — colours/fonts come from the `site.css` tokens; don't hardcode.
- **`index.html` is self-contained**; the card pages use `/site.css`. Keep new card
  pages on `site.css`.
- **No emojis as brand/UI marks** — use the logo or inline SVG line icons.
- Paid CTAs route to **Book a demo** (payment integration is parked).

---

## 10. Build log (what was done)

- **Card-first redesign** — slimmed `index.html` to header + carousel + footer; removed
  the old How/Pricing/Team/Support/CTA scroll sections.
- **13-card carousel** — original 6 feature cards + How-it-works, Desktop, Contact,
  About, Docs, Legal, Book-a-demo; each modal CTA deep-links to its page.
- **12 dedicated pages + `site.css`** shared shell.
- **Corner-fox onboarding** (greet-on-load, dismissible, localStorage).
- **Card order** set to the buyer-journey sequence (§2).
- **Logo everywhere** — `logo.png` as favicon + topnav + footer across all pages.
- **Desktop downloads** — real Windows/Linux buttons (replacing the placeholder).
- **Onboarding** `welcome.html` — Terms → key → SDK → download → dashboard.
- **Partnerships** page (full program layout) + **Reviews** page (honest placeholder) +
  footer links to both.
- **Forms wired to the DB** (§5); partnership + priority sources email the founders.
- **Hero "See pricing"** fast-forwards the carousel to the Pricing card.
- **Footer socials** — LinkedIn + X (GitHub removed).
