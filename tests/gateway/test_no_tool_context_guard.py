from copy import deepcopy

from gateway.run import _text_only_turn_history


def test_text_only_turn_history_removes_protocol_messages():
    history = [
        {"role": "user", "content": "old request"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "1"}]},
        {"role": "tool", "content": "old result", "tool_call_id": "1"},
        {"role": "assistant", "content": "old final", "tool_calls": [{"id": "2"}]},
        {"role": "user", "content": "hello"},
    ]

    assert _text_only_turn_history(history) == [
        {"role": "user", "content": "old request"},
        {"role": "assistant", "content": "old final"},
        {"role": "user", "content": "hello"},
    ]


def test_text_only_turn_history_does_not_mutate_saved_history():
    history = [
        {"role": "assistant", "content": "visible", "tool_calls": [{"id": "1"}]},
        {"role": "tool", "content": "result", "tool_call_id": "1"},
    ]
    original = deepcopy(history)

    _text_only_turn_history(history)

    assert history == original
