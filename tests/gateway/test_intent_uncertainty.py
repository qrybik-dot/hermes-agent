from gateway.intent_uncertainty import (
    clarification_question, detect_uncertain_intent, is_reminder_request,
)


def test_reminder_without_schedule_is_blocked_before_execution():
    text = "Запиши напоминание - разобраться со штрафами, сделать заявления"
    assert is_reminder_request(text)
    assert detect_uncertain_intent(text) == "missing_reminder_date_time"
    assert "дату и время" in clarification_question(text, "missing_reminder_date_time")


def test_reminder_with_partial_schedule_requests_only_missing_part():
    assert detect_uncertain_intent("Напомни завтра разобраться со штрафами") == "missing_reminder_time"
    assert detect_uncertain_intent("Напомни в 10:00 разобраться со штрафами") == "missing_reminder_date"


def test_complete_or_relative_reminder_is_actionable():
    assert detect_uncertain_intent("Напомни завтра в 10:00 разобраться со штрафами") is None
    assert detect_uncertain_intent("Напомни через два часа разобраться со штрафами") is None


def test_non_reminder_save_does_not_trigger_reminder_gate():
    assert is_reminder_request("Запиши в память: разобраться со штрафами") is False
