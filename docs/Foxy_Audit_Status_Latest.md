# Foxy Audit — Where It Stands Now (Latest Upload)

*Reviewed straight from the code in `Foxy-Audit-main__1_.zip`. Worth noting up front: this codebase now contains its own excellent internal audit document, `CODEBASE_DEEP_DIVE.md`, dated 2026-07-04, written with the same "trust the code over the docs" discipline I've been applying in these reviews. I cross-checked its key claims against the actual source (the verifier tool, the PII detector, the sale page's fake-checkout claim, test counts) and they hold up. What follows is my own synthesis of that document plus my own findings, not just a copy of it.*

---

## 1. Where It Stands, Honestly

This is now a **genuinely mature pilot-stage product**, not a prototype. The team's own phase log (reconstructed from git history) shows five completed build phases — desktop companion → walking skeleton → pilot hardening → platform/go-to-market → the production-hardening pass this deep-dive represents — and the claims mostly check out against the code. **200 integration tests** now pass in CI (up from 80 at the last review), there's a real GitHub Actions security-scanning pipeline (Gitleaks, Trivy/SBOM), and a working VM deployment for `foxyaudit.tech`.

The core trust mechanism — SDK hashes locally, backend chains the hashes, chain gets anchored externally — is real, tested, and now has something genuinely new and valuable: a **standalone, dependency-free, open-source verifier script** (`verifier/foxy_verify.py`) that lets anyone re-verify the entire ledger from a downloaded export with zero trust in Foxy's servers. This is exactly the "don't trust us, run this yourself" move I flagged earlier as the single highest-credibility addition available — and it's now built, not just recommended.

**But there are three issues serious enough that I'd call this pilot-ready, not production-ready**, and the team's own document agrees on all three.

---

## 2. What's Genuinely New and Good Since the Last Review

- **Open-source verifier tool** — real, standalone, matches what I'd suggested as the top differentiator move.
- **PII detection meaningfully expanded** — no longer just email + SSN. Now also catches phone numbers, IPv4 addresses, and credit-card-shaped number sequences. Still not exhaustive (no physical addresses, names), but a real step up from "basic pattern flagging."
- **Admin/ops console ("site 3") is real and audited** — full staff CRUD, role-gated (viewer < operator < superadmin), and every staff action writes to an append-only `admin_actions` audit trail.
- **HMAC-peppered multi-key API keys** with per-key create/rotate/revoke, replacing the old single shared key.
- **Durable grading confirmed solid** — the Postgres-outbox worker uses `FOR UPDATE SKIP LOCKED`, has retry-then-dead-letter behavior, and a heartbeat feeding the readiness probe. This was the single biggest "trust the product's own core claim" gap in earlier versions, and it's now properly built.
- **Traffic and usage rollups** are off the hot path and partitioned — sensible engineering, not just a feature checkbox.
- **200 integration tests, CI-gated** — including golden-vector and tamper-cascade tests specifically for the hash chain, which is the one piece of code that must never regress silently.

---

## 3. The Issues — Ranked by Actual Impact

### 🔴 Critical — each of these breaks a headline promise the product makes

**1. Row-Level Security is decorative, not enforced.**
RLS policies exist on the ledger tables, but both production and the test suite run as the `foxy` Postgres role, which is a **superuser** — and superusers bypass `FORCE ROW LEVEL SECURITY` entirely. Tenant isolation currently rests 100% on application code remembering to add `WHERE org_id = ...` to every query. This is exactly the load-bearing assumption I flagged in an earlier review and told you to re-audit before ever touching that database role — it's now formally confirmed as unenforced, and (per the team's own doc) it has **never actually been exercised**, meaning there's no test proving the isolation holds. For a product whose entire pitch is cryptographic trust and tenant separation, this is the top priority fix.

**2. Signup → usable API key is broken.**
Someone pays via Stripe checkout, an `Organization` row gets created — but no `ApiKey` row and no login `User` are created. The plaintext key is generated and returned only to Stripe's webhook response, which never reaches the customer's browser. This is the identical gap I flagged a while back ("people pay and get the setup?" — answer was "not yet"), and it's still open. A paying customer today has no way to actually get into the product.

**3. The desktop fox's breach reaction never fires.**
The whole "ambient guard flashes red the instant something's flagged" story — one of the product's most distinctive, demo-friendly features — is unwired. The UDP listener and handler for the `policy_breach` event exist on the fox's side, but nothing on the backend or SDK side ever emits that event, because grading happens asynchronously on the backend while the fox only listens for a synchronous local signal. The fox currently cannot react to a real breach at all.

### 🟠 High — real correctness, security, or scale problems

- **`/v1/analytics/threats` loads the entire org's ledger into memory** with no SQL-level aggregation or limit — this will fall over at real customer scale, and it's also Bearer-token-only, meaning the web dashboard can't even call it.
- **Customer login has no rate limit** (staff login does). An unthrottled login endpoint is a standard brute-force target.
- **Malformed ISO timestamps** in the analytics router (`created_at.isoformat() + "Z"` on a value that already has a timezone offset, producing `...+00:00Z`) — a small bug, but the kind that silently corrupts anything downstream parsing those timestamps.
- **The sales page's checkout is fake.** It's `setTimeout`-driven UI theater — no real Stripe call happens, "Powered by Stripe · SSL" is a false claim on the page, and the "download" link points at bare `github.com` rather than an actual installer. Combined with issue #2 above, the entire self-serve funnel — the thing I specifically recommended building out as the fastest path to revenue — doesn't actually work end-to-end yet.
- **OpenTimestamps (Bitcoin) anchoring is unimplemented** — it returns `pending` and the actual submission is a TODO, despite being one of the two anchor providers the system claims to support.
- **EVM/Sepolia anchoring is real code but switched off** (`ANCHOR_ENABLED=false`, defaults to a fake stub provider) — meaning the actual "prove it on a public blockchain" claim, while genuinely implemented, isn't live anywhere right now.

### 🟡 Medium/Low — worth fixing, not urgent

- Double unique constraint on `stripe_customer_id` across two migration files (harmless but sloppy).
- `traffic_events` only ever logs inbound direction despite having an in/out schema.
- SDK's `transport.py` is dead code — imported but never called, and targets the wrong endpoint if it ever were.
- The 14 UI mascot themes are fully built but unreachable — nothing in the shipping code ever calls `set_theme()`.
- Model-name drift: code defaults to `gemini-1.5-pro`, production actually runs `gemini-2.5-flash`, and the UI still labels it "Gemini 1.5 Pro" — a small but real case of the marketing surface lying about what's actually running.
- A handful of hardcoded leftovers from development (a dashboard string reading "Yo Fatima", an absolute file path from a specific developer's machine, a stale test API key in the Makefile) — cosmetic, but the kind of thing a technical buyer's due diligence review would notice and lose confidence over.
- Admin IP allow-listing silently allows everyone through when the list is empty — a "secure by default" trap; this should fail closed, not open.

---

## 4. Upgrades — What To Actually Prioritize Next

**This week — the three critical fixes, in this order:**
1. Fix the signup → usable key gap first. It's the single highest-leverage fix: without it, nothing else in the funnel matters, because a paying customer literally cannot use the product today.
2. Wire the fox's breach reaction. This is the best demo moment in the entire product and it currently does nothing — likely a small fix (have the grading worker fire a UDP event on breach, the same way it already updates the ledger) relative to its impact on anyone actually watching a live demo.
3. Address RLS: either fix the database role so it's not a superuser (and then re-test every place that currently relies on the bypass, especially staff cross-org reads — flagged as a real risk the last time this came up), or explicitly document that tenant isolation is app-code-enforced only and add a test that actually proves it holds. Right now it's neither properly enforced nor properly tested.

**Next few weeks:**
4. Turn the sales page's checkout from theater into a real Stripe integration, now that the backend webhook exists — these two fixes (checkout + signup gap) together are what actually make the self-serve motion real.
5. Fix `/v1/analytics/threats` to aggregate in SQL instead of loading the whole ledger into memory, and give the web dashboard a session-based way to call it instead of Bearer-only.
6. Add rate limiting to customer login, matching what staff login already has.
7. Decide the OpenTimestamps question directly: either finish the actual Bitcoin submission or drop the claim until it's real — a security product claiming a capability that silently returns "pending" forever is a credibility risk the moment a technical buyer tests it.

**Housekeeping, whenever there's spare capacity:**
- Clean up the hardcoded leftovers and the model-name drift — cheap, and exactly the kind of small inconsistency that erodes trust in a security product's polish.
- Flip the admin IP allow-list to fail closed on empty.
- Kill the dead `transport.py` code, or finish wiring it if it was meant to do something.

---

## 5. Bottom Line

The engineering discipline here is genuinely strong — the team wrote their own unflinching internal audit and it holds up against the actual source, which is a good sign about how they work. The core cryptographic spine (hash chain, durable grading, the new open-source verifier) is real and well-tested. But the three critical gaps — RLS is decorative, paying customers can't actually get in, and the fox's signature "ambient reaction" doesn't fire — mean this is not yet safe to put in front of a real paying customer or a skeptical technical buyer without those fixed first. None of the three are architecturally hard; they're finishing work on features that are already mostly built, which is a much better place to be than needing new architecture.
