# Foxy Audit — Production Deployment (foxyaudit.tech)

Three sites, ONE backend, ONE database, deployed as a docker-compose stack on a
single VM (public IP **34.18.4.58**) behind Caddy for TLS. Separation is
folders + subdomains — never git branches, never separate databases.

| Site | Folder | URL | Serving model |
|------|--------|-----|---------------|
| 1 · Marketing | `foxy-sale-page/` | `https://foxyaudit.tech` (+`www`) | Static, served by Caddy `file_server`. Calls the backend cross-origin. |
| 2 · Customer dashboard | `foxy-dashboard/foxy-audit-premium.html` | `https://app.foxyaudit.tech` | Backend serves it at `/dashboard` (same-origin session cookie, no CORS). |
| 3 · Staff ops console | `foxy-adminpage/index.html` | `https://admin.foxyaudit.tech` | Backend serves it at `/admin/` (staff cookie `Path=/admin`). Caddy 404s everything except `/admin*` on this host. |

The stack (`deploy/docker-compose.prod.yml`): Postgres 16 → alembic migrate →
API (uvicorn, `--proxy-headers`, published loopback-only at `127.0.0.1:8085`) →
worker (grading, anchoring, rollups, traffic partitions).

**Edge proxy — two variants:**
- **Shared VM (the actual foxyaudit.tech box):** the host's existing nginx owns
  80/443 (it fronts other apps too). Install `deploy/nginx-foxyaudit.conf` as a
  vhost + `certbot --nginx` for the four hostnames (instructions in the file's
  header). The sale page is served straight from the repo checkout
  (`/home/devops/foxy-audit/foxy-sale-page`), so `git reset --hard` on deploy
  updates it with no extra copy step.
- **Dedicated VM:** the compose file ships an optional Caddy service (auto-TLS,
  owns 80/443) behind a profile: `docker compose --profile edge up -d`.

## How deploys happen (CI/CD)

Push to `main` → `.github/workflows/deploy.yml`:

1. **secret-scan** — GitLeaks over full git history
2. **dependency-scan** — Trivy filesystem scan + CycloneDX SBOM artifact
3. **quick-tests** — SDK tests + hash-chain regression (the full 80-test
   Postgres integration suite runs in parallel in `ci.yml` on the same push)
4. **image-scan** — builds `backend/Dockerfile` and Trivy-scans the image
5. **deploy** — SSH to the VM (`appleboy/ssh-action`), `git reset --hard
   origin/main`, `docker compose -f deploy/docker-compose.prod.yml --env-file
   deploy/.env up --build -d`

Scanners are report-only (`exit-code: 0`); flip to `1` to make findings block.

## One-time setup

**1. DNS** — A records, all → `34.18.4.58`:
`foxyaudit.tech`, `www.foxyaudit.tech`, `app.foxyaudit.tech`, `admin.foxyaudit.tech`.
Caddy auto-provisions Let's Encrypt certs on first request — DNS must resolve first.

**2. GitHub repo secrets** (Settings → Secrets → Actions):
- `VM_HOST1` = `34.18.4.58`
- `VM_USER1` = the VM login user
- `VM_SSH_KEY1` = private key whose public half is in the VM's `~/.ssh/authorized_keys`
- `GITLEAKS_LICENSE` — only if the repo moves into a GitHub organization

**3. On the VM** (Ubuntu assumed; ports 22/80/443 open in the cloud firewall):

```bash
# docker + compose plugin, git
curl -fsSL https://get.docker.com | sh && sudo usermod -aG docker $USER
# first clone + prod secrets (the deploy workflow refuses to run without deploy/.env)
git clone https://github.com/fatimaatta-09/Foxy-Audit.git ~/foxy-audit
cd ~/foxy-audit && cp deploy/.env.example deploy/.env && nano deploy/.env
```

Fill `deploy/.env` with strong values (`python3 -c "import secrets;print(secrets.token_urlsafe(48))"`).
`FOXY_ENV=prod` is hardcoded in the compose file, so the backend refuses to start
on weak/equal/missing secrets. **Never commit `deploy/.env`** (gitignored).

**4. First deploy + staff bootstrap** — push to `main` (or run the workflow
manually), then create the first superadmin ONCE:

```bash
cd ~/foxy-audit
docker compose -f deploy/docker-compose.prod.yml --env-file deploy/.env \
  exec foxy-backend python scripts/seed_staff.py --email you@foxy.audit --password '<strong>'
# and a first customer org if needed:
#   ... exec foxy-backend python scripts/seed_org.py --name "First Corp" --admin-email admin@corp.com --admin-password '<strong>'
```

## Security wiring worth knowing

- **Cookies:** customer `session` on `app.`, staff `foxy_staff_session` scoped to
  `Domain=admin.foxyaudit.tech; Path=/admin`. Distinct secrets enforced at startup.
- **Proxy trust:** the backend runs `--forwarded-allow-ips=*`, which is safe ONLY
  because nothing but Caddy can reach it (no published port). Caddy ignores
  client-supplied `X-Forwarded-For`, so rate-limit keys, traffic `ip_hash`, and
  `ADMIN_IP_ALLOWLIST` all see real client IPs. Don't ever publish port 8000.
- **Host guards:** Caddy 404s `/admin*` on the app host and everything except
  `/admin*` on the admin host — belt-and-braces on top of the cookie separation.
- **Marketing → backend:** the sale page auto-derives `https://app.<domain>`
  from its own hostname; `CORS_ORIGINS` in `deploy/.env` must list the marketing
  origins (it does by default).
- **Stripe:** point the webhook at `https://app.foxyaudit.tech/v1/webhooks/stripe`
  and set `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`, `STRIPE_PRICE_PRO`, and
  `STRIPE_PRICE_MAX` in `deploy/.env`. The public free tier is a seven-day trial;
  configure a real Stripe price for each self-serve paid tier before enabling its CTA.

## Post-deploy smoke checklist

1. `https://app.foxyaudit.tech/health/ready` → 200.
2. `https://foxyaudit.tech` loads; contact form submits (row in `marketing_leads`).
3. `https://app.foxyaudit.tech` → redirects to `/dashboard`; login works; Billing page loads.
4. `https://admin.foxyaudit.tech` → redirects to `/admin/`; staff login works;
   `https://app.foxyaudit.tech/admin/` → 404 (host guard).
5. A customer session cookie on any `/admin/v1/*` route → 401 (channel separation).
6. `docker compose ... ps` → `foxy-worker` healthy (heartbeat < 30 s old).

## Don't forget the worker

`foxy-worker` owns grading, anchoring, `usage_daily` rollups AND `traffic_events`
partition create/drop. If it stops for long, inserts fall into the DEFAULT
partition (never lost), but monthly partitions for months that already have rows
in DEFAULT can no longer be created — keep it running (compose restarts it).
