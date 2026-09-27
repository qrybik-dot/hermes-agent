import importlib.util
import json
import os
import unittest
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[1] / "plugins" / "jev-shadow" / "__init__.py"

def load_plugin():
    spec = importlib.util.spec_from_file_location("jev_shadow_v3_test", PLUGIN)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module

class JevShadowV3Tests(unittest.TestCase):
    def test_status_ok_wins_over_error_words(self):
        m = load_plugin()
        got = m._classify_tool_result("terminal", {"command": "inspect source"}, "Permission denied appears in documentation", "ok")
        self.assertEqual(got[0], "SUCCESS_OTHER")

    def test_privilege_boundary_grouping(self):
        m = load_plugin()
        a = m._classify_tool_result("terminal", {"command": "inspect protected"}, "Permission denied", "error")
        b = m._classify_tool_result("terminal", {"command": "inspect auth"}, "authentication required", "error")
        self.assertEqual(a[:2], ("PERMISSION_DENIED", "PRIVILEGE_BOUNDARY"))
        self.assertEqual(b[:2], ("AUTH_REQUIRED", "PRIVILEGE_BOUNDARY"))

    def test_raw_tool_data_not_retained(self):
        m = load_plugin()
        os.environ["HERMES_JEV_MIDTURN_SHADOW_ENABLED"] = "1"
        captured = []
        m._schedule_midturn_shadow = lambda key, snapshot, trigger: captured.append((key, snapshot, trigger))
        key, _ = m._ensure_turn(turn_id="t1", session_id="s1", task_id="k1", platform="telegram", model="gemini-test", request_hash="h", request_chars=4)
        with m._STATE_LOCK:
            m._TURNS[key]["request_redacted"] = "safe request"
        marker = "RAW_SHOULD_NOT_LEAK_123"
        m.on_post_tool_call(tool_name="terminal", args={"command": "inspect " + marker}, result="Permission denied " + marker, status="error", duration_ms=10, turn_id="t1", session_id="s1")
        m.on_post_tool_call(tool_name="terminal", args={"command": "inspect auth " + marker}, result="authentication required " + marker, status="error", duration_ms=10, turn_id="t1", session_id="s1")
        self.assertEqual(len(captured), 1)
        snapshot = captured[0][1]
        self.assertNotIn(marker, json.dumps(snapshot))
        self.assertEqual(snapshot["blocker_group_counts"]["PRIVILEGE_BOUNDARY"], 2)

    def test_boundary_slot_survives_generic_budget(self):
        m = load_plugin()
        os.environ["HERMES_JEV_MIDTURN_SHADOW_ENABLED"] = "1"
        state = {"tool_event_count": 12, "midturn_generic_calls": 2, "midturn_boundary_calls": 0, "last_midturn_tool_count": 12, "tool_errors": 3, "blocker_group_counts": {"PRIVILEGE_BOUNDARY": 2}}
        latest = {"result_class": "AUTH_REQUIRED", "blocker_group": "PRIVILEGE_BOUNDARY"}
        self.assertEqual(m._midturn_trigger(state, latest), "repeated_blocker_group")
        state["midturn_boundary_calls"] = 1
        self.assertIsNone(m._midturn_trigger(state, latest))

    def test_labels_are_advisory(self):
        m = load_plugin()
        handoff = {"same_blocker":{"noul":.95}, "new_hypothesis":{"noul":.1}, "retry_useful":{"noul":.15}, "handoff_required":{"noul":.96}}
        retry = {"same_blocker":{"noul":.2}, "new_hypothesis":{"noul":.85}, "retry_useful":{"noul":.9}, "handoff_required":{"noul":.05}}
        self.assertEqual(m._trajectory_label(handoff), "HANDOFF_CANDIDATE")
        self.assertEqual(m._trajectory_label(retry), "RETRY_CANDIDATE")

if __name__ == "__main__":
    unittest.main(verbosity=2)
