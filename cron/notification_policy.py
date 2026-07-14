"""Persistent, privacy-safe notification deduplication for cron incidents.

The initial integration is intentionally narrow: only failed cron runs are
eligible, and the default mode is ``off``.  ``observe`` records decisions but
never changes delivery.  ``enforce`` may suppress a repeated failure after a
successful prior delivery.  Raw error text and user content are never stored.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import sqlite3
import time
from typing import Any, Mapping

from hermes_constants import get_hermes_home


NOTIFICATION_MODES = frozenset({"off", "observe", "enforce"})
_SEVERITY_RANK = {"info": 0, "warning": 1, "critical": 2}
_DEFAULT_COOLDOWN = {"info": 7 * 86400.0, "warning": 86400.0, "critical": 3600.0}


@dataclass(frozen=True)
class NotificationDecision:
    action: str  # pass | send | suppress | would_send | would_suppress
    reason: str
    fingerprint: str
    mode: str


def notification_mode(value: str | None = None) -> str:
    candidate = str(
        value if value is not None else os.getenv("HERMES_NOTIFICATION_POLICY_MODE", "off")
    ).strip().lower()
    return candidate if candidate in NOTIFICATION_MODES else "off"


def _bounded_text(value: Any, limit: int = 120) -> str:
    return " ".join(str(value or "").split())[:limit]


def classify_cron_failure(error: str | None) -> str:
    """Map raw errors to a small non-sensitive class used only for identity."""
    value = str(error or "").lower()
    if "429" in value or "rate limit" in value or "quota" in value or "usage limit" in value:
        return "rate_limit"
    if "timeout" in value or "timed out" in value or "readtimeout" in value:
        return "timeout"
    if "authenticat" in value or "authoriz" in value or "401" in value or "403" in value:
        return "auth"
    return "other"


def cron_failure_fingerprint(job_id: str, failure_class: str = "other") -> str:
    stable = f"cron.failure\x1f{_bounded_text(job_id, 160)}\x1f{_bounded_text(failure_class, 32)}"
    return hashlib.sha256(stable.encode("utf-8")).hexdigest()


class NotificationStateStore:
    """Additive SQLite state. No message bodies or personal memory are stored."""

    def __init__(self, db_path: str | Path | None = None):
        self.db_path = Path(db_path or (Path(get_hermes_home()) / "state.db"))
        self.ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=5.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    def ensure_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS notification_incidents (
                    fingerprint TEXT PRIMARY KEY,
                    source TEXT NOT NULL,
                    category TEXT NOT NULL,
                    resource_ref TEXT NOT NULL,
                    severity TEXT NOT NULL,
                    state TEXT NOT NULL,
                    first_seen_at REAL NOT NULL,
                    last_seen_at REAL NOT NULL,
                    last_would_send_at REAL,
                    last_delivered_at REAL,
                    delivered_count INTEGER NOT NULL DEFAULT 0,
                    resolved_at REAL
                );
                CREATE INDEX IF NOT EXISTS idx_notification_incidents_state
                    ON notification_incidents(state, last_seen_at DESC);
                """
            )

    def decide_active(
        self,
        *,
        fingerprint: str,
        source: str,
        category: str,
        resource_ref: str,
        severity: str,
        cooldown_seconds: float,
        mode: str,
        now: float | None = None,
    ) -> NotificationDecision:
        ts = float(time.time() if now is None else now)
        severity = severity if severity in _SEVERITY_RANK else "warning"
        cooldown = max(60.0, min(float(cooldown_seconds), 30 * 86400.0))
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM notification_incidents WHERE fingerprint=?",
                (fingerprint,),
            ).fetchone()
            should_send = False
            reason = "cooldown_active"
            if row is None:
                should_send = True
                reason = "new_incident"
                conn.execute(
                    """INSERT INTO notification_incidents (
                        fingerprint,source,category,resource_ref,severity,state,
                        first_seen_at,last_seen_at,last_would_send_at
                    ) VALUES (?,?,?,?,?,'active',?,?,?)""",
                    (
                        fingerprint, _bounded_text(source), _bounded_text(category),
                        _bounded_text(resource_ref), severity, ts, ts, ts,
                    ),
                )
            else:
                prior_rank = _SEVERITY_RANK.get(str(row["severity"]), 1)
                reference = row["last_delivered_at"] if mode == "enforce" else row["last_would_send_at"]
                if row["state"] == "resolved":
                    should_send = True
                    reason = "reopened"
                elif _SEVERITY_RANK[severity] > prior_rank:
                    should_send = True
                    reason = "severity_increased"
                elif reference is None or ts - float(reference) >= cooldown:
                    should_send = True
                    reason = "cooldown_elapsed"
                conn.execute(
                    """UPDATE notification_incidents SET
                        source=?,category=?,resource_ref=?,severity=?,state='active',
                        last_seen_at=?,last_would_send_at=?,resolved_at=NULL
                    WHERE fingerprint=?""",
                    (
                        _bounded_text(source), _bounded_text(category), _bounded_text(resource_ref),
                        severity, ts, ts if should_send else row["last_would_send_at"], fingerprint,
                    ),
                )

        if mode == "observe":
            action = "would_send" if should_send else "would_suppress"
        else:
            action = "send" if should_send else "suppress"
        return NotificationDecision(action, reason, fingerprint, mode)

    def mark_delivered(self, fingerprint: str, *, now: float | None = None) -> None:
        ts = float(time.time() if now is None else now)
        with self._connect() as conn:
            conn.execute(
                """UPDATE notification_incidents SET
                    last_delivered_at=?, delivered_count=delivered_count+1
                WHERE fingerprint=?""",
                (ts, fingerprint),
            )

    def resolve(self, fingerprint: str, *, now: float | None = None) -> bool:
        ts = float(time.time() if now is None else now)
        with self._connect() as conn:
            cur = conn.execute(
                """UPDATE notification_incidents SET
                    state='resolved', last_seen_at=?, resolved_at=?
                WHERE fingerprint=? AND state='active'""",
                (ts, ts, fingerprint),
            )
        return bool(cur.rowcount)

    def resolve_resource(
        self,
        *,
        source: str,
        resource_ref: str,
        now: float | None = None,
    ) -> int:
        ts = float(time.time() if now is None else now)
        with self._connect() as conn:
            cur = conn.execute(
                """UPDATE notification_incidents SET
                    state='resolved', last_seen_at=?, resolved_at=?
                WHERE source=? AND resource_ref=? AND state='active'""",
                (ts, ts, _bounded_text(source), _bounded_text(resource_ref)),
            )
        return int(cur.rowcount)


def _policy_values(job: Mapping[str, Any]) -> tuple[str, float]:
    policy = job.get("notification_policy")
    if not isinstance(policy, Mapping):
        policy = {}
    severity = str(policy.get("severity") or "warning").strip().lower()
    if severity not in _SEVERITY_RANK:
        severity = "warning"
    try:
        cooldown = float(policy.get("cooldown_seconds", _DEFAULT_COOLDOWN[severity]))
    except (TypeError, ValueError):
        cooldown = _DEFAULT_COOLDOWN[severity]
    return severity, cooldown


def decide_cron_failure(
    job: Mapping[str, Any],
    *,
    error: str | None = None,
    mode: str | None = None,
    db_path: str | Path | None = None,
    now: float | None = None,
) -> NotificationDecision:
    resolved_mode = notification_mode(mode)
    job_id = _bounded_text(job.get("id") or "unknown", 160)
    failure_class = classify_cron_failure(error)
    fingerprint = cron_failure_fingerprint(job_id, failure_class)
    if resolved_mode == "off":
        return NotificationDecision("pass", "policy_off", fingerprint, resolved_mode)
    severity, cooldown = _policy_values(job)
    try:
        return NotificationStateStore(db_path).decide_active(
            fingerprint=fingerprint,
            source="cron",
            category=f"job_failure.{failure_class}",
            resource_ref=job_id,
            severity=severity,
            cooldown_seconds=cooldown,
            mode=resolved_mode,
            now=now,
        )
    except Exception:
        # Fail open: a state-store problem must not hide a real incident.
        action = "would_send" if resolved_mode == "observe" else "send"
        return NotificationDecision(action, "state_store_error", fingerprint, resolved_mode)


def mark_cron_failure_delivered(
    job: Mapping[str, Any],
    *,
    error: str | None = None,
    db_path: str | Path | None = None,
    now: float | None = None,
) -> None:
    NotificationStateStore(db_path).mark_delivered(
        cron_failure_fingerprint(
            str(job.get("id") or "unknown"), classify_cron_failure(error),
        ),
        now=now,
    )


def resolve_cron_failure(
    job: Mapping[str, Any],
    *,
    db_path: str | Path | None = None,
    now: float | None = None,
) -> bool:
    return bool(
        NotificationStateStore(db_path).resolve_resource(
            source="cron", resource_ref=str(job.get("id") or "unknown"), now=now,
        )
    )


__all__ = [
    "NOTIFICATION_MODES",
    "NotificationDecision",
    "NotificationStateStore",
    "classify_cron_failure",
    "cron_failure_fingerprint",
    "decide_cron_failure",
    "mark_cron_failure_delivered",
    "notification_mode",
    "resolve_cron_failure",
]
