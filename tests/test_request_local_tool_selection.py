from __future__ import annotations

import hermes_cli.plugins as plugins


def _tool(name: str) -> dict:
    return {"type": "function", "function": {"name": name, "description": name, "parameters": {"type": "object"}}}


class _Manager:
    def __init__(self, results, enabled: bool = True):
        self.results = results
        self.enabled = enabled
        self.payload = None

    def has_hook(self, name: str) -> bool:
        return self.enabled and name == "select_tools_for_request"

    def invoke_hook(self, name: str, **kwargs):
        assert name == "select_tools_for_request"
        self.payload = kwargs
        return self.results


def _names(tool_defs):
    return [t["function"]["name"] for t in tool_defs]


def test_no_selector_is_byte_behavior_noop(monkeypatch):
    tools = [_tool("web_search"), _tool("terminal")]
    manager = _Manager([], enabled=False)
    monkeypatch.setattr(plugins, "_delivery_manager", lambda: manager)
    assert plugins.select_tools_for_request(tools) is tools


def test_selector_can_only_narrow_and_keeps_escape_hatch(monkeypatch):
    tools = [_tool("web_search"), _tool("terminal"), _tool("tool_search"), _tool("tool_describe"), _tool("tool_call")]
    manager = _Manager([{"tool_names": ["web_search"]}])
    monkeypatch.setattr(plugins, "_delivery_manager", lambda: manager)
    selected = plugins.select_tools_for_request(tools, session_id="s", turn_id="t", user_message="research this")
    assert _names(selected) == ["web_search", "tool_search", "tool_describe", "tool_call"]
    assert tuple(manager.payload["available_tool_names"]) == tuple(_names(tools))
    assert _names(tools) == ["web_search", "terminal", "tool_search", "tool_describe", "tool_call"]


def test_expansion_attempt_fails_open(monkeypatch):
    tools = [_tool("web_search"), _tool("tool_search")]
    manager = _Manager([{"tool_names": ["web_search", "write_file"]}])
    monkeypatch.setattr(plugins, "_delivery_manager", lambda: manager)
    assert plugins.select_tools_for_request(tools) is tools


def test_invalid_or_empty_selection_fails_open(monkeypatch):
    tools = [_tool("web_search"), _tool("terminal")]
    manager = _Manager([{"tool_names": "web_search"}, {"tool_names": []}])
    monkeypatch.setattr(plugins, "_delivery_manager", lambda: manager)
    assert plugins.select_tools_for_request(tools) is tools


def test_multiple_selectors_intersect(monkeypatch):
    tools = [_tool("web_search"), _tool("terminal"), _tool("read_file"), _tool("tool_search")]
    manager = _Manager([
        {"tool_names": ["web_search", "terminal", "read_file"]},
        {"tool_names": ["web_search", "read_file"]},
    ])
    monkeypatch.setattr(plugins, "_delivery_manager", lambda: manager)
    assert _names(plugins.select_tools_for_request(tools)) == ["web_search", "read_file", "tool_search"]


def test_selector_exception_fails_open(monkeypatch):
    tools = [_tool("web_search"), _tool("terminal")]
    class BrokenManager:
        def has_hook(self, name): return True
        def invoke_hook(self, name, **kwargs): raise RuntimeError("boom")
    monkeypatch.setattr(plugins, "_delivery_manager", lambda: BrokenManager())
    assert plugins.select_tools_for_request(tools) is tools
