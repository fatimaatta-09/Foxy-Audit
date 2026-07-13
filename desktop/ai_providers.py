"""
Provider-agnostic AI calling for the OmniAware Fox chat popup.

New module. Supports, all selectable in Settings:
  - anthropic  cloud, needs an API key
  - openai     cloud, needs an API key
  - ollama     local model server (https://ollama.com), no key needed
  - lmstudio   local model server, OpenAI-compatible, no key needed
  - custom     any OpenAI-compatible endpoint (self-hosted, etc.)

Every call_* function raises on failure. The caller (ChatPopup) is
expected to catch and fall back to a canned reply so a missing key or
an unreachable local server never breaks the UI.
"""
import requests

TIMEOUT = 30


def call_ai(history: list[dict], system_prompt: str, settings) -> str:
    """Dispatch to whichever provider is configured in `settings`
    (a FoxSettings instance)."""
    provider = settings.ai_provider()
    key = settings.api_key(provider)
    model = settings.model(provider)
    url = settings.base_url(provider)

    if provider == "anthropic":
        return _call_anthropic(history, system_prompt, key, model, url)
    if provider == "openai":
        return _call_openai_compatible(history, system_prompt, key, model, url)
    if provider == "ollama":
        return _call_ollama(history, system_prompt, model, url)
    if provider in ("lmstudio", "custom"):
        return _call_openai_compatible(history, system_prompt, key, model, url)
    raise ValueError(f"Unknown AI provider: {provider!r}")


def _call_anthropic(history, system_prompt, api_key, model, url) -> str:
    if not api_key:
        raise RuntimeError("No Anthropic API key set. Add one in Settings > AI Brain.")
    resp = requests.post(
        url,
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={"model": model, "max_tokens": 250, "system": system_prompt, "messages": history},
        timeout=TIMEOUT,
    )
    resp.raise_for_status()
    data = resp.json()
    parts = [b["text"] for b in data.get("content", []) if b.get("type") == "text"]
    return "".join(parts).strip() or "..."


def _call_openai_compatible(history, system_prompt, api_key, model, url) -> str:
    """Works for OpenAI itself, LM Studio's local server, and any other
    /v1/chat/completions-compatible endpoint."""
    if not url:
        raise RuntimeError("No endpoint URL configured for this provider.")
    headers = {"content-type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    messages = [{"role": "system", "content": system_prompt}] + history
    resp = requests.post(
        url,
        headers=headers,
        json={"model": model, "messages": messages, "max_tokens": 250, "temperature": 0.8},
        timeout=TIMEOUT,
    )
    resp.raise_for_status()
    data = resp.json()
    return (data["choices"][0]["message"]["content"] or "").strip() or "..."


def _call_ollama(history, system_prompt, model, url) -> str:
    if not url:
        raise RuntimeError("No Ollama URL configured.")
    messages = [{"role": "system", "content": system_prompt}] + history
    resp = requests.post(
        url,
        json={"model": model, "messages": messages, "stream": False},
        timeout=TIMEOUT,
    )
    resp.raise_for_status()
    data = resp.json()
    return (data.get("message", {}).get("content", "") or "").strip() or "..."
