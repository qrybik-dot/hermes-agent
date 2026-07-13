from types import SimpleNamespace

from gateway.run import _should_attach_tool_progress_callback, _should_use_live_task_status


def test_progress_callback_attached_for_visible_progress():
    assert _should_attach_tool_progress_callback(True, None) is True


def test_progress_callback_attached_for_required_execution():
    task = SimpleNamespace(requires_execution=True)
    assert _should_attach_tool_progress_callback(False, task) is True


def test_progress_callback_not_attached_for_plain_text_turn():
    task = SimpleNamespace(requires_execution=False)
    assert _should_attach_tool_progress_callback(False, task) is False


def _skill_payload(instruction: str = "") -> str:
    value = (
        '[IMPORTANT: The user has invoked the "triz" skill, indicating they want '
        'you to follow its instructions. The full skill content is loaded below.]\n\n'
        '# Procedure\nCheck, create, save and verify results.'
    )
    if instruction:
        value += (
            '\n\nThe user has provided the following instruction alongside the skill invocation: '
            + instruction
        )
    return value


def test_bare_skill_does_not_emit_internal_live_status():
    assert _should_use_live_task_status("expert_analysis", None, _skill_payload()) is False


def test_skill_with_real_instruction_keeps_live_status():
    assert _should_use_live_task_status(
        "expert_analysis", None, _skill_payload("Настрой gateway на VPS")
    ) is True


def test_semantic_expert_analysis_keeps_live_status():
    assert _should_use_live_task_status(
        "expert_analysis", None, "Разбери системное противоречие"
    ) is True


def test_legacy_active_execution_task_keeps_live_status_for_bare_skill():
    task = SimpleNamespace(requires_execution=True)
    assert _should_use_live_task_status("expert_analysis", task, _skill_payload()) is True
