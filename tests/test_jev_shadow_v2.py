from __future__ import annotations

import importlib.util
import json
from pathlib import Path


def _load_plugin():
    path = Path(__file__).resolve().parents[1] / "plugins" / "jev-shadow" / "__init__.py"
    spec = importlib.util.spec_from_file_location("test_jev_shadow_v2_plugin", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def test_question_set_is_independent_signals():
    m = _load_plugin()
    questions = m._question_set()
    assert "tool_family" not in questions
    expected = {
        "needs_web", "needs_files", "needs_knowledge", "needs_server_ops",
        "needs_code_execution", "needs_browser", "needs_communication",
        "needs_generation", "needs_deep_reasoning", "deterministic_complete",
        "likely_external_side_effect",
    }
    assert expected == set(questions)
    assert all(q["type"] == "noul" for q in questions.values())


def test_external_state_redacts_common_pii_and_secrets():
    m = _load_plugin()
    text = "email a.user@example.com tel +7 999 123-45-67 @anton url https://example.com/x token=abc123secret"
    redacted = m._redact(text)
    assert "a.user@example.com" not in redacted
    assert "+7 999 123-45-67" not in redacted
    assert "@anton" not in redacted
    assert "https://example.com/x" not in redacted
    assert "abc123secret" not in redacted
    assert "[EMAIL]" in redacted and "[PHONE]" in redacted and "[URL]" in redacted


def test_outcome_collects_objective_turn_evidence(monkeypatch, tmp_path):
    m = _load_plugin()
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    key, state = m._ensure_turn(
        turn_id="turn-1", session_id="session-1", task_id="task-1", platform="telegram",
        model="model-a", request_hash="reqhash", request_chars=10,
    )
    assert key == "turn-1"
    m.on_post_tool_call(tool_name="tool_search", turn_id="turn-1", session_id="session-1", status="ok", duration_ms=5)
    m.on_post_tool_call(tool_name="web_search", turn_id="turn-1", session_id="session-1", status="error", duration_ms=7)
    m.on_post_api_request(turn_id="turn-1", session_id="session-1", api_call_count=2,
                          api_duration=0.25, usage={"input_tokens": 100, "output_tokens": 20},
                          model="model-a", provider="proxy")
    m.on_session_end(turn_id="turn-1", session_id="session-1", task_id="task-1",
                     completed=True, failed=False, interrupted=False,
                     turn_exit_reason="text_response(stop)", model="model-a", platform="telegram")
    rows = [json.loads(line) for line in (tmp_path / "logs" / "jev-shadow.jsonl").read_text().splitlines()]
    assert len(rows) == 1
    row = rows[0]
    assert row["event"] == "outcome" and row["schema"] == 2
    assert row["actual_tools"] == ["tool_search", "web_search"]
    assert row["actual_tool_families"] == ["DISCOVERY", "WEB"]
    assert row["discovery_calls"] == 1 and row["tool_errors"] == 1
    assert row["api_calls"] == 2 and row["input_tokens"] == 100 and row["output_tokens"] == 20
    assert "result" not in row and "args" not in row


def test_plugin_registers_observers_but_not_active_selector():
    m = _load_plugin()
    hooks = []
    class Ctx:
        def register_hook(self, name, callback): hooks.append(name)
        def register_command(self, *args, **kwargs): pass
    m.register(Ctx())
    assert hooks == ["pre_llm_call", "post_tool_call", "post_api_request", "on_session_end"]
    assert "select_tools_for_request" not in hooks


def test_jev_exception_is_logged_and_never_raised(monkeypatch, tmp_path):
    m = _load_plugin()
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("TYPESAFE_API_KEY", "dummy")
    class BrokenClient:
        def post(self, *args, **kwargs): raise TimeoutError("simulated timeout")
    monkeypatch.setattr(m, "_client", lambda: BrokenClient())
    m._evaluate_shadow(
        user_message="research current docs", session_id="s", task_id="t", turn_id="turn-fault",
        platform="telegram", model="model-a", provider="proxy", is_first_turn=True,
    )
    rows = [json.loads(line) for line in (tmp_path / "logs" / "jev-shadow.jsonl").read_text().splitlines()]
    assert rows[-1]["event"] == "decision"
    assert rows[-1]["status"] == "exception"
    assert rows[-1]["error_type"] == "TimeoutError"
