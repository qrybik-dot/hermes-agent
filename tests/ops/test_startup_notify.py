from ops.systemd import hermes_startup_notify as notify


def test_success_message_is_explicit_restart_notice(monkeypatch):
    sent = []
    recorded = []
    monkeypatch.setattr(notify.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(notify, "telegram_config", lambda: ("token", "chat"))
    monkeypatch.setattr(notify, "gateway_state", lambda: (True, 123))
    monkeypatch.setattr(notify, "already_notified", lambda _pid: False)
    monkeypatch.setattr(notify, "journal_errors", lambda: [])
    monkeypatch.setattr(notify, "choose_detail", lambda: "Готов принимать задачи.")
    monkeypatch.setattr(notify, "send_message", lambda _token, _chat, text: sent.append(text) or True)
    monkeypatch.setattr(notify, "record_notified", lambda pid: recorded.append(pid))

    assert notify.main() == 0
    assert sent == ["🟢 Hermes перезапущен и снова онлайн. Готов принимать задачи."]
    assert recorded == [123]


def test_same_gateway_pid_does_not_duplicate_notice(monkeypatch):
    sent = []
    monkeypatch.setattr(notify.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(notify, "telegram_config", lambda: ("token", "chat"))
    monkeypatch.setattr(notify, "gateway_state", lambda: (True, 123))
    monkeypatch.setattr(notify, "already_notified", lambda _pid: True)
    monkeypatch.setattr(notify, "send_message", lambda *_args: sent.append(True) or True)

    assert notify.main() == 0
    assert sent == []
