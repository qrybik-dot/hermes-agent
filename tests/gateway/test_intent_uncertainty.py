from gateway.intent_uncertainty import (
    clarification_choices, clarification_question, detect_uncertain_intent, is_reminder_request,
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


def test_short_action_uses_reply_context_instead_of_guessing():
    assert detect_uncertain_intent("обнови") == "missing_object"
    assert detect_uncertain_intent("обнови", context_text="Update Radar: Graphify 0.9.6") is None


def test_vague_outcome_uses_open_question():
    assert detect_uncertain_intent("сделай нормально") == "vague_outcome"
    assert clarification_choices("сделай нормально", "vague_outcome") is None
    assert "результат" in clarification_question("сделай нормально", "vague_outcome")


def test_ambiguous_record_uses_destination_buttons():
    text = "Запиши разобраться со штрафами"
    assert detect_uncertain_intent(text) == "ambiguous_destination"
    assert clarification_choices(text, "ambiguous_destination") == [
        "В задачи", "Как напоминание", "В календарь", "В память",
    ]


def test_explicit_domain_save_is_not_reclassified_as_generic_destination():
    assert detect_uncertain_intent("Сохрани место для будущих поездок") is None
    assert detect_uncertain_intent("Сохрани файл отчёта") is None
    assert detect_uncertain_intent("Запиши контакт врача") is None
