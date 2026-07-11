#!/usr/bin/env python3
"""Read-only verifier for the minimal Hermes production contract."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import urllib.request
from pathlib import Path


def run(*args: str) -> tuple[int, str]:
    proc = subprocess.run(args, text=True, capture_output=True, timeout=20)
    return proc.returncode, (proc.stdout + proc.stderr).strip()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    checks: list[dict[str, object]] = []

    def check(name: str, ok: bool, detail: str) -> None:
        checks.append({"name": name, "ok": ok, "detail": detail})

    for key in ("agent_root", "knowledge_root"):
        root = Path(manifest["runtime"][key])
        for link in ("current", "previous"):
            path = root / link
            resolved = path.resolve(strict=False)
            check(f"{key}.{link}", path.is_symlink() and resolved.is_dir(), str(resolved))

    for unit in manifest["required_services"]:
        rc, out = run("systemctl", "is-active", unit)
        check(f"service.{unit}", rc == 0 and out == "active", out)

    for unit in manifest["required_timers"]:
        rc, out = run("systemctl", "is-active", unit)
        check(f"timer.{unit}", rc == 0 and out == "active", out)

    for unit in manifest["required_enabled_services"]:
        rc, out = run("systemctl", "is-enabled", unit)
        check(f"enabled.{unit}", rc == 0 and out == "enabled", out)

    rc, out = run("systemctl", "--failed", "--no-legend")
    check("systemd.failed", rc == 0 and not out, out or "0")

    for name, url in manifest["health_endpoints"].items():
        try:
            with urllib.request.urlopen(url, timeout=10) as response:
                payload = response.read().decode("utf-8", "replace")
            parsed = json.loads(payload)
            check(f"health.{name}", parsed.get("status") == "ok", "status=" + str(parsed.get("status")))
        except Exception as exc:
            check(f"health.{name}", False, type(exc).__name__)

    unit_names = manifest["required_services"] + manifest["required_enabled_services"]
    rc, unit_text = run("systemctl", "cat", *sorted(set(unit_names)))
    check("systemd.unit_read", rc == 0, "readable" if rc == 0 else "failed")
    for fragment in manifest["forbidden_runtime_fragments"]:
        check(f"forbidden.{fragment}", fragment not in unit_text, "absent" if fragment not in unit_text else "present")

    result = {"status": "PASS" if all(c["ok"] for c in checks) else "FAIL", "checks": checks}
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        for item in checks:
            print(("PASS" if item["ok"] else "FAIL"), item["name"], item["detail"])
        print(result["status"])
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
