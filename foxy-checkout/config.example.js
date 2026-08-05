/* Foxy Audit — checkout.foxyaudit.tech deployment configuration (M3a)
 *
 * COPY THIS FILE TO THE VM AS  /home/devops/foxy-checkout-config/config.js
 *
 *   sudo mkdir -p /home/devops/foxy-checkout-config
 *   sudo cp foxy-checkout/config.example.js \
 *           /home/devops/foxy-checkout-config/config.js
 *   sudo nano /home/devops/foxy-checkout-config/config.js   # paste the token
 *
 * and fill in the token. Do NOT put it in the repo: the deploy runs
 * `git reset --hard origin/main`, so a tracked config.js would be destroyed on
 * every push. That one host path is what BOTH edges read — the live host nginx
 * serves it through `location = /config.js { alias … }`, and the (unstarted)
 * Caddy path gets it bind-mounted by docker-compose. Same file either way, the
 * same pattern the installer binaries already use at /home/devops/foxy-downloads.
 *
 * No reload is needed after editing it: nginx reads the file per request.
 * `foxy-checkout/config.js` is gitignored so a local copy cannot be committed.
 *
 * ────────────────────────────────────────────────────────────────────────────
 * ONE VALUE, AND IT DECIDES THE ENVIRONMENT TOO
 * ────────────────────────────────────────────────────────────────────────────
 * `clientToken` is Paddle's PUBLIC client-side token. Paddle documents it as
 * safe to expose in frontend code — it is not a secret and it is not the API
 * key. It is the only value this page needs.
 *
 *   test_…   → the SANDBOX environment
 *   live_…   → the LIVE environment
 *
 * The page reads the environment from that prefix rather than taking it as a
 * second setting. Two settings could disagree — a live token with the
 * environment still on "sandbox" — and that mismatch would only surface on a
 * real customer's real card. One value cannot contradict itself.
 *
 * An empty or unrecognised token makes the page say so plainly and refuse to
 * open a checkout. It never guesses an environment.
 *
 * ⚠ NEVER PUT `PADDLE_API_KEY` (sdbx_… / live API key) IN THIS FILE.
 * It is served to every visitor and would be readable in view-source. The
 * transaction is already created server-side; this page only opens it.
 *
 * Where to find the token:
 *   Paddle dashboard → Developer tools → Authentication → Client-side tokens
 */
window.FOXY_CHECKOUT = {
  clientToken: '',
};
