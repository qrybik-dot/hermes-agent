#!/usr/bin/env python3
"""Artifact Store reconciliation, retention cleanup, quota alerts, and reports."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hermes_cli.artifact_store import ArtifactStore, utcnow


def _fmt_gib(value: int) -> str:
    return f"{value / 1024**3:.2f} ГБ"


def _thresholds(store: ArtifactStore) -> list[tuple[str, int]]:
    return [
        ("warning", store.limits.warning),
        ("critical", store.limits.critical),
        ("emergency", store.limits.emergency),
    ]


def _top_groups(store: ArtifactStore, limit: int = 5) -> list[dict[str, Any]]:
    with store.connect() as conn:
        rows = conn.execute(
            "SELECT version_group,COUNT(*) AS count,COALESCE(SUM(size_bytes),0) AS size "
            "FROM artifacts WHERE local_available=1 AND status != 'trash' "
            "GROUP BY version_group ORDER BY size DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [{"version_group": row["version_group"], "count": row["count"], "size_bytes": row["size"]} for row in rows]


def _send_telegram(message: str, dry_run: bool = False) -> dict[str, Any]:
    if dry_run:
        return {"sent": False, "dry_run": True, "message": message}
    command = [sys.executable, "-m", "hermes_cli.main", "send", "--to", "telegram", message]
    completed = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=90, check=False)
    return {
        "sent": completed.returncode == 0,
        "returncode": completed.returncode,
        "stdout": completed.stdout[-1000:],
        "stderr": completed.stderr[-1000:],
    }


def update_alerts(store: ArtifactStore, *, dry_run: bool = False) -> dict[str, Any]:
    health = store.health()
    usage = int(health["usage_bytes"])
    active_level = "ok"
    crossed: list[str] = []
    cleared: list[str] = []
    sent: list[dict[str, Any]] = []

    with store.lock(), store.connect() as conn:
        for name, threshold in _thresholds(store):
            row = conn.execute("SELECT * FROM storage_alerts WHERE threshold=?", (name,)).fetchone()
            was_active = bool(row and row["active"])
            is_active = usage >= threshold
            if is_active:
                active_level = name
            if is_active and not was_active:
                crossed.append(name)
                conn.execute(
                    "INSERT INTO storage_alerts(threshold,active,first_crossed_at,last_notified_at,cleared_at,usage_bytes) "
                    "VALUES(?,1,?,?,NULL,?) ON CONFLICT(threshold) DO UPDATE SET active=1,"
                    "first_crossed_at=excluded.first_crossed_at,last_notified_at=excluded.last_notified_at,"
                    "cleared_at=NULL,usage_bytes=excluded.usage_bytes",
                    (name, utcnow(), utcnow(), usage),
                )
            elif not is_active and was_active:
                cleared.append(name)
                conn.execute(
                    "UPDATE storage_alerts SET active=0,cleared_at=?,usage_bytes=? WHERE threshold=?",
                    (utcnow(), usage, name),
                )
            else:
                conn.execute(
                    "INSERT INTO storage_alerts(threshold,active,usage_bytes) VALUES(?,?,?) "
                    "ON CONFLICT(threshold) DO UPDATE SET active=excluded.active,usage_bytes=excluded.usage_bytes",
                    (name, int(is_active), usage),
                )
        conn.commit()

    if crossed:
        highest = crossed[-1]
        icons = {"warning": "⚠️", "critical": "🟠", "emergency": "🔴"}
        groups = _top_groups(store)
        group_lines = "\n".join(
            f"• {item['version_group']}: {_fmt_gib(item['size_bytes'])}, файлов {item['count']}"
            for item in groups
        ) or "• Нет данных"
        message = (
            f"{icons[highest]} Хранилище артефактов Hermes: {highest.upper()}\n\n"
            f"Использовано: {_fmt_gib(usage)} из 4.00 ГБ\n"
            f"Свободно: {_fmt_gib(max(0, store.limits.hard - usage))}\n"
            f"Файлов: {health['artifact_count']}\n\n"
            f"Крупнейшие группы:\n{group_lines}\n\n"
            "Безопасная очистка затрагивает только просроченные temporary/draft/superseded/trash. "
            "Единственные финальные файлы автоматически не удаляются."
        )
        sent.append(_send_telegram(message, dry_run=dry_run))

    if cleared and not crossed and usage < store.limits.warning:
        sent.append(
            _send_telegram(
                f"✅ Хранилище артефактов снова ниже порога: {_fmt_gib(usage)} из 4.00 ГБ.",
                dry_run=dry_run,
            )
        )
    return {
        "usage_bytes": usage,
        "active_level": active_level,
        "crossed": crossed,
        "cleared": cleared,
        "delivery": sent,
    }


def run(*, cleanup_apply: bool, dry_run_alert: bool = False) -> dict[str, Any]:
    store = ArtifactStore()
    result = {
        "health_before": store.health(),
        "reconcile": store.reconcile(),
        "cleanup": store.cleanup(dry_run=not cleanup_apply, safe_only=True),
        "alerts": update_alerts(store, dry_run=dry_run_alert),
        "health_after": store.health(),
    }
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply-cleanup", action="store_true")
    parser.add_argument("--dry-run-alert", action="store_true")
    parser.add_argument("--health-only", action="store_true")
    args = parser.parse_args()
    store = ArtifactStore()
    result = store.health() if args.health_only else run(
        cleanup_apply=args.apply_cleanup,
        dry_run_alert=args.dry_run_alert,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
