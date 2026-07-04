from types import SimpleNamespace

from gateway.run import _should_attach_tool_progress_callback


def test_progress_callback_attached_for_visible_progress():
    assert _should_attach_tool_progress_callback(True, None) is True


def test_progress_callback_attached_for_required_execution():
    task = SimpleNamespace(requires_execution=True)
    assert _should_attach_tool_progress_callback(False, task) is True


def test_progress_callback_not_attached_for_plain_text_turn():
    task = SimpleNamespace(requires_execution=False)
    assert _should_attach_tool_progress_callback(False, task) is False
