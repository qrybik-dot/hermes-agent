from __future__ import annotations

from typing import Any

from agent.model_metadata import (
    estimate_messages_tokens_rough,
    estimate_request_tokens_rough,
)


def isolate_request_if_needed(
    agent: Any,
    api_messages: list[dict],
) -> tuple[list[dict], int, int, int]:
    current_user_text = str(getattr(agent, "_fallback_current_user_text", "") or "")
    """Return a current-turn-only request for isolated fallback entries."""
    if getattr(agent, "_fallback_isolate_context_pending", False):
        allowed = set(
            getattr(agent, "_fallback_isolated_allowed_tools", set()) or set()
        )
        agent.tools = [
            tool for tool in (getattr(agent, "tools", None) or [])
            if (tool.get("function") or {}).get("name") in allowed
        ]
        agent.valid_tool_names = {
            (tool.get("function") or {}).get("name")
            for tool in agent.tools
            if (tool.get("function") or {}).get("name")
        }
        agent._fallback_isolate_context_pending = False
        agent._fallback_context_isolated = True

    if not getattr(agent, "_fallback_context_isolated", False):
        chars = sum(len(str(item)) for item in api_messages)
        rough = estimate_messages_tokens_rough(api_messages)
        request_rough = estimate_request_tokens_rough(
            api_messages, tools=agent.tools or None
        )
        return api_messages, chars, rough, request_rough

    user_index = next(
        (
            index
            for index in range(len(api_messages) - 1, -1, -1)
            if isinstance(api_messages[index], dict)
            and api_messages[index].get("role") == "user"
        ),
        None,
    )
    if user_index is None:
        tail = [{"role": "user", "content": current_user_text}]
    else:
        tail = [
            dict(item)
            for item in api_messages[user_index:]
            if isinstance(item, dict) and item.get("role") != "system"
        ]
        if not tail or tail[0].get("role") != "user":
            tail.insert(0, {"role": "user", "content": current_user_text})
        else:
            tail[0]["content"] = current_user_text

    system_text = (
        "Process only the current public request. Do not use unavailable context. "
        "If facts are missing, say so clearly. Respond in the user's language. "
        "Use only the explicitly available public tools."
    )
    result = [{"role": "system", "content": system_text}, *tail]
    chars = sum(len(str(item)) for item in result)
    rough = estimate_messages_tokens_rough(result)
    request_rough = estimate_request_tokens_rough(
        result, tools=agent.tools or None
    )
    return result, chars, rough, request_rough
