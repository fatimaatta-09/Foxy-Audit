# Foxy Audit — End-to-End Demo

Drives the whole walking skeleton with one `@foxy.audit`-decorated function.

## Prereqs

1. Backend running (see [../backend/README.md](../backend/README.md)) on `http://127.0.0.1:8000`,
   with an org seeded — keep the printed `FOXY_API_KEY`.
2. Desktop fox running so you can watch it react:
   - `python ../desktop/omni_fox.py`
   - In its Settings: **Backend URL** = `http://127.0.0.1:8000`, **Org Key** = your `foxy_sk_...`.
   - Open the dashboard (right-click the fox → Dashboard) to watch the Blind Audit Log fill.

## Run

```bash
pip install -e ../sdk
export FOXY_API_KEY=foxy_sk_...
export FOXY_BACKEND_URL=http://127.0.0.1:8000
python run_demo.py
```

You'll see the fox show a **local hash queued** reaction for each captured call. A red reaction is
shown only for a real backend policy breach after asynchronous grading. The dashboard first shows
CAPTURED rows, then replaces them with the authoritative backend verdict when it refreshes.

## Verify tamper-evidence

```bash
curl -H "Authorization: Bearer $FOXY_API_KEY" $FOXY_BACKEND_URL/v1/verify   # ok:true
psql "postgresql://foxy:foxy@localhost:5432/foxy" -c \
  "UPDATE audit_logs SET token_count = token_count + 1 WHERE seq = 1;"
curl -H "Authorization: Bearer $FOXY_API_KEY" $FOXY_BACKEND_URL/v1/verify   # first_broken_seq:1
```

Even with no backend/key set, the SDK still emits a local-only capture signal, so `python run_demo.py`
is a valid smoke test of the SDK-to-fox path on its own; it does not create server evidence.
