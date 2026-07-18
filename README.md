# 🦊 Foxy Audit

> **Friendly on the surface. Tamper-evident under the hood.**
> *Mathematically verifiable AI compliance, powered by a lightweight SDK and a tamper-evident cryptographic ledger.*

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![FastAPI](https://img.shields.io/badge/Backend-FastAPI-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com)
[![Next.js](https://img.shields.io/badge/Frontend-Next.js-000000?logo=nextdotjs&logoColor=white)](https://nextjs.org)
[![Gemini](https://img.shields.io/badge/AI-Gemini_1.5_Pro-8E75B2?logo=google&logoColor=white)](https://deepmind.google/technologies/gemini/)

---

## 💡 The Compliance Nightmare

Regulated buyers increasingly require defensible audit evidence for production AI systems. Standard writable databases do not by themselves show whether a historical record was changed, and a one-time audit does not provide continuous evidence.

Existing enterprise governance tools can require substantial integration overhead. Developers need a low-friction capture path, and compliance officers need evidence they can independently verify.

## ⚡ The Foxy Solution

**Foxy Audit** (powered by the CipherTrail engine) bridges cryptographic tamper evidence and user-friendly AI governance.

We provide a lightweight Python SDK (`pip install foxy-audit`) that captures supported AI inputs/outputs locally, creates content-blind commitments, durably spools metadata, and streams it into an asynchronous, cryptographically chained backend. For the compliance officer, "Foxy" acts as an interactive desktop copilot that visualizes system health and generates independently verifiable evidence on demand.

### 🏗️ High-Level System Architecture

```text
[ Your Production App / AI Model ]
               │
               ▼  (Local keyed commitment via @foxy.audit)
         [ FastAPI API ]
               │
               ▼  (Immediate 202 Accepted Task Offloading: <1ms Latency)
            [ Redis ]
               │
               ▼  (Background Worker Consumption)
     [ Gemini 2.5 Flash ] ──► (Policy & Compliance Evaluation)
               │
               ▼  (Chained Hash Construction)
  [ PostgreSQL Hash-Chain Ledger ] ──► [ Foxy Audit UI Dashboard ]
```

## ✨ Key Features

- **Durable asynchronous capture** — The default SDK path writes metadata to a local SQLite/WAL spool and uploads in the background with retry. Measure application overhead in your own workload; no fixed latency or zero-lag guarantee is made.

- **Cryptographically Chained Integrity** — Every database row is linked to the row before it via a versioned sequential hash chain. New SDK events use customer-keyed HMAC commitments; changing captured metadata or deleting/reordering a row is detectable by verification. (This is a hash chain, not a Merkle tree or a blockchain.)

- **Evidence-bounded Policy Grading** — Deterministic local rules evaluate signals the system actually receives. An optional Gemini judge can add metadata-level analysis; unavailable evaluation is reported as unknown, not clean.

- **The "Foxy" UI** — A sleek, claymorphism-styled enterprise portal designed for compliance officers to visually verify cryptographic proofs, interact with their audit logs, and download verification PDFs with a single click.

## 🚀 Quickstart Guide

### 1. Install the SDK

```bash
# Once published to PyPI:
pip install foxy-audit

# Until then, install straight from source:
pip install "git+https://github.com/fatimaatta-09/Foxy-Audit.git#subdirectory=sdk"
```

### 2. Initialize and Decorate

Simply wrap your existing LLM invocation functions. The decorator automatically handles telemetry, local cryptographic hashing, and background ingestion streams.

```python
import os
from foxy_audit import FoxyClient

# Initialize the client
foxy = FoxyClient(api_key=os.getenv("FOXY_API_KEY"))

@foxy.audit(policy="hipaa_basic", agent="gpt-4o")
def call_medical_llm(user_prompt: str):
    # Your standard AI invocation code here
    response = openai.ChatCompletion.create(
        model="gpt-4o",
        messages=[{"role": "user", "content": user_prompt}]
    )
    return response
```

## 🛠️ Tech Stack & Infrastructure

| Layer | Technology |
|---|---|
| Developer SDK | Native Python Wrapper, SQLite/WAL spool, keyed commitments, `requests` |
| Backend Core | Python FastAPI, SQLAlchemy (PostgreSQL Object Mapping) |
| Asynchronous Queueing | SDK SQLite/WAL spool + PostgreSQL grading outbox |
| AI Evaluation Layer | Google Gemini 2.5 Flash (Asynchronous Compliance Parsing) |
| Auditor Interface | FastAPI-served admin/customer web surfaces + desktop companion |
| Deployment | Production-ready for Railway (Backend) and Vercel (Frontend) |

## 🔮 Future High-Performance Roadmap

- [ ] **Hardware-Root-of-Trust (FPGA Support)** — Offloading client-side SHA-256 hashing directly to local FPGA hardware layers to achieve zero software-overhead execution.
- [ ] **Optional zero-knowledge proofs** — Explore independently reviewed proof systems for narrowly defined statements; this is not part of the current evidence model.
- [ ] **Automated Incident Response Circuit Breakers** — Allowing the SDK to catch compliance failure states pushed from the backend to instantly isolate malicious user sessions.

---

Built to help teams collect and verify AI-governance evidence. It is not legal advice or a certification. Distributed under the MIT License.
