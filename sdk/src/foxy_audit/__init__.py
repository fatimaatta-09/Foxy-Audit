"""
Foxy Audit SDK — Governance-as-Code for AI applications.

Wrap any LLM call with the ``@audit`` decorator and every invocation is
committed locally with a customer key, the raw text is discarded, and only metadata is
streamed to the Foxy Audit backend — plus a best-effort local UDP ping that
makes the desktop "fox" companion react in real time.

Quickstart
----------
    from foxy_audit import FoxyClient

    foxy = FoxyClient(api_key="foxy_sk_...")     # or set $FOXY_API_KEY

    @foxy.audit(policy="hipaa_basic")
    def ask_model(prompt: str) -> str:
        return llm_client.generate(prompt)       # your code, unchanged

The default decorator mode does not block the wrapped function and keeps
telemetry errors out of your application. Set ``audit_required=True`` when
the application must fail closed if evidence delivery cannot be confirmed.
"""

from .client import FoxyClient, FoxyPolicyBlocked
from .config import FoxyConfig

__version__ = "1.1.0"
__all__ = ["FoxyClient", "FoxyConfig", "FoxyPolicyBlocked", "audit", "__version__"]

# Module-level convenience: a lazily-created client configured from the
# environment (FOXY_API_KEY / FOXY_BACKEND_URL), so `from foxy_audit import audit`
# works without explicitly constructing a client.
_default_client = None


def _client() -> FoxyClient:
    global _default_client
    if _default_client is None:
        _default_client = FoxyClient()
    return _default_client


def audit(policy: str = "default", agent: str | None = None, mode: str | None = None):
    """Decorator bound to the default environment-configured client.

    ``mode`` selects the preflight behaviour ("observe"|"block"|"redact");
    when omitted it follows the client's configured mode (FOXY_MODE)."""
    return _client().audit(policy, agent=agent, mode=mode)
