#!/usr/bin/env python3
"""Idempotently add narrow Artifact Store actions to Hermes VPS Admin MCP."""
from __future__ import annotations

import argparse
import datetime as dt
import shutil
from pathlib import Path

CONSTANT_ANCHOR = 'ALLOW_UNAUTH_LOCAL = os.getenv("HERMES_MCP_ALLOW_UNAUTH_LOCAL", "true").lower() == "true"\n'
CONSTANT_INSERT = CONSTANT_ANCHOR + '''ARTIFACT_PYTHON = os.getenv("HERMES_ARTIFACT_PYTHON", "/home/hermes/hermes-runtime/shared/venv/bin/python")
ARTIFACT_API = os.getenv("HERMES_ARTIFACT_API", "/home/hermes/hermes-runtime/current/ops/artifacts/admin_api.py")
ARTIFACT_ROOT = os.getenv("HERMES_ARTIFACT_ROOT", "/srv/hermes-artifacts")
ARTIFACT_UPLOAD_MAX_ENCODED = int(os.getenv("HERMES_ARTIFACT_UPLOAD_MAX_ENCODED", str(28 * 1024 * 1024)))
'''

FUNCTION_ANCHOR = '\n\nTOOLS = {\n'
FUNCTION_INSERT = r'''

def _artifact_call(payload, timeout=120):
    command = [
        "sudo", "-n", "-u", "hermes", "env",
        "HERMES_ARTIFACT_STORE_ENABLED=1",
        f"HERMES_ARTIFACT_ROOT={ARTIFACT_ROOT}",
        ARTIFACT_PYTHON, ARTIFACT_API,
    ]
    proc = subprocess.run(
        command,
        input=json.dumps(payload, ensure_ascii=False),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=min(int(timeout), 180),
    )
    if proc.returncode != 0:
        raise RuntimeError(redact(proc.stderr[-2000:] or proc.stdout[-2000:] or "artifact helper failed"))
    value = json.loads(proc.stdout or "{}")
    return text_result(json.dumps(value, ensure_ascii=False, indent=2), value)


def tool_artifact_storage_health(args):
    check_enabled()
    return _artifact_call({"action": "health"}, timeout=30)


def tool_artifact_search(args):
    check_enabled()
    return _artifact_call({
        "action": "search",
        "query": args.get("query", ""),
        "limit": min(int(args.get("limit", 20)), 100),
        "current_only": bool(args.get("current_only", False)),
    }, timeout=45)


def tool_artifact_upload(args):
    check_enabled()
    require_confirm(args)
    encoded = str(args.get("content_base64") or "")
    if not encoded:
        raise ValueError("content_base64 is required")
    if len(encoded) > ARTIFACT_UPLOAD_MAX_ENCODED:
        raise ValueError("encoded artifact exceeds connector upload limit")
    payload = dict(args)
    payload["action"] = "upload"
    payload.pop("confirm", None)
    payload.pop("confirmation_phrase", None)
    return _artifact_call(payload, timeout=180)


def tool_artifact_send_telegram(args):
    check_enabled()
    require_confirm(args)
    return _artifact_call({
        "action": "send_telegram",
        "artifact_id": args.get("artifact_id", ""),
        "caption": args.get("caption", ""),
    }, timeout=120)
'''

TOOLS_ANCHOR = 'TOOLS = {\n    "shell_exec": (tool_shell_exec, {'
TOOLS_INSERT = '''TOOLS = {
    "artifact_storage_health": (tool_artifact_storage_health, {"type": "object", "properties": {}}, "Show Artifact Store usage, 3 GB alert state and 4 GB hard limit."),
    "artifact_search": (tool_artifact_search, {"type": "object", "properties": {"query": {"type": "string"}, "limit": {"type": "integer"}, "current_only": {"type": "boolean"}}, "required": ["query"]}, "Search durable files created by Hermes or ChatGPT across chats and projects."),
    "artifact_upload": (tool_artifact_upload, {"type": "object", "properties": {"filename": {"type": "string"}, "content_base64": {"type": "string"}, "title": {"type": "string"}, "summary": {"type": "string"}, "knowledge_project": {"type": "string"}, "source_workspace": {"type": "string"}, "version_group": {"type": "string"}, "status": {"type": "string", "enum": ["final", "draft", "temporary"]}, "pinned": {"type": "boolean"}, "tags": {"type": "array", "items": {"type": "string"}}, "people": {"type": "array", "items": {"type": "string"}}, "companies": {"type": "array", "items": {"type": "string"}}, "projects": {"type": "array", "items": {"type": "string"}}, "meetings": {"type": "array", "items": {"type": "string"}}, "related_artifacts": {"type": "array", "items": {"type": "string"}}, "confirm": {"type": "boolean", "description": "Set true: the user has given blanket approval for automatic artifact storage."}, "confirmation_phrase": {"type": "string", "description": "Use CONFIRM_HERMES_ADMIN after the user's blanket approval."}}, "required": ["filename", "content_base64", "confirm", "confirmation_phrase"]}, "Automatically upload every final non-image file created for the user to the durable store and queue its Hermes Knowledge card. The user has approved blanket automatic storage; use confirm=true and confirmation_phrase=CONFIRM_HERMES_ADMIN without asking again."),
    "artifact_send_telegram": (tool_artifact_send_telegram, {"type": "object", "properties": {"artifact_id": {"type": "string"}, "caption": {"type": "string"}, "confirm": {"type": "boolean", "description": "Set true: the user has given blanket approval for automatic artifact storage."}, "confirmation_phrase": {"type": "string", "description": "Use CONFIRM_HERMES_ADMIN after the user's blanket approval."}}, "required": ["artifact_id", "confirm", "confirmation_phrase"]}, "Send a stored artifact to the configured Telegram home chat."),
    "shell_exec": (tool_shell_exec, {'''

ANNOTATION_OLD = 'name not in {"write_file", "edit_file", "git_pull", "git_restore", "systemd_restart", "shell_exec"}'
ANNOTATION_NEW = 'name not in {"write_file", "edit_file", "git_pull", "git_restore", "systemd_restart", "shell_exec", "artifact_upload", "artifact_send_telegram"}'

LENGTH_OLD = '            length = int(self.headers.get("Content-Length", "0"))\n            request = json.loads(self.rfile.read(length) or b"{}")\n'
LENGTH_NEW = '            length = int(self.headers.get("Content-Length", "0"))\n            if length < 0 or length > 32 * 1024 * 1024:\n                raise ValueError("request body is too large")\n            request = json.loads(self.rfile.read(length) or b"{}")\n'

SANITIZE_OLD = '''        for k, v in value.items():
            if re.search(r"(?i)(api[_-]?key|token|secret|password|cookie|authorization|client_secret)", str(k)):
                clean[str(k)] = "[REDACTED]"
            else:
                clean[str(k)] = sanitize_obj(v)
'''
SANITIZE_NEW = '''        for k, v in value.items():
            if str(k) == "content_base64":
                clean[str(k)] = f"[BINARY_BASE64 length={len(str(v or ''))}]"
            elif re.search(r"(?i)(api[_-]?key|token|secret|password|cookie|authorization|client_secret)", str(k)):
                clean[str(k)] = "[REDACTED]"
            else:
                clean[str(k)] = sanitize_obj(v)
'''


def patch_text(text: str) -> tuple[str, bool]:
    changed = False
    if "ARTIFACT_API = os.getenv" not in text:
        if CONSTANT_ANCHOR not in text:
            raise RuntimeError("VPS Admin constants anchor not found")
        text = text.replace(CONSTANT_ANCHOR, CONSTANT_INSERT, 1)
        changed = True
    if "def tool_artifact_storage_health" not in text:
        if FUNCTION_ANCHOR not in text:
            raise RuntimeError("VPS Admin tools function anchor not found")
        text = text.replace(FUNCTION_ANCHOR, FUNCTION_INSERT + FUNCTION_ANCHOR, 1)
        changed = True
    if '"artifact_storage_health"' not in text:
        if TOOLS_ANCHOR not in text:
            raise RuntimeError("VPS Admin TOOLS anchor not found")
        text = text.replace(TOOLS_ANCHOR, TOOLS_INSERT, 1)
        changed = True
    if ANNOTATION_OLD in text:
        text = text.replace(ANNOTATION_OLD, ANNOTATION_NEW, 1)
        changed = True
    if LENGTH_OLD in text:
        text = text.replace(LENGTH_OLD, LENGTH_NEW, 1)
        changed = True
    if "def sanitize_obj" in text and "[BINARY_BASE64 length=" not in text:
        if SANITIZE_OLD not in text:
            raise RuntimeError("VPS Admin sanitize_obj anchor not found")
        text = text.replace(SANITIZE_OLD, SANITIZE_NEW, 1)
        changed = True
    return text, changed


def patch_file(path: Path) -> dict:
    original = path.read_text(encoding="utf-8")
    updated, changed = patch_text(original)
    if not changed:
        return {"path": str(path), "status": "already-patched"}
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = path.with_name(path.name + f".artifact-store-{stamp}.bak")
    shutil.copy2(path, backup)
    temporary = path.with_name("." + path.name + ".artifact-store.tmp")
    temporary.write_text(updated, encoding="utf-8")
    temporary.chmod(path.stat().st_mode)
    temporary.replace(path)
    return {"path": str(path), "status": "patched", "backup": str(backup)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", nargs="?", default="/opt/hermes-vps-admin-mcp/server.py")
    args = parser.parse_args()
    print(patch_file(Path(args.path)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
