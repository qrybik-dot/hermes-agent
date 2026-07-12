import pytest

from gateway.avito_intent import detect_avito_intent, is_avito_followup


@pytest.mark.parametrize(
    "text",
    [
        "@avito\n[The user sent an image]",
        "@авито четыре бутыли",
        "Авит, продаём это [The user sent an image]",
        "Сделай объявление на Авито",
        "Четыре бутыли 18,9 л, б/у. Продаём это",
    ],
)
def test_high_confidence_avito_intents_start_without_clarification(text):
    assert detect_avito_intent(text).kind == "avito"


@pytest.mark.parametrize(
    "text",
    [
        "[The user sent an image]",
        "Найди цены",
        "Оцени это",
        "Сделай объявление",
    ],
)
def test_ambiguous_inputs_require_clarification(text):
    assert detect_avito_intent(text).kind == "clarify"


@pytest.mark.parametrize(
    "text",
    [
        "Хочу купить такую бутыль на Авито",
        "Добавь эти фото в базу знаний",
        "Сделай объявление для Telegram",
        "Не для Авито, просто опиши фото",
        "Посмотри объявление на Avito и оцени цену",
        "https://www.avito.ru/korolev/example",
        "Какая завтра погода?",
    ],
)
def test_negative_and_other_intents_do_not_start_avito_seller(text):
    assert detect_avito_intent(text).kind == "other"


def test_followup_accepts_more_photos_but_rejects_topic_switch():
    assert is_avito_followup("ещё фото\n[The user sent an image]")
    assert is_avito_followup("добавь торг")
    assert not is_avito_followup("Какая завтра погода?")
    assert not is_avito_followup("добавь это в базу знаний")
