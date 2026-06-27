from gateway.task_continuation import TaskStateStore


def test_explicit_task_id_can_reopen_completed_task(tmp_path):
    store = TaskStateStore(tmp_path / "state.db")
    task = store.create(
        task_id="b27e2d8b2026",
        platform="telegram",
        chat_id="1",
        session_key="s",
        title="Calendar task",
        original_request="Создай событие в календаре",
        role="simple",
        toolsets=["terminal", "skills"],
        required_toolsets=["terminal"],
        requires_execution=True,
        status="completed",
    )
    decision = store.resolve("Продолжить b27e2d8b2026", "telegram", "1")
    assert decision.kind == "selected"
    assert decision.task.task_id == task.task_id


def test_reply_continuation_artifact_is_not_active(tmp_path):
    store = TaskStateStore(tmp_path / "state.db")
    artifact = store.create(
        platform="telegram",
        chat_id="1",
        session_key="s",
        title="Reply artifact",
        original_request='[Replying to: "old report"]\nГотов продолжить',
        role="server_debug",
        toolsets=["terminal"],
        required_toolsets=["terminal"],
        requires_execution=True,
        status="incomplete",
    )
    store.update(
        artifact.task_id,
        last_error="execution task completed without tool calls",
    )
    assert store.active("telegram", "1") == []
    assert store.resolve("Готов продолжить", "telegram", "1").kind == "empty"
