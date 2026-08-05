# checkout.foxyaudit.tech — the Paddle checkout page

The page a buyer lands on after the backend has created a Paddle transaction.

**Paddle has no hosted checkout.** It is overlay or inline, and both need
Paddle.js running on a page you serve from a domain Paddle has approved. Until
this page existed, the `checkout_url` the backend returned led nowhere and **no
payment could be completed**.

Static. No build step, no framework, no backend route. Served by Caddy (site 4).

---

## The one thing you must configure

Copy `config.example.js` to the VM as
**`/home/devops/foxy-checkout-config/config.js`** and fill in one value:

```js
window.FOXY_CHECKOUT = {
  clientToken: 'test_xxxxxxxxxxxxxxxxxxxxxxxxx',
};
```

| Value | What it is |
|---|---|
| `clientToken` | Paddle's **public client-side token**. Paddle dashboard → Developer tools → Authentication → Client-side tokens. |

**The environment is derived from the token's prefix, not configured separately.**

| Prefix | Environment | `Paddle.Environment.set('sandbox')` |
|---|---|---|
| `test_` | sandbox | called |
| `live_` | production | **not** called |
| anything else, or empty | *refuses* | — |

One value, so the two cannot disagree. Two settings would let a deployment hold
a live token while still claiming sandbox, and that mismatch would only surface
on a real customer's real card.

An empty or unrecognised token makes the page say plainly that checkout is
unavailable. It never guesses an environment and never opens a checkout it
cannot complete.

### Why the config lives outside the repository

The deploy runs `git reset --hard origin/main`, so a tracked `config.js` would
be overwritten on every push. It is bind-mounted over the page's `config.js`
from an out-of-git path — the same reason the installer binaries live at
`/home/devops/foxy-downloads`. On a fresh VM the file is simply absent, the
`<script src="config.js">` 404s, and the page shows its unconfigured state.

> **Never put `PADDLE_API_KEY` in this file.** It is served to every visitor and
> would be readable in view-source. The transaction is created server-side; this
> page only opens it. Paddle documents the *client-side token* as safe to expose
> — the API key is a different credential entirely.

---

## Owner setup checklist (Paddle side)

Ordered, because each step blocks the next:

1. **Sandbox account** — `sandbox-vendors.paddle.com/signup`. No KYC needed to
   start; verification runs in parallel.
2. **Default payment link** — Paddle → Checkout → Checkout settings → Default
   payment link → `https://checkout.foxyaudit.tech/`.
   **Paddle refuses to create any transaction until this is set**, so the
   backend's `POST /transactions` fails without it. Its domain must be approved
   and must serve Paddle.js — which is exactly what this page does.
3. **Client-side token** — Developer tools → Authentication. Put it in
   `config.js` as above.
4. **Prices** — create the Pro and Max products/prices; put the `pri_…` ids in
   the backend's `PADDLE_PRICE_PRO` / `PADDLE_PRICE_MAX`.
5. **Notification destination** — point it at
   `https://app.foxyaudit.tech/v1/webhooks/paddle` and put its secret in the
   backend's `PADDLE_WEBHOOK_SECRET`.
6. **DNS** — `checkout.foxyaudit.tech` → the VM. Caddy provisions the
   certificate on first request.

---

## The states, and why each exists

A payment page that silently shows nothing is worse than one that says what went
wrong. Every state has its own message and at least one way forward.

| State | When | Way out |
|---|---|---|
| `loading` | the default; Paddle.js is loading | resolves itself |
| `open` | the overlay is up | reopen · back to plans |
| `completed` | payment taken | dashboard · home |
| `closed` | the buyer closed the overlay; **nothing charged** | reopen · back to plans |
| `no-transaction` | no `_ptxn` — somebody navigated here directly | see the plans |
| `rejected` | Paddle refused the transaction, or it never opened within 15s | start a new checkout · contact |
| `provider-unreachable` | Paddle.js did not load at all | try again · contact |
| `unconfigured` | no client token on this deployment | contact us |
| *(noscript)* | scripting is off, so the overlay can never open | contact us |

The five that report a problem carry `role="alert"`; the neutral ones do not,
because announcing "checkout is open" as an alert is noise.

---

## Security

- **One third-party origin, ever: Paddle.** The CSP denies by default and is
  scoped to this hostname, which is the whole reason the checkout is a separate
  subdomain — the marketing site, dashboard and admin console keep their own
  policies untouched.
- **No backend route.** Static file serving only, no proxy, so the origin that
  runs third-party payment script has no path to the API.

### ⚠ `'unsafe-inline'` in `script-src` is PROVISIONAL — remove it after the first real checkout

The policy currently allows `'unsafe-inline'` on `script-src`. **Nobody has yet
watched Paddle.js run under this policy**, and a CSP that silently blocks the SDK
breaks the only page in the product that can take money. So it ships loose in the
one direction that cannot be verified from a developer machine, and tight
everywhere else.

What makes that acceptable rather than lazy: none of *our* code is inline (there
are zero inline `<script>` blocks, and a guard fails if one appears), the page's
only input is `_ptxn`, which is matched against `^txn_[A-Za-z0-9]{1,64}$`, and
nothing on the page uses `innerHTML`. There is no injection sink for inline
script to be reached through.

**The exact edit, once one real sandbox checkout has completed end to end:**

1. Delete the two occurrences of ` 'unsafe-inline'` from the `script-src`
   directive — **`deploy/nginx-foxyaudit.conf`** (the live one) and
   **`deploy/Caddyfile`** (the dedicated-box copy). Leave `style-src`'s
   `'unsafe-inline'` alone; Paddle injects styles.
2. `pytest foxy-checkout -q` — `test_the_two_csp_copies_are_byte_identical`
   fails if you edit only one of them.
3. Run a sandbox checkout again and watch the browser console. If Paddle.js
   needs inline script after all, the console says so explicitly and you put it
   back with a note recording that it is required rather than untested.

Until step 3 has actually been done, this stays.
- **`_ptxn` is validated, never trusted.** It is the page's only input, matched
  against `^txn_[A-Za-z0-9]{1,64}$` and only ever written with `textContent`.
  Nothing on this page uses `innerHTML`.
- **Fonts are embedded**, not fetched. The marketing site pulls Poppins from
  Google Fonts; both consoles embed it. A payment page follows the consoles —
  one fewer origin, and it still renders as designed if Google is blocked.

## Tests

```bash
pytest foxy-checkout -q
```

Run in CI alongside the dashboard guards.
