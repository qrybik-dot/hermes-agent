from gateway.task_router import external_provider_fallback_safe


def test_public_simple_research_and_broad_personal_topics_are_allowed():
    assert external_provider_fallback_safe(
        "Объясни разницу между двумя публичными подходами", "simple"
    )
    assert external_provider_fallback_safe(
        "Найди свежие публичные источники о рынке", "research"
    )
    assert external_provider_fallback_safe(
        "Сравни общие подходы к медицинским исследованиям без персональных данных",
        "research",
    )
    assert external_provider_fallback_safe(
        "Проведи ревью структуры обезличенного резюме", "simple"
    )


def test_account_backed_or_identifier_bearing_requests_are_blocked():
    assert not external_provider_fallback_safe(
        "Проверь мою почту и календарь", "simple"
    )
    assert not external_provider_fallback_safe("Проверь Mosreg", "research")
    assert not external_provider_fallback_safe(
        "Напиши на user@example.com", "simple"
    )


def test_continuation_requires_a_self_contained_current_message():
    assert not external_provider_fallback_safe(
        "Продолжить deadbeef1234", "simple", continued=True
    )
    detailed = (
        "Продолжить deadbeef1234. Независимо от прежней переписки сравни три "
        "публичных варианта архитектуры, оцени скорость, качество и ограничения, "
        "а затем верни самодостаточный вывод без обращения к старому контексту. "
        "Не используй персональные данные и внутренние файлы."
    )
    assert external_provider_fallback_safe(detailed, "planning", continued=True)


def test_public_code_or_sanitized_logs_are_allowed_only_without_execution():
    assert external_provider_fallback_safe(
        "Проведи ревью этого публичного Python-фрагмента", "coding"
    )
    assert external_provider_fallback_safe(
        "Объясни обезличенный фрагмент systemd-лога", "server_debug"
    )
    assert not external_provider_fallback_safe(
        "Исправь код в репозитории", "coding"
    )
    assert not external_provider_fallback_safe(
        "Исправь код в репозитории", "coding", requires_execution=True
    )
    assert not external_provider_fallback_safe(
        "Перезапусти VPS-сервис", "server_debug"
    )
    assert not external_provider_fallback_safe(
        "Перезапусти VPS-сервис", "server_debug", requires_execution=True
    )
    assert not external_provider_fallback_safe(
        "Проанализируй большой документ", "long_context"
    )
