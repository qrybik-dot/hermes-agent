from __future__ import annotations

import json
from datetime import datetime, timezone

from hermes_cli import update_radar_actions as actions


def _findings():
    return [
        {
            "key": "ubuntu_security",
            "name": "Ubuntu security updates",
            "finding_type": "security",
            "current": None,
            "latest": ["gzip"],
            "status": "ok",
            "verdict": "🚨 срочно",
            "reason": "1 security package pending",
            "source": "apt security repositories",
        },
        {
            "key": "cliproxy",
            "name": "CLIProxyAPI",
            "finding_type": "release",
            "current": "7.2.50",
            "latest": "7.2.51",
            "status": "ok",
            "verdict": "✅ можно обновлять",
            "reason": "new release",
            "source": "https://example.test/cliproxy",
        },
        {
            "key": "uv",
            "name": "uv",
            "finding_type": "release",
            "current": "0.11.26",
            "latest": "0.11.27",
            "status": "ok",
            "verdict": "✅ можно обновлять",
            "reason": "new release",
            "source": "https://example.test/uv",
        },
        {
            "key": "graphify",
            "name": "Graphify",
            "finding_type": "release",
            "current": "0.9.5",
            "latest": "0.9.8",
            "status": "ok",
            "verdict": "🧪 сначала пилот",
            "reason": "requires regression testing",
            "source": "https://example.test/graphify",
        },
        {
            "key": "docker_inventory",
            "name": "Active Docker containers",
            "finding_type": "inventory",
            "current": ["old"],
            "latest": ["new"],
            "status": "ok",
            "verdict": "🧪 проверить",
            "reason": "runtime inventory changed",
            "source": "local",
        },
    ]


def _isolate(monkeypatch, tmp_path):
    monkeypatch.setattr(actions, "ACTION_FILE", tmp_path / "actions.json")
    monkeypatch.setattr(actions, "RADAR_STATE_FILE", tmp_path / "state.json")


def test_action_groups_updates_separately_from_pilots(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    action = actions.create_action(_findings())

    assert action["security"] == ["ubuntu_security"]
    assert action["recommended"] == ["cliproxy", "uv"]
    assert action["checks"] == ["graphify", "docker_inventory"]
    assert action["selected"] == ["ubuntu_security", "cliproxy", "uv"]

    text = actions.render_home(action)
    assert "🚨 Безопасность" in text
    assert "✅ Рекомендуемые обновления" in text
    assert "🧪 Требуют проверки" in text
    assert "https://" not in text


def test_keyboard_supports_manual_multiselect_and_confirmation(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    action = actions.create_action(_findings())

    home = actions.keyboard_spec(action)
    labels = [button["text"] for row in home for button in row]
    assert "🚨 Обновить безопасность" in labels
    assert "✅ Обновить рекомендуемое" in labels
    assert "⚙️ Выбрать вручную" in labels
    assert "🧪 Запустить проверки" in labels
    assert all(len(button["callback_data"].encode()) <= 64 for row in home for button in row)

    manual = actions.update_action(action["token"], stage="manual")
    manual = actions.toggle_selection(action["token"], "uv")
    assert "uv" not in manual["selected"]
    manual_rows = actions.keyboard_spec(manual)
    assert any("⬜ uv" == button["text"] for row in manual_rows for button in row)

    confirm = actions.update_action(action["token"], stage="confirm")
    assert "Подтвердите обновление" in actions.render_confirm(confirm)
    assert "▶️ Подтвердить и запустить" in [
        button["text"] for row in actions.keyboard_spec(confirm) for button in row
    ]


def test_agent_prompt_contains_only_selected_updates(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    action = actions.create_action(_findings())
    action = actions.set_selection(action["token"], ["cliproxy"], stage="confirm")

    prompt = actions.build_agent_prompt(action, mode="update")
    assert "CLIProxyAPI" in prompt
    assert "uv 0.11" not in prompt
    assert "Graphify" not in prompt
    assert "Не обновляй остальные компоненты" in prompt
    assert "VPS автоматически не перезагружай" in prompt


def test_postpone_rearms_finding_for_tomorrow_and_snoozes_until_then(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    action = actions.create_action(_findings())
    actions.RADAR_STATE_FILE.write_text(json.dumps({
        "schema": 3,
        "observed": {},
        "notified": {item["key"]: "fingerprint" for item in _findings()},
    }))

    postponed = actions.postpone_until_tomorrow(action["token"])
    assert postponed["status"] == "postponed"
    state = json.loads(actions.RADAR_STATE_FILE.read_text())
    assert state["notified"] == {}
    assert actions.filter_snoozed(_findings()) == []

    store = json.loads(actions.ACTION_FILE.read_text())
    store["snoozed"] = {
        signature: "2000-01-01T00:00:00Z"
        for signature in store.get("snoozed", {})
    }
    actions.ACTION_FILE.write_text(json.dumps(store))
    assert len(actions.filter_snoozed(_findings())) == len(_findings())
