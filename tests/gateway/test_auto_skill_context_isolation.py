from types import SimpleNamespace

import agent.skill_commands as skill_commands
from gateway.run import _inject_auto_skill_context


def test_auto_skill_stays_out_of_canonical_user_text(monkeypatch):
    raw_text = "Запиши напоминание - разобраться с штрафами, сделать заявления"
    event = SimpleNamespace(text=raw_text, auto_skill="memory-profile")

    monkeypatch.setattr(
        skill_commands,
        "_load_skill_payload",
        lambda name, task_id=None: ("SKILL BODY", "/tmp/memory-profile", "memory-profile"),
    )
    monkeypatch.setattr(
        skill_commands,
        "_build_skill_message",
        lambda body, skill_dir, note: f"{note}\n{body}\nDIR={skill_dir}",
    )

    context, loaded = _inject_auto_skill_context(event, "BASE CONTEXT", "task-1")

    assert event.text == raw_text
    assert raw_text not in context
    assert "BASE CONTEXT" in context
    assert "SKILL BODY" in context
    assert loaded == ["memory-profile"]


def test_auto_skill_context_is_reinjected_for_each_bound_turn(monkeypatch):
    calls = []
    event = SimpleNamespace(text="обычный запрос", auto_skill=["one", "two"])

    def fake_load(name, task_id=None):
        calls.append((name, task_id))
        return (f"BODY {name}", f"/tmp/{name}", name)

    monkeypatch.setattr(skill_commands, "_load_skill_payload", fake_load)
    monkeypatch.setattr(
        skill_commands,
        "_build_skill_message",
        lambda body, skill_dir, note: f"{note}\n{body}",
    )

    context, loaded = _inject_auto_skill_context(event, "", "task-2")

    assert calls == [("one", "task-2"), ("two", "task-2")]
    assert loaded == ["one", "two"]
    assert "BODY one" in context and "BODY two" in context
    assert event.text == "обычный запрос"


def test_no_auto_skill_leaves_context_and_text_unchanged():
    event = SimpleNamespace(text="обычный запрос", auto_skill=None)

    context, loaded = _inject_auto_skill_context(event, "BASE", "task-3")

    assert context == "BASE"
    assert loaded == []
    assert event.text == "обычный запрос"
