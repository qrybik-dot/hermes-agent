#!/usr/bin/env python3
"""Fast read-only user-facing smoke matrix for Hermes production."""

from __future__ import annotations

import json
import subprocess
import sys
import urllib.request


def command(name: str, args: list[str], expected: str | None = None) -> dict:
    try:
        proc = subprocess.run(args, text=True, capture_output=True, timeout=40, cwd="/home/hermes")
        output = (proc.stdout + proc.stderr).strip()
        ok = proc.returncode == 0 and (expected is None or expected in output)
        return {"name": name, "ok": ok, "detail": expected if ok and expected else output[-300:]}
    except Exception as exc:
        return {"name": name, "ok": False, "detail": type(exc).__name__}


def main() -> int:
    checks = [
        command("telegram_target", ["/home/hermes/hermes-runtime/shared/venv/bin/hermes", "send", "--list", "telegram"], "Anton V"),
        command("startup_enabled", ["systemctl", "is-enabled", "hermes-startup-notify.service"], "enabled"),
        command("report_timer", ["systemctl", "is-active", "hermes-report-finalizer.timer"], "active"),
    ]
    try:
        with urllib.request.urlopen("http://127.0.0.1:2092/health", timeout=10) as response:
            payload = json.loads(response.read())
        checks.append({"name": "knowledge_health", "ok": payload.get("status") == "ok", "detail": "status=" + str(payload.get("status"))})
    except Exception as exc:
        checks.append({"name": "knowledge_health", "ok": False, "detail": type(exc).__name__})
    result = {"status": "PASS" if all(c["ok"] for c in checks) else "FAIL", "checks": checks}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
