"""Static guards for the Paddle checkout page (M3a).

`checkout.foxyaudit.tech` is the page where somebody types a card number, and it
is the only page in this product that loads third-party script. It has no build
step and no framework, so these are the checks a merge gate can actually run:
shape, wiring, and the promises that are easy to break silently.

Modelled on `foxy-adminpage/test_admin_shell.py`.

    python -m pytest foxy-checkout -q
"""
from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
HTML = (HERE / "index.html").read_text(encoding="utf-8")
JS = (HERE / "checkout.js").read_text(encoding="utf-8")
CONFIG_EXAMPLE = (HERE / "config.example.js").read_text(encoding="utf-8")
CADDYFILE = (REPO / "deploy" / "Caddyfile").read_text(encoding="utf-8")
NGINX = (REPO / "deploy" / "nginx-foxyaudit.conf").read_text(encoding="utf-8")
COMPOSE = (REPO / "deploy" / "docker-compose.prod.yml").read_text(encoding="utf-8")

#: The one third-party origin this whole surface is allowed to touch.
PADDLE_CDN = "https://cdn.paddle.com/paddle/v2/paddle.js"


def _strip_js_comments(src: str) -> str:
    """Executable JS only.

    A guard that scans a file scans the prose explaining the guard too — this
    project has been bitten by that three times, most recently by a module
    docstring that named the very string its own guard forbade. Comments come
    out first so the rule can be documented in the file it governs.
    """
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    # `(?<!:)` so `https://…` inside a string literal is not mistaken for a line
    # comment — which it was on the first run, and which quietly deleted the very
    # Paddle URL another guard was asserting on.
    return re.sub(r"(?m)(?<!:)//.*$", "", src)


def _markup() -> str:
    """HTML with <style>, <script> and comments removed."""
    out = re.sub(r"<style[^>]*>.*?</style>", "", HTML, flags=re.S)
    out = re.sub(r"<script[^>]*>.*?</script>", "", out, flags=re.S)
    return re.sub(r"<!--.*?-->", "", out, flags=re.S)


def _states() -> list[str]:
    return re.findall(r'data-state="([a-z-]+)"', _markup())


def _panel(state: str) -> str:
    m = re.search(r'<section class="panel" data-state="%s".*?</section>' % state,
                  _markup(), re.S)
    assert m, f"panel {state} is gone"
    return m.group(0)


# ── 1 · no dead ends, no silent nothing ─────────────────────────────────────

def test_every_state_the_prompt_named_exists() -> None:
    """A payment page that silently shows nothing is worse than one that says
    what went wrong. These four are the ones a buyer can actually hit."""
    states = _states()
    for required in ("no-transaction", "rejected", "provider-unreachable", "closed"):
        assert required in states, f"no panel for {required}"


def test_every_state_says_something_and_offers_a_way_out() -> None:
    """No dead ends. Every panel needs a heading, a sentence, and at least one
    control — an error with no recovery path is where a buyer gives up."""
    for state in _states():
        panel = _panel(state)
        assert "<h1>" in panel, f"{state} has no heading"
        assert 'class="body"' in panel or 'class="spin"' in panel, f"{state} says nothing"
        actions = len(re.findall(r'class="btn[^"]*"', panel))
        if state == "loading":
            continue                       # transient; it resolves on its own
        assert actions >= 1, f"{state} is a dead end — no way forward"


def test_the_states_are_mutually_exclusive_by_default() -> None:
    """Exactly one panel may be visible without JS. If two were un-hidden, a
    second state could draw over the first and the page would lie."""
    panels = re.findall(r'<section class="panel" data-state="[a-z-]+"( hidden)?', _markup())
    visible = [p for p in panels if not p.strip()]
    assert len(visible) == 1, f"{len(visible)} panels start visible; exactly 1 may"


def test_failure_states_announce_themselves_to_a_screen_reader() -> None:
    """Visual-only error indication fails WCAG and fails a blind buyer. The
    states that report a problem carry role=alert; the neutral ones do not,
    because announcing 'checkout is open' as an alert is noise."""
    for state in ("no-transaction", "rejected", "provider-unreachable",
                  "unconfigured", "closed"):
        assert 'role="alert"' in _panel(state), f"{state} changes silently"
    for state in ("loading", "open", "completed"):
        assert 'role="alert"' not in _panel(state), f"{state} should not be an alert"


# ── 2 · no secrets, ever ────────────────────────────────────────────────────

def test_no_api_key_is_referenced_in_anything_the_browser_receives() -> None:
    """The transaction is created server-side; this page needs only the PUBLIC
    client-side token. An API key here would be in every visitor's view-source.

    Assembled from fragments and scanned over code-with-comments-stripped, so
    the files may keep explaining the rule without tripping it."""
    needle = "PADDLE" + "_API_KEY"
    files = (("index.html", HTML),
             ("checkout.js", _strip_js_comments(JS)),
             ("config.example.js", _strip_js_comments(CONFIG_EXAMPLE)))
    for name, src in files:
        assert needle not in src, f"{name} references the secret API key"
        # EVERY file, not just checkout.js. Checking only the script left the
        # config — the one file a human edits and pastes credentials into — with
        # no guard at all, which a mutation pasting `apiKey: 'sdbx_…'` walked
        # straight through.
        for prefix in ("sdbx_", "apikey_", "pdl_apikey_"):
            assert prefix not in src, f"{name} embeds a {prefix}… server-side key"
        # And the field name itself: the config declares exactly one key, and a
        # second one appearing is the shape this is guarding against.
        assert not re.search(r"\bapi_?[Kk]ey\b", src), (
            f"{name} declares an apiKey field; only clientToken belongs here"
        )


def test_the_shipped_config_defaults_to_empty() -> None:
    """An unconfigured deployment must fail honestly, not inherit somebody's
    token from the repo."""
    m = re.search(r"clientToken:\s*'([^']*)'", CONFIG_EXAMPLE)
    assert m, "config.example.js no longer declares clientToken"
    assert m.group(1) == "", "a token is committed in the example config"


def test_no_config_js_is_committed() -> None:
    """config.js is bind-mounted from OUTSIDE the repo, because the deploy runs
    `git reset --hard` and would overwrite a tracked one on every push."""
    assert not (HERE / "config.js").exists(), (
        "a config.js is committed; it would be overwritten on every deploy"
    )


# ── 3 · the environment cannot be mis-set ───────────────────────────────────

def test_the_environment_is_derived_from_the_token_prefix() -> None:
    """One configurable value, so sandbox and live cannot disagree. Two settings
    would let a deployment hold a live token while claiming sandbox, and that
    mismatch surfaces on a real customer's real card."""
    code = _strip_js_comments(JS)
    assert "'test_'" in code and "'live_'" in code, "the prefixes are gone"
    assert "sandbox" in code and "production" in code


def test_sandbox_is_set_only_for_sandbox() -> None:
    """Paddle requires Environment.set('sandbox') for sandbox and requires it
    NOT to be called for live. An unconditional call would point every live
    payment at test money; no call at all would do the reverse."""
    code = _strip_js_comments(JS)
    call = re.search(r"Paddle\.Environment\.set\(", code)
    assert call, "the sandbox switch is gone"
    before = code[:call.start()]
    assert re.search(r"if\s*\(\s*env\s*===\s*'sandbox'\s*\)\s*\{[^}]*$", before), (
        "Environment.set is called unconditionally"
    )


def test_an_unrecognised_token_refuses_rather_than_guessing() -> None:
    code = _strip_js_comments(JS)
    assert "return ''" in code, "environmentFor no longer has an unknown branch"
    assert re.search(r"if\s*\(!token\s*\|\|\s*!env\)", code), (
        "the page no longer refuses when the environment cannot be determined"
    )


# ── 4 · the page's only input is treated as untrusted ───────────────────────

def test_the_transaction_id_is_validated_not_trusted() -> None:
    """`_ptxn` is the page's single input and therefore its single injection
    surface. It is regex-checked against Paddle's documented shape."""
    code = _strip_js_comments(JS)
    assert "_ptxn" in code
    assert re.search(r"/\^txn_\[a-zA-Z0-9\]\{1,64\}\$/", code), (
        "the transaction id is no longer format-checked"
    )


def test_nothing_is_written_to_the_page_with_innerhtml() -> None:
    """textContent or nothing. This is what keeps 'unsafe-inline' in the CSP a
    convenience rather than a hole."""
    code = _strip_js_comments(JS)
    for sink in ("innerHTML", "outerHTML", "document.write", "insertAdjacentHTML"):
        assert sink not in code, f"checkout.js writes markup via {sink}"


# ── 5 · exactly one third party ─────────────────────────────────────────────

def test_the_open_timeout_stays_one_named_provisional_constant() -> None:
    """15s is the one number on this page that was NOT read off Paddle's docs —
    Paddle does not document whether a refused transaction id emits an error, so
    this timeout is what stops the page sitting on "Opening…" forever.

    It has to stay findable and stay named. Inlining it puts a magic number in a
    callback where the person who finally sees a real refused transaction will
    not think to look, and there is no other signal telling them it was a guess.
    """
    code = _strip_js_comments(JS)
    assert re.search(r"var LOAD_TIMEOUT_MS = \d+;", code), (
        "the timeout is no longer a named constant"
    )
    assert "LOAD_TIMEOUT_MS)" in code, "setTimeout no longer uses the named constant"
    assert not re.search(r"setTimeout\([^)]*,\s*\d{3,}\s*\)", code), (
        "a raw millisecond literal was inlined into a setTimeout"
    )
    # The comment is the other half: a named constant nobody knows is a guess is
    # just a tidier guess. Read the file WITH comments for this one.
    decl = JS[:JS.index("var LOAD_TIMEOUT_MS")]
    assert "PROVISIONAL" in decl[-1200:], (
        "the constant no longer says it is provisional"
    )


def test_paddle_js_is_loaded_from_paddles_own_cdn() -> None:
    """Paddle requires it; a self-hosted copy of a payment SDK would be both a
    licence problem and a stale-code problem."""
    assert PADDLE_CDN in _strip_js_comments(JS)


def test_no_other_external_origin_is_referenced_anywhere() -> None:
    """The rest of this product is CDN-free. The checkout page gets ONE
    exception and it is Paddle; anything else must be caught here, on the page
    that runs third-party script next to a card form."""
    allowed = {"cdn.paddle.com", "foxyaudit.tech", "app.foxyaudit.tech"}
    for name, src in (("index.html", HTML), ("checkout.js", _strip_js_comments(JS))):
        for host in re.findall(r"https?://([a-zA-Z0-9.-]+)", src):
            assert host in allowed, f"{name} reaches out to {host}"


def test_the_brand_font_is_embedded_not_fetched() -> None:
    """The marketing site fetches Poppins from Google Fonts; both consoles embed
    it. A payment page follows the consoles — one fewer origin in the CSP, and it
    still renders as designed when Google is blocked."""
    assert "fonts.googleapis.com" not in HTML and "fonts.gstatic.com" not in HTML
    assert HTML.count("@font-face") >= 3, "the embedded faces are gone"
    assert "data:font/woff2" in HTML


# ── 6 · the edge config ─────────────────────────────────────────────────────

def test_the_caddyfile_has_a_fourth_site_for_checkout() -> None:
    assert "checkout.foxyaudit.tech {" in CADDYFILE
    blocks = re.findall(r"(?m)^([a-z0-9_.,: -]+)\s*\{\s*$", CADDYFILE)
    assert len(blocks) == 4, f"expected 4 site blocks, found {len(blocks)}: {blocks}"


def _site_block(opener: str) -> str:
    """One site block's body, sliced by brace balance from its opening line."""
    start = CADDYFILE.index(opener)
    depth, i = 0, start
    while i < len(CADDYFILE):
        if CADDYFILE[i] == "{":
            depth += 1
        elif CADDYFILE[i] == "}":
            depth -= 1
            if depth == 0:
                return CADDYFILE[start:i + 1]
        i += 1
    raise AssertionError(f"{opener} never closes")


def _checkout_block() -> str:
    start = CADDYFILE.index("checkout.foxyaudit.tech {")
    depth, i = 0, start
    while i < len(CADDYFILE):
        if CADDYFILE[i] == "{":
            depth += 1
        elif CADDYFILE[i] == "}":
            depth -= 1
            if depth == 0:
                return CADDYFILE[start:i + 1]
        i += 1
    raise AssertionError("the checkout site block never closes")


def test_the_checkout_site_cannot_reach_the_backend() -> None:
    """Static only. The origin that runs third-party payment script has no route
    to our API at all — that is most of why it is a separate hostname."""
    assert "reverse_proxy" not in _checkout_block()
    assert "file_server" in _checkout_block()


def test_the_csp_allows_paddle_and_nothing_else() -> None:
    block = _checkout_block()
    csp = re.search(r'Content-Security-Policy "([^"]+)"', block)
    assert csp, "the checkout site has no CSP"
    policy = csp.group(1)
    assert policy.startswith("default-src 'none'"), "the CSP does not deny by default"
    hosts = set(re.findall(r"https://([a-zA-Z0-9.*-]+)", policy))
    assert hosts <= {"cdn.paddle.com", "*.paddle.com", "paddle.com"}, (
        f"the CSP allows a non-Paddle origin: {hosts}"
    )
    for directive in ("frame-ancestors 'none'", "form-action 'none'", "base-uri 'none'"):
        assert directive in policy, f"the CSP is missing {directive}"


def test_the_csp_is_scoped_to_the_checkout_site_only() -> None:
    """The separate subdomain exists precisely so the Paddle allowance does not
    touch the marketing site, the dashboard or the admin console."""
    assert CADDYFILE.count("Content-Security-Policy") == 1, (
        "a CSP has appeared outside the checkout site block"
    )
    # Read the OTHER three site blocks and prove no Paddle allowance leaked into
    # them. Scanning "everything before the checkout block" was the first
    # version and it was wrong — it matched the checkout site's own explanatory
    # comment, which sits above its opening brace.
    for other in ("foxyaudit.tech, www.foxyaudit.tech {", "app.foxyaudit.tech {",
                  "admin.foxyaudit.tech {"):
        assert other in CADDYFILE, f"{other} was disturbed"
        body = _site_block(other)
        assert "paddle" not in body.lower(), f"{other} now references Paddle"
        assert "Content-Security-Policy" not in body, f"{other} gained a CSP"


# ── 6b · the CSP lives in TWO files, and they must not drift ────────────────
# nginx is the source of truth: `caddy` sits behind profiles:["edge"] and is not
# started on the shared VM, so the nginx header is the one a customer receives.
# The Caddyfile still carries the same policy because it describes the same four
# sites for a dedicated box. Two copies of a security header that can drift is
# how one gets tightened and the live one does not.


def _nginx_server_block(server_name: str) -> str:
    """One `server { … }` block, sliced by brace balance, COMMENTS STRIPPED.

    Stripping is not tidiness. These blocks document their own rules, so a
    structural check run over the raw text can be satisfied by the sentence
    describing the rule instead of the rule: the `location /` comment contains
    `=404`, `/index.html` and `try_files`, which between them defeated both
    halves of the fallback guard until a mutation exposed it. Every caller here
    wants the directives, so they all get the directives.
    """
    at = NGINX.index(f"server_name {server_name};")
    start = NGINX.rindex("server {", 0, at)
    depth, i = 0, start
    while i < len(NGINX):
        if NGINX[i] == "{":
            depth += 1
        elif NGINX[i] == "}":
            depth -= 1
            if depth == 0:
                return _nginx_code(NGINX[start:i + 1])
        i += 1
    raise AssertionError(f"the {server_name} server block never closes")


def _nginx_code(block: str) -> str:
    """A vhost block with its `#` comments removed.

    Load-bearing. These blocks explain their own rules in prose, so a structural
    check run over the raw text is satisfied by the comment describing the rule
    rather than by the rule: the `location /` comment contains `=404`,
    `/index.html` AND `try_files`, which between them defeated both halves of the
    fallback guard. Caught by re-breaking; the mutation that swapped `=404` for
    `/index.html` came back green.
    """
    return "\n".join(re.sub(r"#.*$", "", line) for line in block.splitlines())


def _nginx_csp() -> str:
    block = _nginx_server_block("checkout.foxyaudit.tech")
    m = re.search(r'add_header Content-Security-Policy "([^"]+)"', block)
    assert m, "the checkout vhost has no CSP"
    return m.group(1)


def _caddy_csp() -> str:
    m = re.search(r'Content-Security-Policy "([^"]+)"', _checkout_block())
    assert m, "the checkout Caddy site has no CSP"
    return m.group(1)


def test_the_two_csp_copies_are_byte_identical() -> None:
    """Not "equivalent" — identical. A policy that differs by a space is a policy
    somebody has to diff by eye to compare, and the whole point of holding two
    copies is that nobody has to."""
    nginx_policy, caddy_policy = _nginx_csp(), _caddy_csp()
    assert nginx_policy == caddy_policy, (
        "the nginx and Caddy content-security policies have drifted\n"
        f"  nginx: {nginx_policy!r}\n"
        f"  caddy: {caddy_policy!r}"
    )


def test_the_nginx_csp_survives_a_404() -> None:
    """`always`, or nginx omits the header on any non-2xx. config.js legitimately
    404s on a deployment nobody has configured yet, and that response renders in
    the same browsing context as the page."""
    block = _nginx_server_block("checkout.foxyaudit.tech")
    for header in ("Content-Security-Policy", "X-Content-Type-Options",
                   "Referrer-Policy", "X-Robots-Tag"):
        m = re.search(r'add_header %s "[^"]+"([^;]*);' % header, block)
        assert m, f"{header} is missing from the checkout vhost"
        assert "always" in m.group(1), f"{header} is not set with `always`"


def test_the_checkout_vhost_is_static_and_scoped() -> None:
    """No proxy_pass: the origin running third-party payment script has no route
    to the API. And the CSP must not have leaked onto the other three vhosts."""
    block = _nginx_server_block("checkout.foxyaudit.tech")
    assert "proxy_pass" not in block, "the checkout vhost can reach the backend"
    assert "root /home/devops/foxy-audit/foxy-checkout;" in block
    assert NGINX.count("Content-Security-Policy") == 1, (
        "a CSP has appeared outside the checkout vhost"
    )
    for other in ("foxyaudit.tech www.foxyaudit.tech", "app.foxyaudit.tech",
                  "admin.foxyaudit.tech"):
        assert "paddle" not in _nginx_server_block(other).lower(), (
            f"the {other} vhost now references Paddle"
        )


def test_the_checkout_vhost_does_not_rewrite_a_missing_file_to_the_page() -> None:
    """`=404`, not the marketing vhost's `/index.html` fallback. config.js is
    absent until the owner mounts one, and the page depends on that being a clean
    404 — a fallback would hand the browser HTML to parse as JavaScript. Caddy's
    file_server 404s here too, so this is what keeps the two edges alike."""
    code = _nginx_server_block("checkout.foxyaudit.tech")
    directives = re.findall(r"try_files\s+([^;]+);", code)
    assert directives, "the checkout vhost has no try_files at all"
    for d in directives:
        assert d.strip().endswith("=404"), (
            f"try_files ends {d.strip()!r} — a missing asset can serve the page"
        )


def test_the_live_edge_serves_config_js_from_outside_the_repo() -> None:
    """The whole out-of-git design was only true on the edge that never starts.

    The compose bind-mount is on the `caddy` service, and caddy is
    `profiles: ["edge"]`. nginx serves `root …/foxy-checkout` with `try_files`,
    so without this location `/config.js` resolves INSIDE the repo working tree —
    where the file can never be, because the deploy resets it and it is
    gitignored. The page would read as permanently unconfigured in production
    while every document said otherwise.
    """
    block = _nginx_server_block("checkout.foxyaudit.tech")
    m = re.search(r"location\s*=\s*/config\.js\s*\{([^}]*)\}", block, re.S)
    assert m, "the live edge has no rule for /config.js"
    body = m.group(1)
    alias = re.search(r"alias\s+(\S+?);", body)
    assert alias, "/config.js is not aliased anywhere"
    assert alias.group(1) == "/home/devops/foxy-checkout-config/config.js", (
        f"/config.js is aliased to {alias.group(1)!r}, which is not the "
        f"out-of-git path the docs and the compose mount both name"
    )
    # Must resolve OUTSIDE the served root, or it is the repo copy again.
    root = re.search(r"root\s+(\S+?);", block).group(1)
    assert not alias.group(1).startswith(root), (
        "the alias points back inside the repo working tree the deploy resets"
    )


def test_a_missing_config_js_is_a_404_and_never_the_page() -> None:
    """The page depends on the absence being a clean 404: `<script src>` on an
    HTML body makes the browser parse markup as JavaScript. Two ways that breaks
    — a try_files fallback, or an error_page redirect — so neither may appear."""
    block = _nginx_server_block("checkout.foxyaudit.tech")
    m = re.search(r"location\s*=\s*/config\.js\s*\{([^}]*)\}", block, re.S)
    body = m.group(1)
    assert "try_files" not in body, "/config.js has a fallback and can serve HTML"
    assert "index" not in body, "/config.js can fall through to an index file"
    assert "error_page" not in block, (
        "an error_page would turn the missing-config 404 into an HTML body"
    )
    # And it must not declare its own add_header: nginx drops every inherited
    # header in a location that sets one, which would ship this response bare.
    assert "add_header" not in body, (
        "/config.js sets its own header and therefore loses the server-level CSP"
    )


def test_both_edges_read_the_same_host_path() -> None:
    """`=404` parity was the standard set for these two edges; the config path is
    the same question. One host file, so putting it there configures whichever
    edge happens to be running."""
    block = _nginx_server_block("checkout.foxyaudit.tech")
    nginx_path = re.search(r"alias\s+(\S+?);", block).group(1)
    m = re.search(r"-\s*(/home/devops/\S+?):/srv/checkout/config\.js:ro", COMPOSE)
    assert m, "the caddy edge no longer mounts an out-of-git config"
    assert m.group(1) == nginx_path, (
        f"the two edges read different files: nginx {nginx_path!r} vs "
        f"compose {m.group(1)!r}"
    )


def test_config_js_is_gitignored() -> None:
    """Not a secret — Paddle's client-side token is public by design — but a
    deployment config in the repo is wrong regardless, and gitleaks would not
    flag it, so nothing else would catch a `git add -A`.

    Asked of git rather than of the .gitignore text. A substring check passed
    happily when the pattern was mutated to `config.jsx`, because the old string
    is still inside the new one; and a text match cannot see a later negation
    that re-includes the path anyway. `git check-ignore` answers the question
    that actually matters.
    """
    proc = subprocess.run(["git", "check-ignore", "-q", "foxy-checkout/config.js"],
                          cwd=REPO, capture_output=True)
    assert proc.returncode == 0, (
        "git does not ignore foxy-checkout/config.js, so a real deployment "
        "config dropped into the served directory can be committed"
    )


def test_certbot_is_told_about_the_new_hostname() -> None:
    """The install comments are the runbook. A hostname missing from the certbot
    line is a hostname with no certificate, which on a payment page is fatal."""
    head = NGINX[:NGINX.index("# ── Site 1")]
    assert "-d checkout.foxyaudit.tech" in head, (
        "certbot is never told to issue a certificate for checkout."
    )


def test_compose_mounts_the_page_and_its_out_of_git_config() -> None:
    assert "../foxy-checkout:/srv/checkout:ro" in COMPOSE, (
        "the page is not mounted, or is not mounted as a directory"
    )
    assert "/home/devops/foxy-checkout-config/config.js:/srv/checkout/config.js:ro" in COMPOSE, (
        "the out-of-git config is not mounted over the page's config.js"
    )


# ── 7 · it parses, and it balances ──────────────────────────────────────────

@pytest.mark.skipif(not shutil.which("node"), reason="node is not installed")
def test_the_script_parses() -> None:
    proc = subprocess.run(["node", "--check", str(HERE / "checkout.js")],
                          capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr


def test_the_page_has_no_inline_script_to_check() -> None:
    """Every script on this page is external, which is what lets the CSP lean on
    'self' rather than on inline allowances for OUR code."""
    inline = [m for m in re.findall(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>",
                                    HTML, re.S) if m.strip()]
    assert inline == [], f"{len(inline)} inline script blocks appeared"


def test_the_style_block_balances() -> None:
    styles = "".join(re.findall(r"<style[^>]*>(.*?)</style>", HTML, re.S))
    assert styles.count("{") == styles.count("}"), (
        f"unbalanced braces: {styles.count('{')} open, {styles.count('}')} close"
    )


def test_the_touch_targets_are_large_enough() -> None:
    """44px, on the page where a mis-tap costs a sale."""
    styles = "".join(re.findall(r"<style[^>]*>(.*?)</style>", HTML, re.S))
    assert re.search(r"\.btn\{[^}]*min-height:44px", styles, re.S), (
        "the 44px minimum on .btn is gone"
    )


if __name__ == "__main__":  # pragma: no cover
    sys.exit(pytest.main([__file__, "-q"]))
