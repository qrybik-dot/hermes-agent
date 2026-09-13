from __future__ import annotations

import ast
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SERVICE_PATH = ROOT / "ops" / "telegram_ro" / "service.py"


def _load_service():
    spec = importlib.util.spec_from_file_location("telegram_ro_service", SERVICE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_private_message_url_parsing():
    service = _load_service()
    peer, message_id = service.parse_message_url("https://t.me/c/1166593449/224981")
    assert peer == "-1001166593449"
    assert message_id == 224981


def test_public_message_url_parsing():
    service = _load_service()
    assert service.parse_message_url("https://t.me/example_channel/123") == ("example_channel", 123)
    assert service.parse_message_url("https://t.me/s/example_channel/123") == ("example_channel", 123)


def test_non_message_urls_rejected():
    service = _load_service()
    for url in [
        "http://t.me/example_channel/123",
        "https://example.com/c/1166593449/224981",
        "https://t.me/+invite",
        "https://t.me/c/not-a-number/123",
    ]:
        try:
            service.parse_message_url(url)
        except service.RequestError:
            pass
        else:
            raise AssertionError(url)


def test_only_three_protocol_actions_exist():
    service = _load_service()
    assert service.ALLOWED_ACTIONS == frozenset({"health", "message", "download"})
    for action in [
        "send", "send_message", "reply", "forward", "edit", "edit_message",
        "delete", "delete_messages", "reaction", "join", "leave", "raw", "invoke",
    ]:
        try:
            service.validate_action(action)
        except service.RequestError as exc:
            assert str(exc) == "action_not_allowed"
        else:
            raise AssertionError(f"forbidden action accepted: {action}")


def test_service_ast_has_no_telegram_mutator_calls_or_generic_invoke():
    tree = ast.parse(SERVICE_PATH.read_text(encoding="utf-8"))
    attrs = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
    banned = {
        "send_message", "send_file", "forward_messages", "edit_message", "delete_messages",
        "send_reaction", "join_channel", "leave_channel", "__call__", "invoke",
    }
    assert attrs.isdisjoint(banned), attrs & banned
