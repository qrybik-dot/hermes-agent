import sqlite3
from concurrent.futures import ThreadPoolExecutor

from cron.notification_policy import (
    NotificationStateStore,
    cron_failure_fingerprint,
    decide_cron_failure,
    mark_cron_failure_delivered,
    notification_mode,
    resolve_cron_failure,
)


def test_mode_defaults_off(monkeypatch):
    monkeypatch.delenv("HERMES_NOTIFICATION_POLICY_MODE", raising=False)
    assert notification_mode() == "off"
    assert notification_mode("bad") == "off"


def test_off_mode_does_not_create_database(tmp_path):
    path = tmp_path / "state.db"
    decision = decide_cron_failure({"id": "job-1"}, mode="off", db_path=path)
    assert decision.action == "pass"
    assert not path.exists()


def test_observe_records_but_never_suppresses_delivery(tmp_path):
    path = tmp_path / "state.db"
    job = {"id": "job-1", "notification_policy": {"cooldown_seconds": 3600}}
    first = decide_cron_failure(job, mode="observe", db_path=path, now=1000)
    second = decide_cron_failure(job, mode="observe", db_path=path, now=1010)
    assert first.action == "would_send"
    assert second.action == "would_suppress"


def test_enforce_suppresses_duplicate_after_real_delivery(tmp_path):
    path = tmp_path / "state.db"
    job = {"id": "job-1", "notification_policy": {"cooldown_seconds": 3600}}
    first = decide_cron_failure(job, mode="enforce", db_path=path, now=1000)
    assert first.action == "send"
    mark_cron_failure_delivered(job, db_path=path, now=1001)
    second = decide_cron_failure(job, mode="enforce", db_path=path, now=1010)
    assert second.action == "suppress"
    third = decide_cron_failure(job, mode="enforce", db_path=path, now=5001)
    assert third.action == "send"


def test_severity_increase_bypasses_cooldown(tmp_path):
    path = tmp_path / "state.db"
    warning = {"id": "job-1", "notification_policy": {"severity": "warning"}}
    critical = {"id": "job-1", "notification_policy": {"severity": "critical"}}
    decide_cron_failure(warning, mode="enforce", db_path=path, now=1000)
    mark_cron_failure_delivered(warning, db_path=path, now=1001)
    decision = decide_cron_failure(critical, mode="enforce", db_path=path, now=1010)
    assert decision.action == "send"
    assert decision.reason == "severity_increased"


def test_resolution_and_reopen_are_persisted_across_store_instances(tmp_path):
    path = tmp_path / "state.db"
    job = {"id": "job-1"}
    decide_cron_failure(job, mode="enforce", db_path=path, now=1000)
    mark_cron_failure_delivered(job, db_path=path, now=1001)
    assert resolve_cron_failure(job, db_path=path, now=1100)
    reopened = decide_cron_failure(job, mode="enforce", db_path=path, now=1110)
    assert reopened.action == "send"
    assert reopened.reason == "reopened"


def test_state_schema_stores_no_message_body(tmp_path):
    path = tmp_path / "state.db"
    decide_cron_failure({"id": "job-1"}, mode="observe", db_path=path, now=1000)
    with sqlite3.connect(path) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(notification_incidents)")}
    assert "message" not in columns
    assert "content" not in columns
    assert "error" not in columns


def test_fingerprint_is_stable_and_does_not_include_error_text():
    assert cron_failure_fingerprint("job-1") == cron_failure_fingerprint("job-1")
    assert cron_failure_fingerprint("job-1") != cron_failure_fingerprint("job-2")


def test_distinct_failure_classes_are_not_suppressed_as_the_same_incident(tmp_path):
    path = tmp_path / "state.db"
    job = {"id": "job-1"}
    timeout = decide_cron_failure(
        job, error="ReadTimeout", mode="enforce", db_path=path, now=1000,
    )
    mark_cron_failure_delivered(job, error="ReadTimeout", db_path=path, now=1001)
    auth = decide_cron_failure(
        job, error="HTTP 401 authentication failed", mode="enforce", db_path=path, now=1010,
    )
    assert timeout.action == "send"
    assert auth.action == "send"
    assert timeout.fingerprint != auth.fingerprint


def test_parallel_observers_share_one_transactionally_safe_row(tmp_path):
    path = tmp_path / "state.db"
    job = {"id": "job-1"}

    def observe(index: int):
        return decide_cron_failure(
            job, error="timeout", mode="observe", db_path=path, now=1000 + index,
        )

    with ThreadPoolExecutor(max_workers=8) as pool:
        decisions = list(pool.map(observe, range(24)))
    assert len(decisions) == 24
    with sqlite3.connect(path) as conn:
        count = conn.execute("SELECT COUNT(*) FROM notification_incidents").fetchone()[0]
    assert count == 1
