/* Foxy Audit — checkout.foxyaudit.tech (M3a)
 *
 * The page a buyer lands on after the backend has already created a Paddle
 * transaction. Paddle has no hosted checkout: it is overlay or inline, and both
 * need Paddle.js running on a domain Paddle has approved. This is that domain,
 * and this file is everything it does.
 *
 * DELIBERATELY EXTERNAL, NOT INLINE. Keeping our own code in a file served from
 * this origin is what lets the CSP say `script-src 'self' https://cdn.paddle.com`
 * instead of opening the door to arbitrary inline script.
 *
 * THERE IS NO API KEY HERE AND THERE MUST NEVER BE ONE. The transaction was
 * created server-side; this page needs only the PUBLIC client-side token, which
 * Paddle documents as safe to expose. A `PADDLE_API_KEY` in this file would be
 * in every visitor's view-source.
 */
(function () {
  'use strict';

  var PADDLE_JS = 'https://cdn.paddle.com/paddle/v2/paddle.js';
  // A checkout that never reports `checkout.loaded` is a checkout that never
  // opened — a rejected or expired transaction is the usual reason, and Paddle
  // does not always emit an error for one. Without this the page would sit on
  // "Opening…" forever, which is the silent-nothing state this page exists to
  // avoid.
  var LOAD_TIMEOUT_MS = 15000;

  var el = function (id) { return document.getElementById(id); };
  var loadTimer = null;

  /** Show exactly one state. They are mutually exclusive by construction, so a
   *  second state can never draw on top of the first. */
  function show(state) {
    var panels = document.querySelectorAll('[data-state]');
    for (var i = 0; i < panels.length; i++) {
      panels[i].hidden = panels[i].getAttribute('data-state') !== state;
    }
    document.body.setAttribute('data-showing', state);
  }

  /** The transaction id from the URL.
   *
   *  Validated against Paddle's documented shape rather than trusted, and only
   *  ever written to the DOM with textContent. This is the page's ONLY input,
   *  so it is the page's only injection surface — and a payment page reflecting
   *  an unvalidated query parameter is how a CSP ends up load-bearing.
   */
  function transactionId() {
    var raw = '';
    try {
      raw = new URLSearchParams(window.location.search).get('_ptxn') || '';
    } catch (e) {
      return '';
    }
    return /^txn_[a-zA-Z0-9]{1,64}$/.test(raw) ? raw : '';
  }

  /** The environment, DERIVED from the token rather than configured separately.
   *
   *  Paddle documents the prefixes: `test_` is sandbox, `live_` is production.
   *  Deriving means the two can never disagree. Two independent settings would
   *  let a deployment hold a live token while claiming sandbox — which is
   *  precisely the silent mis-targeting this has to prevent, and it would fail
   *  at the worst moment, on a real card.
   */
  function environmentFor(token) {
    if (token.indexOf('test_') === 0) return 'sandbox';
    if (token.indexOf('live_') === 0) return 'production';
    return '';
  }

  function boot() {
    var cfg = window.FOXY_CHECKOUT || {};
    var token = typeof cfg.clientToken === 'string' ? cfg.clientToken.trim() : '';
    var env = environmentFor(token);
    var txn = transactionId();

    // Unconfigured beats everything else: with no token nothing can be opened,
    // and saying "this link is invalid" would blame the buyer for our config.
    if (!token || !env) {
      show('unconfigured');
      return;
    }
    if (!txn) {
      show('no-transaction');
      return;
    }

    show('loading');

    var script = document.createElement('script');
    script.src = PADDLE_JS;
    script.async = true;
    script.onerror = function () { show('provider-unreachable'); };
    script.onload = function () { openCheckout(token, env, txn); };
    document.head.appendChild(script);
  }

  function openCheckout(token, env, txn) {
    if (typeof window.Paddle === 'undefined') {
      show('provider-unreachable');
      return;
    }
    try {
      // MUST be called for sandbox and MUST NOT be called for production —
      // Paddle's own docs are explicit that a sandbox token still needs it.
      if (env === 'sandbox') {
        window.Paddle.Environment.set('sandbox');
      }
      window.Paddle.Initialize({
        token: token,
        eventCallback: function (event) {
          var name = event && event.name;
          if (name === 'checkout.loaded') {
            clearTimeout(loadTimer);
            show('open');
          } else if (name === 'checkout.completed') {
            clearTimeout(loadTimer);
            show('completed');
          } else if (name === 'checkout.closed') {
            clearTimeout(loadTimer);
            // Nothing was charged. This is a normal thing for a person to do,
            // so it gets its own state and a way back in — not an error.
            show('closed');
          } else if (name === 'checkout.error') {
            clearTimeout(loadTimer);
            show('rejected');
          }
        },
      });
      window.Paddle.Checkout.open({ transactionId: txn });
      loadTimer = setTimeout(function () { show('rejected'); }, LOAD_TIMEOUT_MS);
    } catch (e) {
      // The exception TYPE, never its message: an SDK error can quote the
      // request it failed on, and this one is rendered into the page.
      show('rejected');
    }
  }

  function wire() {
    var reopen = el('reopen');
    if (reopen) {
      reopen.addEventListener('click', function () {
        // A full reload rather than a second open() call: the SDK has already
        // been initialised and re-opening a closed overlay from a stale state
        // is exactly the sort of thing that half-works.
        window.location.reload();
      });
    }
    var retry = el('retry');
    if (retry) {
      retry.addEventListener('click', function () { window.location.reload(); });
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', function () { wire(); boot(); });
  } else {
    wire();
    boot();
  }
})();
