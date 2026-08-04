from pathlib import Path

from agent.prompt_builder import TASK_COMPLETION_GUIDANCE


def test_system_guidance_forbids_unverified_tool_narration():
    text = TASK_COMPLETION_GUIDANCE.lower()
    assert "never present a command as executed" in text
    assert "current turn" in text
    assert "explicitly label the claim unverified" in text


def test_empty_execution_final_cannot_be_delivered_as_ready():
    source = Path("gateway/run.py").read_text(encoding="utf-8")
    assert "empty_final_response" in source
    assert 'result["partial"] = True' in source
    assert 'result["completed"] = False' in source
    assert 'status="incomplete"' in source
    assert 'last_error="empty_final_response"' in source
    assert "(empty)" in source
