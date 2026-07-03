# Foxy Audit — Three-Site Deployment Split

How the three sites deploy to three subdomains against ONE backend and ONE database
(Phase 4 handoff item 5). The separation is folders + subdomains — never git branches,
never separate databases.

| Site | Folder | Subdomain | Serving model |
|------|--------|-----------|---------------|
| 1 · Marketing | `foxy-sale-page/` | `foxyaudit.com` | Static hosting (any CDN). Calls the backend cross-origin. |
| 2 · Customer dashboard | `foxy-dashboard/foxy-audit-premium.html` | `app.foxyaudit.com` | Served BY the backend at `GET /dashboard` (same-origin — session cookie needs no CORS). |
| 3 · Staff ops console | `foxy-adminpage/index.html` | `admin.foxyaudit.com` | Served BY the backend at `GET /admin/` (same-origin — staff cookie has `Path=/admin`). |

Sites 2 and 3 are the SAME backend process behind two hostnames: point both
`app.foxyaudit.com` and `admin.foxyaudit.com` at it. The `/admin` mount carries its own
isolated middleware stack (distinct cookie + secret + CORS + IP guard), so hostname
routing needs no special rules — but you MAY additionally block `/admin/*` on the
`app.` vhost at the proxy for belt-and-braces.

## Backend environment (prod)

`FOXY_ENV=prod` makes startup FAIL unless all of this is set correctly (see
`backend/app/config.py::_require_secure_prod`) and forces TLS-only cookies:

```bash
FOXY_ENV=prod
DATABASE_URL=postgresql+psycopg://…                 # managed Postgres 16
SESSION_SECRET=<strong random>                      # customer cookie
STAFF_SESSION_SECRET=<different strong random>      # staff cookie — MUST differ
API_KEY_PEPPER=<strong random — set once, never rotate casually>
STAFF_COOKIE_DOMAIN=admin.foxyaudit.com             # staff cookie never leaves the admin host
CORS_ORIGINS=https://foxyaudit.com                  # marketing origin (for /v1/leads + /v1/track)
ADMIN_CORS_ORIGINS=                                 # empty — admin UI is same-origin at /admin/
ADMIN_IP_ALLOWLIST=<office/VPN egress IPs, comma-separated>
TRAFFIC_TRACKING_ENABLED=true
TRAFFIC_RETENTION_DAYS=90
USAGE_ROLLUP_INTERVAL=300
```

Generate secrets with `python -c "import secrets;print(secrets.token_urlsafe(48))"`.
The full annotated list lives in `backend/.env.example`; `backend/docker-compose.yml`
passes every var through.

## Per-site wiring

- **Marketing (site 1):** static deploy of `foxy-sale-page/`. Set the backend origin
  before the page's inline script: `<script>window.FOXY_API='https://app.foxyaudit.com'</script>`
  — and keep `https://foxyaudit.com` in `CORS_ORIGINS` or the lead form + pageview
  beacon preflights will fail.
- **Dashboard (site 2):** nothing to deploy separately — the backend serves the HTML
  (override the file location with `FOXY_DASHBOARD_HTML` if the container layout
  differs; compose already bind-mounts it read-only).
- **Admin (site 3):** served at `GET /admin/` (`FOXY_ADMIN_HTML` override available).
  Bootstrap the first superadmin ONCE:
  `python scripts/seed_staff.py --email you@foxy.audit --password '<strong>'`
  (it refuses to run if staff already exist).

## Reverse proxy requirements

- Terminate TLS for all three hostnames; prod cookies are `Secure`-only so plain HTTP
  logins will silently fail.
- The admin IP allow-list trusts the FIRST `X-Forwarded-For` hop. The proxy MUST
  overwrite (not append to) any client-supplied `X-Forwarded-For`, or the guard can be
  spoofed. It is defense-in-depth on top of `require_staff`, never a replacement.

## Don't forget the worker

Run `python -m app.worker_main` (compose service `foxy-worker`) alongside the API. It
owns grading, anchoring, `usage_daily` rollups, AND `traffic_events` partition
create/drop — if it stops for long, inserts fall into the DEFAULT partition (never
lost, but monthly partitions for months with rows already in DEFAULT can no longer be
created, so keep it running).

## Post-deploy smoke checklist

1. `https://app.foxyaudit.com/health/ready` → 200.
2. `https://app.foxyaudit.com/dashboard` → login works; Billing page loads usage + invoices.
3. `https://admin.foxyaudit.com/admin/` → staff login works from an allow-listed IP; 403 from elsewhere.
4. A customer session cookie on any `/admin/v1/*` route → 401 (channel separation).
5. Marketing page → submit the contact form → row lands in `marketing_leads`; pageview lands in `traffic_events` with `site='marketing'`.
