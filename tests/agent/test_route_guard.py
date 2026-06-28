from agent.chat_completion_helpers import has_pending_fallback
from agent.error_classifier import FailoverReason


class DummyAgent:
    def __init__(self, tool_count):
        self._fallback_chain = [
            {
                "provider": "zai",
                "model": "glm-4.7-flash",
                "reasons": ["rate_limit"],
                "only_before_tools": True,
            }
        ]
        self._fallback_index = 0
        self._executed_tool_call_count = tool_count


def test_entry_available_before_tools():
    assert has_pending_fallback(DummyAgent(0), FailoverReason.rate_limit)


def test_entry_blocked_after_tool_call():
    assert not has_pending_fallback(DummyAgent(1), FailoverReason.rate_limit)


def test_reason_filter_remains_active():
    assert not has_pending_fallback(DummyAgent(0), FailoverReason.timeout)
