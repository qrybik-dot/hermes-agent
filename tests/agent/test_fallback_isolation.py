from agent.fallback_isolation import isolate_request_if_needed


class DummyAgent:
    def __init__(self):
        self._fallback_isolate_context_pending = True
        self._fallback_context_isolated = False
        self._fallback_isolated_allowed_tools = {"web_search"}
        self.tools = [
            {"function": {"name": "web_search"}},
            {"function": {"name": "terminal"}},
        ]
        self.valid_tool_names = {"web_search", "terminal"}


def test_isolation_keeps_only_current_turn_and_allowed_tools():
    agent = DummyAgent()
    messages = [
        {"role": "system", "content": "old system"},
        {"role": "user", "content": "OLD_MARKER"},
        {"role": "assistant", "content": "old answer"},
        {"role": "user", "content": "current request plus injected context"},
    ]
    agent._fallback_current_user_text = "current request"
    result, chars, rough, request_rough = isolate_request_if_needed(agent, messages)
    joined = str(result)
    assert "OLD_MARKER" not in joined
    assert "injected context" not in joined
    assert "current request" in joined
    assert agent.valid_tool_names == {"web_search"}
    assert agent._fallback_context_isolated is True
    assert chars > 0 and rough > 0 and request_rough > 0


def test_isolation_remains_active_on_later_calls():
    agent = DummyAgent()
    agent._fallback_current_user_text = "current request"
    first, *_ = isolate_request_if_needed(
        agent,
        [{"role": "user", "content": "current request"}],
    )
    second, *_ = isolate_request_if_needed(
        agent,
        [
            {"role": "system", "content": "old system"},
            {"role": "user", "content": "current request"},
            {"role": "assistant", "content": "tool call"},
            {"role": "tool", "content": "public result"},
        ],
    )
    assert len(first) == 2
    assert "old system" not in str(second)
    assert "public result" in str(second)
