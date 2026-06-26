"""Foxy Audit backend — FastAPI ingestion service.

Endpoints:
  GET  /v1/health            liveness + key check (desktop app probes this)
  POST /v1/logs              ingest metadata → chain → 202 → background Gemini
  GET  /v1/verify            recompute the chain, detect tampering
  POST /v1/passport          generate a compliance passport (HTML)
  POST /v1/keys/rotate       rotate the org's API key
  POST /v1/webhooks/stripe   Stripe subscription webhook
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .routers import billing, health, keys, logs, passport, verify
from .worker import worker


@asynccontextmanager
async def lifespan(app: FastAPI):
    worker.start()
    yield
    worker.stop()


app = FastAPI(title="Foxy Audit", version="0.1.0", lifespan=lifespan)

# Permissive CORS — the local web/desktop clients live on other origins.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router, tags=["health"])
app.include_router(logs.router, tags=["logs"])
app.include_router(verify.router, tags=["verify"])
app.include_router(passport.router, tags=["passport"])
app.include_router(keys.router, tags=["keys"])
app.include_router(billing.router, tags=["billing"])


@app.get("/")
def root():
    return {"service": "foxy-audit", "status": "ok"}

