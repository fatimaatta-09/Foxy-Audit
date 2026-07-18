"""Vendor-neutral metadata extraction for common LLM response shapes.

This intentionally avoids importing OpenAI, Anthropic, Gemini, or LiteLLM. The
SDK hashes the complete local object, while this helper extracts only bounded
identifiers and usage counts for the evidence record.
"""

from __future__ import annotations

from typing import Any


def response_metadata(response: Any) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for name in ("id", "model", "provider"):
        value = getattr(response, name, None)
        if value is not None:
            result[name] = str(value)[:256]
    usage = getattr(response, "usage", None)
    if usage is not None:
        values = {}
        for name in ("prompt_tokens", "completion_tokens", "total_tokens", "input_tokens", "output_tokens"):
            value = getattr(usage, name, None)
            if value is None and isinstance(usage, dict):
                value = usage.get(name)
            if isinstance(value, int):
                values[name] = value
        if values:
            result["usage"] = values
    choices = getattr(response, "choices", None)
    if choices is not None and isinstance(choices, (list, tuple)):
        result["choice_count"] = len(choices)
        tool_names = []
        for choice in choices[:32]:
            message = getattr(choice, "message", None)
            calls = getattr(message, "tool_calls", None)
            if calls is None and isinstance(message, dict):
                calls = message.get("tool_calls")
            for call in calls or []:
                function = getattr(call, "function", None)
                name = getattr(function, "name", None)
                if name is None and isinstance(function, dict):
                    name = function.get("name")
                if name:
                    tool_names.append(str(name)[:128])
        if tool_names:
            result["tool_names"] = tool_names[:64]
    return result
