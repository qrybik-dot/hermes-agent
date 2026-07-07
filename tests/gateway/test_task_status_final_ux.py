"""Regression tests for compact Telegram terminal status cards."""

from gateway.run import _compact_terminal_task_status


def test_terminal_status_keeps_only_gauge_and_elapsed_time():
    verbose = (
        "🏆 ▰▰▰▰▰▰▰▰▰▰ 100%\n"
        "Завершено · 1 мин 43 сек\n"
        "Задача: Обнови только выбранные компоненты из Update Radar: uv 0.11.26 → 0\n\n"
        "✓ uv: 0.11.26 → 0.11.27; источник: ссылку\n"
        "✓ Запускаю тесты"
    )

    assert _compact_terminal_task_status(verbose) == (
        "🏆 ▰▰▰▰▰▰▰▰▰▰ 100%\n"
        "Завершено · 1 мин 43 сек"
    )


def test_terminal_status_never_leaks_truncated_task_or_completed_steps():
    compact = _compact_terminal_task_status(
        "⚠️ ▰▰▰▰▰▰▰▰▰▱ 90%\n"
        "Завершено частично · 2 мин 01 сек\n"
        "Задача: CLIProxyAPI 7.2.50 → 7.2\n"
        "✓ источник: ссылку"
    )

    assert "Задача:" not in compact
    assert "источник:" not in compact
    assert "✓" not in compact
    assert compact.count("\n") == 1


def test_terminal_status_handles_empty_content():
    assert _compact_terminal_task_status("") == ""
