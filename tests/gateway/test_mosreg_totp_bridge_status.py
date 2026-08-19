from gateway.mosreg_totp_bridge import TotpBridgeStore


def _register(store, now=100):
    return store.register_session({
        "task_id": "task_mosreg_1234",
        "session_secret": "A" * 43,
        "challenge": {
            "telegram_user_id": "7",
            "chat_id": "42",
            "reply_to_message_id": "99",
            "expires_at": now + 60,
        },
    }, now=now)


def test_reply_status_distinguishes_wrong_target_no_active_and_duplicate(tmp_path):
    store = TotpBridgeStore(root=tmp_path / "bridge")
    assert _register(store)["status"] == "registered"

    wrong = store.put_reply_value({
        "telegram_user_id": "7", "chat_id": "42", "reply_to_message_id": "98",
        "value_kind": "totp", "value": "123456",
    }, now=101)
    assert wrong == {"ok": False, "status": "wrong_reply_target"}

    stranger = store.put_reply_value({
        "telegram_user_id": "8", "chat_id": "42", "reply_to_message_id": "99",
        "value_kind": "totp", "value": "123456",
    }, now=101)
    assert stranger == {"ok": False, "status": "no_active_challenge"}

    stored = store.put_reply_value({
        "telegram_user_id": "7", "chat_id": "42", "reply_to_message_id": "99",
        "value_kind": "totp", "value": "123456",
    }, now=101)
    assert stored == {"ok": True, "status": "stored"}

    duplicate = store.put_reply_value({
        "telegram_user_id": "7", "chat_id": "42", "reply_to_message_id": "99",
        "value_kind": "totp", "value": "654321",
    }, now=102)
    assert duplicate == {"ok": False, "status": "already_submitted"}


def test_expired_reply_is_no_active_challenge(tmp_path):
    store = TotpBridgeStore(root=tmp_path / "bridge")
    _register(store, now=100)
    result = store.put_reply_value({
        "telegram_user_id": "7", "chat_id": "42", "reply_to_message_id": "99",
        "value_kind": "totp", "value": "123456",
    }, now=161)
    assert result == {"ok": False, "status": "no_active_challenge"}
