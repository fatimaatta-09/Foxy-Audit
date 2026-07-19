# Prompt for Claude Code — Foxy Audit: Ship-to-Client Readiness

Paste everything below into Claude Code, run from the repo root. It has real, persistent access to
the actual files (this chat only sees whatever I upload each time), so it can verify its own work by
actually running things, not just describing them.

---

## Context

Foxy Audit is a three-tier AI compliance product: a PyQt6 desktop pet, a Python SDK
(`@foxy.audit` decorator), and a FastAPI + PostgreSQL backend. It locally hashes AI prompt/response
pairs (SHA-256), chains them sequentially so tampering is mathematically detectable, optionally
anchors the chain to a public blockchain, and lets a compliance officer export proof. The core
crypto is already verified working. What's missing is packaging, security hardening, and a genuine
first-run experience for a non-technical client who just wants to download something and have it
work.

## Your task

Take this repo from "developer can run it from source" to "a client can download one installer per
OS, enter an API key, and see the product work" — end to end, with nothing manual required from me.
Work through the sections below in order; each depends on the one before it. Do not skip the
verification step at the end of each section — actually run the thing and show me the real output,
don't just claim it works.

---

### 1. Security audit and hardening (do this FIRST, before anything ships anywhere)

- Grep the entire repo history (not just current files) for any committed secrets: API keys,
  private keys (especially any Sepolia/EVM private key), session secrets, database passwords. If
  anything is found, treat it as compromised — rotating the live value isn't enough, it must be
  scrubbed from git history (`git filter-repo` or BFG), and I need to be told exactly what was found
  and where.
- Confirm `SESSION_SECRET`, `STAFF_SESSION_SECRET`, `API_KEY_PEPPER`, and the anchoring private key
  are never present with real values in any committed file — only in `.env` (gitignored) or a
  secrets manager.
- Confirm the app actually refuses to start in production with default/placeholder secret values
  (check this is enforced in code, not just documented).
- Confirm the admin IP allow-list fails closed (empty list = deny) in production mode specifically —
  write a test if one doesn't exist.
- Confirm Row-Level Security is enforced via a non-superuser application role, not bypassed. Write
  an isolation test: two orgs, two API keys, prove org A can never retrieve org B's data through any
  route, including raw query paths.
- Run `pip-audit` (Python) and `npm audit` (if any JS deps exist) and fix or document any
  high/critical findings.

**Verification:** show me the actual grep results for secrets, the actual test run output for RLS
isolation and the fail-closed IP allow-list, and the actual audit tool output.

---

### 2. Make the SDK installable the normal way

- Test `pip install .` for the SDK in a completely clean virtual environment on a machine with no
  prior project history. Fix anything that breaks.
- Once that's clean, walk me through (don't execute without asking) the steps to publish it to PyPI
  as `foxy-audit`, including version pinning strategy for the `1.0.0` release.
- Update the README's install instructions to match whatever actually works, not what's aspirational.

**Verification:** show the actual output of a fresh `python -m venv` + `pip install .` sequence.

---

### 3. Package the desktop pet for Windows, macOS, and Linux

- Write a PyInstaller `.spec` file for `desktop/omni_fox.py`, correctly bundling PyQt6 (Qt plugins
  are the most common thing that silently breaks — use `--collect-all PyQt6` or equivalent), the
  sprite atlas assets, and all other data files it needs.
- Set up a GitHub Actions matrix workflow (`windows-latest`, `macos-latest`, `ubuntu-latest`) that
  builds all three artifacts automatically on every version tag, so this never needs to be done by
  hand again.
- **Windows:** produce a signed `.exe` (I will provide a code-signing certificate separately — for
  now, set up the workflow to accept one via a GitHub secret, and clearly flag in your output that
  an unsigned exe will trigger SmartScreen warnings and must not ship to real clients as-is).
- **macOS:** produce a notarized `.app`/`.dmg` (same caveat — I'll provide an Apple Developer ID;
  flag clearly if this step is stubbed pending that).
- **Linux:** produce a working AppImage (lowest friction of the three, no signing required).
- Build a genuine first-run flow: on first launch, if no API key is configured, show a simple
  "Paste your Foxy Audit API key" dialog instead of requiring a manually-edited config file.

**Verification:** actually run the PyInstaller build for at least the Linux target in this
environment and confirm the resulting binary launches and reaches the "enter your API key" screen.
For Windows/macOS, since you likely can't build native binaries in a Linux sandbox, verify the CI
workflow YAML is syntactically valid and show me what it will do, but be explicit that native
Windows/macOS testing needs to happen on those actual platforms before I ship them.

---

### 4. End-to-end client experience test

Simulate being a brand-new client with zero context, using only the packaged artifacts (not running
from source):

1. Install from the packaged artifact.
2. Enter an API key.
3. Trigger a real interaction through the SDK.
4. Confirm the desktop pet reacts correctly (green for clean, red for a real policy breach).
5. Export the ledger and run the standalone verifier — confirm it reports the chain intact.
6. Deliberately corrupt one exported row and re-run the verifier — confirm it correctly flags
   exactly that row as tampered.

**Verification:** show me the actual terminal output of every step. If any step requires manual
intervention, a copy-pasted value, or "just trust it," stop and tell me — that's exactly the kind of
gap that needs fixing before this ships, not glossed over.

---

### 5. Final report

Give me a plain-language summary: what's now genuinely ready to hand to a real client, what still
needs a human step (code-signing certs, Apple notarization credentials, actual PyPI publish
confirmation), and any security findings from step 1 that need my immediate attention before anyone
outside the team touches this.

---

## Ground rules while you work

- Never invent or assume an API key, certificate, or credential exists — if something requires one
  and it's missing, stop and tell me exactly what you need from me.
- Don't mark anything "done" based on a doc or comment claiming it's done — verify by actually
  running it.
- If you find a claim in the README, sale page, or docs that doesn't match what the code actually
  does, flag it explicitly rather than quietly fixing only the code or only the docs.
