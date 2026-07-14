from unittest.mock import patch

from cron.notification_policy import NotificationDecision
from cron.scheduler import run_one_job


def _job():
    return {"id": "job-1", "name": "Health check"}


def _base_patches():
    return (
        patch("cron.scheduler.claim_dispatch", return_value=True),
        patch("cron.scheduler.run_job", return_value=(False, "saved output", "", "boom")),
        patch("cron.scheduler.save_job_output", return_value="/tmp/output.md"),
        patch("cron.scheduler.mark_job_run"),
    )


def test_observe_mode_never_changes_delivery():
    p_claim, p_run, p_save, p_mark = _base_patches()
    with p_claim, p_run, p_save, p_mark, \
         patch("cron.scheduler.notification_mode", return_value="observe"), \
         patch(
             "cron.scheduler.decide_cron_failure",
             return_value=NotificationDecision("would_suppress", "cooldown_active", "a" * 64, "observe"),
         ), \
         patch("cron.scheduler._deliver_result", return_value=None) as deliver, \
         patch("cron.scheduler.mark_cron_failure_delivered") as persisted:
        assert run_one_job(_job()) is True
    deliver.assert_called_once()
    persisted.assert_called_once_with(_job(), error="boom")


def test_enforce_mode_suppresses_only_policy_duplicate():
    p_claim, p_run, p_save, p_mark = _base_patches()
    with p_claim, p_run, p_save, p_mark, \
         patch("cron.scheduler.notification_mode", return_value="enforce"), \
         patch(
             "cron.scheduler.decide_cron_failure",
             return_value=NotificationDecision("suppress", "cooldown_active", "a" * 64, "enforce"),
         ), \
         patch("cron.scheduler._deliver_result") as deliver:
        assert run_one_job(_job()) is True
    deliver.assert_not_called()


def test_success_only_resolves_state_and_keeps_normal_delivery():
    job = _job()
    with patch("cron.scheduler.claim_dispatch", return_value=True), \
         patch("cron.scheduler.run_job", return_value=(True, "saved output", "Daily result", None)), \
         patch("cron.scheduler.save_job_output", return_value="/tmp/output.md"), \
         patch("cron.scheduler.mark_job_run"), \
         patch("cron.scheduler.notification_mode", return_value="observe"), \
         patch("cron.scheduler.resolve_cron_failure", return_value=True) as resolved, \
         patch("cron.scheduler._deliver_result", return_value=None) as deliver:
        assert run_one_job(job) is True
    resolved.assert_called_once_with(job)
    deliver.assert_called_once()
