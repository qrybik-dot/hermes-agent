import importlib.util
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[2] / "ops" / "artifacts" / "patch_vps_admin.py"
spec = importlib.util.spec_from_file_location("patch_vps_admin", MODULE_PATH)
PATCH = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(PATCH)


def test_patch_realistic_server_shape_is_idempotent():
    source = '''
import os
import json
import re
import subprocess
ALLOW_UNAUTH_LOCAL = os.getenv("HERMES_MCP_ALLOW_UNAUTH_LOCAL", "true").lower() == "true"

def sanitize_obj(value):
    if isinstance(value, dict):
        clean = {}
        for k, v in value.items():
            if re.search(r"(?i)(api[_-]?key|token|secret|password|cookie|authorization|client_secret)", str(k)):
                clean[str(k)] = "[REDACTED]"
            else:
                clean[str(k)] = sanitize_obj(v)
        return clean
    return value

def tool_snapshot_state(args):
    return args

TOOLS = {
    "shell_exec": (tool_shell_exec, {
        "type": "object",
    }, "desc"),
}

def tool_descriptors():
    return [{"annotations": {"readOnlyHint": name not in {"write_file", "edit_file", "git_pull", "git_restore", "systemd_restart", "shell_exec"}}} for name in TOOLS]

class Handler:
    def do_POST(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
            request = json.loads(self.rfile.read(length) or b"{}")
        except Exception:
            pass
'''
    updated, changed = PATCH.patch_text(source)
    assert changed is True
    assert "artifact_storage_health" in updated
    assert "artifact_upload" in updated
    assert "def _artifact_call" in updated
    assert "request body is too large" in updated
    assert "[BINARY_BASE64 length=" in updated

    second, changed_again = PATCH.patch_text(updated)
    assert changed_again is False
    assert second == updated
