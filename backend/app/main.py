"""Foxy Audit backend — FastAPI ingestion service.

Endpoints:
  GET  /v1/health   liveness + key check (the desktop app probes this)
  POST /v1/logs     ingest metadata → chain → Gemini verdict → store
  GET  /v1/verify   recompute the chain, detect tampering
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .routers import health, logs, verify

app = FastAPI(title="Foxy Audit", version="0.1.0")

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


@app.get("/")
def root():
    return {"service": "foxy-audit", "status": "ok"}
