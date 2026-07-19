# Foxy Audit Demo

There are three honest test paths.

## Offline verifier demo

Use this when you have no database, API key, or LLM. It uses clearly labelled
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

## Live GPT-5.6 client demo

Use this path when a judge needs to see a real model call and the actual Foxy
pipeline. It calls the OpenAI Responses API with the chosen model, requires a
durable SDK receipt, waits for the backend worker's genuine metadata verdict,
and verifies the server-owned chain. It never manufactures a provider result.

Start the stack with the same provider key available to the backend and worker:

```powershell
# From the repository root, in Terminal 1.
cd backend
$env:OPENAI_API_KEY = "your_openai_key"
$env:OPENAI_MODEL = "gpt-5.6"
docker compose up --build -d
docker compose logs foxy-seed
```

Then, in a second terminal, use the API key printed by `foxy-seed`:

```powershell
# From the repository root, in Terminal 2.
pip install -e .\sdk
$env:OPENAI_API_KEY = "your_openai_key"
$env:OPENAI_MODEL = "gpt-5.6"
$env:FOXY_API_KEY = "foxy_sk_key_printed_by_the_seed_service"
$env:FOXY_BACKEND_URL = "http://127.0.0.1:8000"
python demo\live_openai_client.py
```

Use non-sensitive content for the demo prompt: the selected OpenAI model sees
it as the customer's provider, while Foxy receives only commitments and bounded
metadata. See [../docs/OPENAI_BUILD_WEEK_SUBMISSION.md](../docs/OPENAI_BUILD_WEEK_SUBMISSION.md)
for the complete judge runbook.

## What to show a judge

1. Run `offline_demo.py` and show all four PASS checks.
2. Run `live_openai_client.py` against the local backend and show its four
   actual checks: provider response, receipt, queued verdict, and verification.
3. Open the dashboard ledger and verify the chain.
4. Mutate a test row and rerun verification to show the first broken sequence.
5. Explain that the offline sample is synthetic, while the GPT-5.6 client path
   is a real provider and SDK/backend integration.
