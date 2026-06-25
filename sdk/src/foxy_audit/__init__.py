"""
Foxy Audit SDK — Governance-as-Code for AI applications.

Wrap any LLM call with the ``@audit`` decorator and every invocation is
hashed locally (SHA-256), the raw text is discarded, and only metadata is
streamed to the Foxy Audit backend — plus a best-effort local UDP ping that
makes the desktop "fox" companion react in real time.

Quickstart
----------
    from foxy_audit import FoxyClient

    foxy = FoxyClient(api_key="foxy_sk_...")     # or set $FOXY_API_KEY

    @foxy.audit(policy="hipaa_basic")
    def ask_model(prompt: str) -> str:
        return llm_client.generate(prompt)       # your code, unchanged

The decorator never blocks the wrapped function and never raises its own
errors into your application — telemetry failure is swallowed silently.
"""

from .client import FoxyClient
from .config import FoxyConfig

__version__ = "0.1.0"
__all__ = ["FoxyClient", "FoxyConfig", "audit", "__version__"]

# Module-level convenience: a lazily-created client configured from the
# environment (FOXY_API_KEY / FOXY_BACKEND_URL), so `from foxy_audit import audit`
# works without explicitly constructing a client.
_default_client = None


def _client() -> FoxyClient:
    global _default_client
    if _default_client is None:
        _default_client = FoxyClient()
    return _default_client


def audit(policy: str = "default"):
    """Decorator bound to the default environment-configured client."""
    return _client().audit(policy)
