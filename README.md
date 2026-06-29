# 🦊 Foxy Audit

> **Friendly on the surface. Unbreakable under the hood.**
> *Mathematically verifiable AI compliance, powered by a lightweight SDK and a tamper-evident cryptographic ledger.*

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![FastAPI](https://img.shields.io/badge/Backend-FastAPI-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com)
[![Next.js](https://img.shields.io/badge/Frontend-Next.js-000000?logo=nextdotjs&logoColor=white)](https://nextjs.org)
[![Gemini](https://img.shields.io/badge/AI-Gemini_1.5_Pro-8E75B2?logo=google&logoColor=white)](https://deepmind.google/technologies/gemini/)

---

## 💡 The Compliance Nightmare

The **EU AI Act**, **HIPAA**, and **PCI DSS v4.0** mandate strictly *tamper-evident logging* for production AI systems. Standard writable databases are no longer legally sufficient. If an insider or hacker gains root access, audit trails can be altered or deleted without leaving a trace.

Existing enterprise governance tools are heavy, boring, and require massive integration overhead. Developers need a drop-in utility that guarantees data integrity, and compliance officers need an interface they actually want to use.

## ⚡ The Foxy Solution

**Foxy Audit** (powered by the CipherTrail engine) bridges the gap between hardcore cryptographic immutability and user-friendly AI governance.

We provide a lightweight Python SDK (`pip install foxy-audit`) that intercepts AI inputs/outputs, performs ultra-low latency local hashing, and streams data into an asynchronous, cryptographically chained backend. For the compliance officer, "Foxy" acts as an interactive desktop copilot that visualizes system health and generates verifiable audit proofs on demand.

### 🏗️ High-Level System Architecture

```text
[ Your Production App / AI Model ]
               │
               ▼  (Local SHA-256 Hash via @foxy.audit)
         [ FastAPI API ]
               │
               ▼  (Immediate 202 Accepted Task Offloading: <1ms Latency)
            [ Redis ]
               │
               ▼  (Background Worker Consumption)
     [ Gemini 1.5 Pro ] ──► (Policy & Compliance Evaluation)
               │
               ▼  (Chained Hash Construction)
  [ PostgreSQL Merkle Tree Ledger ] ──► [ Foxy Audit UI Dashboard ]
```

## ✨ Key Features

- **<1ms Integration Latency** — The SDK computes SHA-256 hashes locally on the host machine and fires an asynchronous telemetry payload. Your core application experiences absolutely zero lag.

- **Cryptographically Chained Integrity** — Every database row is anchored to the block before it via a Merkle chain. If historical data is tampered with, the cryptographic signatures instantly break, exposing the breach.

- **Intelligent Policy Grading** — A background worker powered by Google Gemini 1.5 Pro evaluates telemetry data against strict regulatory frameworks in real-time.

- **Active Policy Configuration** — Non-technical executives can instantly toggle compliance rules (PII detection, prompt injection, regulated data mode) via the dashboard, instantly altering the strictness of the Gemini evaluator.

- **Verification Sandbox** — A built-in zero-knowledge proof tool allowing any third-party auditor to mathematically prove that the company's logs haven't been tampered with. It shifts the paradigm from "Trust Us" to "Trust the Math."

- **The "Foxy" UI** — A sleek, claymorphism-styled enterprise portal designed for compliance officers to visually verify cryptographic proofs, interact with their audit logs, and download verification PDFs with a single click.

## 🚀 Quickstart Guide

### 1. Install the SDK

```bash
pip install foxy-audit
```

### 2. Initialize and Decorate

Simply wrap your existing LLM invocation functions. The decorator automatically handles telemetry, local cryptographic hashing, and background ingestion streams.

```python
import os
from foxy_audit import FoxyClient

# Initialize the client
foxy = FoxyClient(api_key=os.getenv("FOXY_API_KEY"))

@foxy.audit(policy_group="healthcare-standard")
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
| Developer SDK | Native Python Wrapper, `hashlib`, `requests-async` |
| Backend Core | Python FastAPI, SQLAlchemy (PostgreSQL Object Mapping) |
| Asynchronous Queueing | Redis / BullMQ Task Management |
| AI Evaluation Layer | Google Gemini 1.5 Pro (Asynchronous Compliance Parsing) |
| Auditor Interface | Next.js, Tailwind CSS, Anime.js (Cryptographic UI animations) |
| Deployment | Production-ready for Railway (Backend) and Vercel (Frontend) |

## 🔮 Future High-Performance Roadmap

- [ ] **Hardware-Root-of-Trust (FPGA Support)** — Offloading client-side SHA-256 hashing directly to local FPGA hardware layers to achieve zero software-overhead execution.
- [ ] **Tier-1 ZKPs (snarkjs Integration)** — Generating pre-compiled Groth16 Zero-Knowledge Proof circuits to provide absolute compliance verifiability while keeping underlying prompt data completely private.
- [ ] **Automated Incident Response Circuit Breakers** — Allowing the SDK to catch compliance failure states pushed from the backend to instantly isolate malicious user sessions.

---

Built for the future of legally safe, autonomous AI. Distributed under the MIT License.
