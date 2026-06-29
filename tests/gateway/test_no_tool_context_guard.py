from copy import deepcopy

from gateway.run import _text_only_turn_history


def test_text_only_turn_history_keeps_only_clean_tail_after_tool_boundary():
    history = [
        {"role": "user", "content": "old request"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "1"}]},
        {"role": "tool", "content": "old result", "tool_call_id": "1"},
        {"role": "assistant", "content": "old final", "tool_calls": [{"id": "2"}]},
        {"role": "user", "content": "clean question"},
        {"role": "assistant", "content": "clean answer"},
        {"role": "user", "content": "failed retry one"},
        {"role": "user", "content": "failed retry two"},
    ]
    assert _text_only_turn_history(history) == [
        {"role": "user", "content": "clean question"},
        {"role": "assistant", "content": "clean answer"},
    ]


def test_text_only_turn_history_leaves_clean_dialogue_intact():
    history = [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi"},
    ]
    assert _text_only_turn_history(history) == history


def test_text_only_turn_history_does_not_mutate_saved_history():
    history = [
        {"role": "assistant", "content": "visible", "tool_calls": [{"id": "1"}]},
        {"role": "tool", "content": "result", "tool_call_id": "1"},
        {"role": "user", "content": "failed retry"},
    ]
    original = deepcopy(history)
    _text_only_turn_history(history)
    assert history == original
