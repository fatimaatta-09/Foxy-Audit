"""Foxy Audit backend — FastAPI ingestion service.

Endpoints:
  GET  /v1/health            liveness + key check (desktop app probes this)
  POST /v1/logs/batch        ingest metadata -> chain -> 202 -> background Gemini
  GET  /v1/logs              fetch paginated audit log rows
  GET  /v1/logs/{seq}        fetch single row by sequence number
  GET  /v1/verify            recompute the chain, detect tampering
  POST /v1/passport          generate a compliance passport (HTML)
  POST /v1/keys/rotate       rotate the org's API key
  GET  /v1/policies          fetch active policy config
  PUT  /v1/policies          update policy config
  POST /v1/auth/{login,logout,me,users}  human dashboard session auth (RBAC)
  GET  /dashboard            serve the web dashboard (same-origin login)
  POST /v1/webhooks/stripe   Stripe subscription webhook
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
from .routers import (
    auth_human, billing, health, keys, logs, passport, policies, verify,
)

# The web dashboard (foxy-audit-premium.html) is served same-origin at /dashboard
# so the human login session cookie works with no CORS/BFF.
_DASHBOARD_HTML = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "foxy-audit-premium.html")
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        subprocess.run(["alembic", "current"], check=True, capture_output=True)
    except Exception as e:
        # Non-fatal: alembic may not be on PATH (e.g. `python -m uvicorn`, or a
        # container without it). The check is advisory — never crash startup.
        print(f"Warning: alembic current check skipped/failed: {e}")
    yield


app = FastAPI(title="Foxy Audit", version="0.1.0", lifespan=lifespan)

# Secure CORS — origins are read from CORS_ORIGINS env var (comma-separated).
_settings = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=_settings.get_cors_origins(),
    allow_methods=["GET", "POST", "PUT", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)

# Human dashboard login rides a signed cookie session (separate channel from the
# SDK's Authorization: Bearer key). https_only=False for local http dev.
app.add_middleware(
    SessionMiddleware,
    secret_key=_settings.session_secret,
    https_only=False,
    same_site="lax",
)

app.state.limiter = logs.limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.include_router(auth_human.router, tags=["auth"])
app.include_router(health.router, tags=["health"])
app.include_router(logs.router, tags=["logs"])
app.include_router(verify.router, tags=["verify"])
app.include_router(passport.router, tags=["passport"])
app.include_router(keys.router, tags=["keys"])
app.include_router(billing.router, tags=["billing"])
app.include_router(policies.router, tags=["policies"])


@app.get("/")
def root():
    return {"service": "foxy-audit", "status": "ok"}


@app.get("/dashboard")
def dashboard():
    """Serve the premium web dashboard (same-origin with the API)."""
    return FileResponse(_DASHBOARD_HTML, media_type="text/html")
