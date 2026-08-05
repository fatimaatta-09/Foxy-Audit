/* Foxy Audit — checkout.foxyaudit.tech deployment configuration (M3a)
 *
 * COPY THIS FILE TO THE VM AS  /home/devops/foxy-checkout-config/config.js
 * and fill in the token. Do NOT commit the filled-in version: the deploy runs
 * `git reset --hard origin/main`, so a tracked config.js would be overwritten
 * on every deploy. The compose file bind-mounts the out-of-git directory over
 * this page's config.js for exactly that reason — the same pattern the installer
 * binaries already use at /home/devops/foxy-downloads.
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
