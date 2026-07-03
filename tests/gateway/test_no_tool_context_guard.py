from copy import deepcopy

import pytest

import gateway.run as gateway_run
from gateway.run import _build_simple_no_tools_history
from tests.gateway.test_fast_command import (
    _CapturingAgent,
    _install_fake_agent,
    _make_runner,
    _make_source,
)


def test_build_simple_no_tools_history_keeps_only_clean_tail_after_tool_boundary():
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
    assert _build_simple_no_tools_history(history) == [
        {"role": "user", "content": "clean question"},
        {"role": "assistant", "content": "clean answer"},
    ]


def test_build_simple_no_tools_history_leaves_clean_dialogue_intact():
    history = [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi"},
    ]
    assert _build_simple_no_tools_history(history) == history


def test_build_simple_no_tools_history_does_not_mutate_saved_history():
    history = [
        {"role": "assistant", "content": "visible", "tool_calls": [{"id": "1"}]},
        {"role": "tool", "content": "result", "tool_call_id": "1"},
        {"role": "user", "content": "failed retry"},
    ]
    original = deepcopy(history)
    _build_simple_no_tools_history(history)
    assert history == original


@pytest.mark.asyncio
async def test_simple_no_mcp_route_projects_stale_tool_history(monkeypatch, tmp_path):
    _install_fake_agent(monkeypatch)
    runner = _make_runner()

    import gateway.task_runtime as task_runtime
    from gateway.task_router import TaskRoute
    route = TaskRoute(
        role="simple",
        reason="short ordinary message",
        toolsets=["no_mcp"],
        max_iterations=12,
        skip_context_files=True,
    )
    monkeypatch.setattr(
        task_runtime,
        "prepare_task_turn",
        lambda **kwargs: task_runtime.PreparedTaskTurn(
            message=kwargs["message"],
            route=route,
            task=None,
            early_response=None,
            continued=False,
        ),
    )

    (tmp_path / "config.yaml").write_text("model:\n  default: test-model\n", encoding="utf-8")
    monkeypatch.setattr(gateway_run, "_hermes_home", tmp_path)
    monkeypatch.setattr(gateway_run, "_env_path", tmp_path / ".env")
    monkeypatch.setattr(gateway_run, "load_dotenv", lambda *args, **kwargs: None)
    monkeypatch.setattr(gateway_run, "_load_gateway_config", lambda: {})
    monkeypatch.setattr(gateway_run, "_load_gateway_runtime_config", lambda: {})
    monkeypatch.setattr(gateway_run, "_resolve_gateway_model", lambda config=None: "test-model")
    monkeypatch.setattr(
        gateway_run,
        "_resolve_runtime_agent_kwargs",
        lambda: {
            "provider": "custom",
            "api_mode": "chat_completions",
            "base_url": "http://127.0.0.1:8317/v1",
            "api_key": "test",
        },
    )

    import hermes_cli.tools_config as tools_config
    monkeypatch.setattr(
        tools_config,
        "_get_platform_tools",
        lambda user_config, platform_key: {"web", "terminal", "memory", "skills"},
    )

    history = [
        {"role": "user", "content": "old task"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "1"}]},
        {"role": "tool", "content": "old result", "tool_call_id": "1"},
        {"role": "user", "content": "clean question"},
        {"role": "assistant", "content": "clean answer"},
        {"role": "user", "content": "failed retry"},
    ]

    _CapturingAgent.last_run = None
    result = await runner._run_agent(
        message="привет",
        context_prompt="",
        history=history,
        source=_make_source(),
        session_id="session-1",
        session_key="agent:main:telegram:dm:12345",
    )

    assert result["final_response"] == "ok"
    assert _CapturingAgent.last_run["conversation_history"] == [
        {"role": "user", "content": "clean question"},
        {"role": "assistant", "content": "clean answer"},
    ]
