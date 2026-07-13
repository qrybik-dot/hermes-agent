from gateway.messaging_contract import public_response_text, public_verdict


def test_public_verdict_has_only_three_states():
    assert public_verdict("success") == "READY"
    assert public_verdict("INCOMPLETE") == "PARTIAL"
    assert public_verdict("partial") == "PARTIAL"
    assert public_verdict("failed") == "BLOCKED"
    assert public_verdict("partial", has_usable_result=False) == "BLOCKED"


def test_public_response_hides_legacy_incomplete_label():
    assert public_response_text("INCOMPLETE\nНужна проверка") == "PARTIAL\nНужна проверка"
    assert public_response_text("Статус: INCOMPLETE\nНужна проверка").startswith("Статус: PARTIAL")
