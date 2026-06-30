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
  POST /v1/webhooks/stripe   Stripe subscription webhook
"""

from __future__ import annotations

import subprocess
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from .config import get_settings
from .routers import billing, health, keys, logs, passport, policies, verify


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        subprocess.run(["alembic", "current"], check=True, capture_output=True)
    except subprocess.CalledProcessError as e:
        print(f"Warning: alembic current failed: {e.stderr}")
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

app.state.limiter = logs.limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

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
