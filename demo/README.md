# Foxy Audit Demo

There are two honest test paths.

## Offline verifier demo

Use this when you have no database, API key, or LLM. It uses clearly labeled
synthetic events but real customer-keyed commitments, chain construction,
anchor receipt checking, and tamper detection.

```powershell
python offline_demo.py
```

## Real SDK and backend demo

This path requires PostgreSQL, the backend, an organization API key, and the
SDK. Follow [../backend/README.md](../backend/README.md) to start the backend,
then run:

```powershell
pip install -e ..\sdk
$env:FOXY_API_KEY = "foxy_sk_your_key"
$env:FOXY_BACKEND_URL = "http://127.0.0.1:8000"
python run_demo.py
```

The decorated functions in `run_demo.py` are local stand-ins for a customer's
existing model call. They prove the SDK-to-backend behavior without claiming
that a model was contacted. Replace the function body with the customer's
actual provider call for a client integration test.

With no provider key, capture, chain verification, and deterministic metadata
rules still work. To exercise a real optional judge, set one or both keys in
the backend environment and restart the worker:

```text
GEMINI_API_KEY=...
OPENAI_API_KEY=...
OPENAI_MODEL=gpt-5.6
```

The judges receive hashes and bounded metadata only. They never receive the
raw prompt or response. The real opt-in integration test is documented in
`backend/tests/integration/test_optional_integrations.py`.

## What to show a judge

1. Run `offline_demo.py` and show all four PASS checks.
2. Run `run_demo.py` against the local backend.
3. Open the dashboard ledger and verify the chain.
4. Mutate a test row and rerun verification to show the first broken sequence.
5. Explain that the offline sample is synthetic, while the SDK/backend path is
   the actual product path.
