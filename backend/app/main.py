"""Foxy Audit backend — FastAPI ingestion service + admin platform.

Three surfaces, sharing ONE backend + ONE database, split at the ASGI layer so a
customer credential can NEVER satisfy a staff route and vice-versa:

  * customer_api  — the SDK ingest + customer dashboard (/v1/*, /dashboard). Its
                    own SessionMiddleware (cookie `session`) + customer CORS.
  * admin_api     — the internal admin site ("site 3"), mounted at /admin so its
                    paths are /admin/v1/*. Its OWN SessionMiddleware (cookie
                    `foxy_staff_session`, a DISTINCT secret), admin CORS, and an IP
                    allow-list guard. require_staff/require_platform_role gate it.
  * root `app`    — a bare parent that only mounts the two above. Kept middleware-
                    free so the two sub-apps' session cookies never nest/collide
                    (two SessionMiddleware on one app both write scope["session"]).

Endpoints (customer):
  GET  /v1/health · POST /v1/logs/batch · GET /v1/logs[/{seq}] · GET /v1/stats
  GET  /v1/verify · POST /v1/passport · POST /v1/keys/rotate · GET/POST/DELETE /v1/keys
  GET/PUT /v1/policies · GET /v1/analytics/threats · GET/POST /v1/anchors
  POST /v1/auth/* (dashboard session) · POST /v1/leads · POST /v1/track (public)
  POST /v1/webhooks/stripe · GET /dashboard
Endpoints (admin, /admin/v1/*):
  POST /admin/v1/auth/{login,logout} · GET /admin/v1/auth/me
  GET  /admin/v1/organizations[/{id}] · POST …/{id}/suspend|enable
  GET/POST /admin/v1/staff · POST …/{id}/disable · GET /admin/v1/stats · /admin/v1/traffic
"""

from __future__ import annotations

import os
import subprocess
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from starlette.middleware.sessions import SessionMiddleware

from .config import get_settings
from .middleware.admin_guard import AdminIPAllowlistMiddleware
from .middleware.security import BodySizeLimitMiddleware, SecurityHeadersMiddleware
from .middleware.traffic import TrafficMiddleware
from .observability import RequestIdMiddleware, configure_logging
from .routers import (
    account, admin_data, admin_inbox, admin_orgs, admin_staff, admin_stats, analytics, anchors,
    auth_google, auth_human, auth_staff, badge, billing, consent, health, keys, leads, logs,
    passport, policies, verify,
)

_settings = get_settings()


# The web dashboard (foxy-audit-premium.html) is served same-origin at /dashboard
# so the human login session cookie works with no CORS/BFF. The file lives in
# foxy-dashboard/ at the repo root (local dev); in a container it's mounted at
# /app (see docker-compose), so resolve across candidates (or FOXY_DASHBOARD_HTML).
def _find_dashboard_html() -> str:
    here = os.path.dirname(__file__)  # backend/app
    candidates = [os.environ.get("FOXY_DASHBOARD_HTML")] + [
        os.path.abspath(os.path.join(here, "..", "..", "foxy-dashboard",
                                     "foxy-audit-premium.html")),               # repo (dev)
        os.path.abspath(os.path.join(here, "..", "foxy-audit-premium.html")),   # /app (container)
    ]
    for c in candidates:
        if c and os.path.isfile(c):
            return c
    return candidates[-1]  # last resort — FileResponse will 404 clearly


_DASHBOARD_HTML = _find_dashboard_html()


# The ops console (foxy-adminpage/index.html) is served same-origin at /admin/ so
# the staff session cookie (Path=/admin) works with no CORS. Same candidate
# resolution as the dashboard (or FOXY_ADMIN_HTML).
def _find_admin_html() -> str:
    here = os.path.dirname(__file__)  # backend/app
    candidates = [os.environ.get("FOXY_ADMIN_HTML")] + [
        os.path.abspath(os.path.join(here, "..", "..", "foxy-adminpage",
                                     "index.html")),                             # repo (dev)
        os.path.abspath(os.path.join(here, "..", "foxy-adminpage.html")),        # /app (container)
    ]
    for c in candidates:
        if c and os.path.isfile(c):
            return c
    return candidates[-1]  # last resort — FileResponse will 404 clearly


_ADMIN_HTML = _find_admin_html()


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging(get_settings().is_prod)   # structured logs + request id (5E)
    try:
        subprocess.run(["alembic", "current"], check=True, capture_output=True)
    except Exception as e:
        # Non-fatal: alembic may not be on PATH (e.g. `python -m uvicorn`, or a
        # container without it). The check is advisory — never crash startup.
        print(f"Warning: alembic current check skipped/failed: {e}")
    yield


# ───────────────────────────── customer API (site 2) ─────────────────────────
customer_api = FastAPI(title="Foxy Audit", version="0.1.0")
customer_api.state.limiter = logs.limiter
customer_api.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

for _r in (auth_human, auth_google, health, logs, verify, passport, keys, billing, policies,
           analytics, anchors, leads, consent, account, badge):
    customer_api.include_router(_r.router, tags=[_r.__name__.rsplit(".", 1)[-1]])


@customer_api.get("/")
def customer_root():
    return {"service": "foxy-audit", "status": "ok"}


@customer_api.get("/dashboard")
def dashboard():
    """Serve the premium web dashboard (same-origin with the customer API)."""
    return FileResponse(_DASHBOARD_HTML, media_type="text/html")


# Middleware runs outermost-last: SessionMiddleware is added LAST so it wraps (runs
# before) TrafficMiddleware — the traffic row can then read the logged-in session.
customer_api.add_middleware(TrafficMiddleware, site="app")
customer_api.add_middleware(
    CORSMiddleware,
    allow_origins=_settings.get_cors_origins(),
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)
customer_api.add_middleware(
    SessionMiddleware,
    secret_key=_settings.session_secret,
    session_cookie="session",
    https_only=_settings.is_prod,          # was hardcoded False — now TLS-only in prod
    same_site="lax",
    max_age=_settings.session_max_age,
)
# Body-size cap + security headers (headers added last → outermost, on every response). (5B.6)
customer_api.add_middleware(BodySizeLimitMiddleware)
customer_api.add_middleware(SecurityHeadersMiddleware)
customer_api.add_middleware(RequestIdMiddleware)   # outermost: assign/echo X-Request-ID first


# ────────────────────────────── admin API (site 3) ───────────────────────────
admin_api = FastAPI(title="Foxy Audit Admin", version="0.1.0")
admin_api.state.limiter = auth_staff.limiter
admin_api.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

for _r in (auth_staff, admin_orgs, admin_staff, admin_stats, admin_data, admin_inbox):
    admin_api.include_router(_r.router, tags=[_r.__name__.rsplit(".", 1)[-1]])


@admin_api.get("/")
def admin_console():
    """Serve the ops console UI (same-origin with the admin API)."""
    return FileResponse(_ADMIN_HTML, media_type="text/html")

admin_api.add_middleware(TrafficMiddleware, site="admin")
admin_api.add_middleware(
    CORSMiddleware,
    allow_origins=_settings.get_admin_cors_origins(),   # SEPARATE from customer origins
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)
# DISTINCT cookie name + secret + strict SameSite + host-scoped Domain/Path so the
# staff cookie is never sent to the customer origin and a customer cookie can never
# satisfy require_staff.
admin_api.add_middleware(
    SessionMiddleware,
    secret_key=_settings.staff_session_secret,
    session_cookie="foxy_staff_session",
    https_only=_settings.is_prod,
    same_site="strict",
    max_age=_settings.staff_session_max_age,
    path="/admin",
    domain=(_settings.staff_cookie_domain or None),
)
# IP allow-list guard blocks before auth/routing (just inside the header/size layer).
admin_api.add_middleware(AdminIPAllowlistMiddleware)
admin_api.add_middleware(BodySizeLimitMiddleware)
admin_api.add_middleware(SecurityHeadersMiddleware)
admin_api.add_middleware(RequestIdMiddleware)   # outermost: assign/echo X-Request-ID first


# ───────────────────────────────── root app ──────────────────────────────────
# Bare parent: mounts only, no middleware (so the two session cookies never nest).
app = FastAPI(title="Foxy Audit (root)", version="0.1.0", lifespan=lifespan)
app.mount("/admin", admin_api)   # must precede the "/" mount so /admin/* wins
app.mount("/", customer_api)
