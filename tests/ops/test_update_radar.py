import importlib.util
import json
import sys
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[2] / "scripts" / "update_radar.py"
spec = importlib.util.spec_from_file_location("update_radar", MODULE_PATH)
radar = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = radar
assert spec.loader is not None
spec.loader.exec_module(radar)


def test_is_newer_semver_and_v_prefix():
    assert radar.is_newer("v0.18.1", "0.18.0") is True
    assert radar.is_newer("7.2.49", "7.2.49") is False


def test_release_finding_for_core_requires_pilot():
    obs = radar.Observation(
        key="hermes", name="Hermes Agent", kind="release",
        current="0.18.0", latest="0.19.0", source="official",
    )
    findings = radar.evaluate([obs], {"observed": {}})
    assert len(findings) == 1
    assert findings[0].verdict == "🧪 сначала пилот"


def test_security_packages_are_urgent():
    obs = radar.Observation(
        key="ubuntu_security", name="Ubuntu security", kind="security",
        latest=["libssl3", "curl"], source="apt",
    )
    findings = radar.evaluate([obs], {"observed": {}})
    assert findings[0].verdict == "🚨 срочно"


def test_inventory_requires_a_previous_baseline():
    obs = radar.Observation(
        key="model_catalog", name="Models", kind="inventory",
        current=["a", "b"],
    )
    assert radar.evaluate([obs], {"observed": {}}) == []
    findings = radar.evaluate(
        [obs],
        {"observed": {"model_catalog": {"current": ["a"]}}},
    )
    assert len(findings) == 1
    assert findings[0].finding_type == "inventory"


def test_fingerprint_is_stable_and_ignores_release_notes():
    base = radar.Finding(
        key="uv", name="uv", finding_type="release", current="1", latest="2",
        status="ok", verdict="✅ можно обновлять", reason="new", source="official",
        release_notes="first",
    )
    changed_notes = radar.Finding(**{**radar.asdict(base), "release_notes": "second"})
    assert radar.finding_fingerprint(base) == radar.finding_fingerprint(changed_notes)


def test_fixture_shape_loads(tmp_path):
    fixture = tmp_path / "fixture.json"
    fixture.write_text(json.dumps({"observations": [{
        "key": "uv", "name": "uv", "kind": "release",
        "current": "0.1.0", "latest": "0.2.0", "source": "official"
    }]}))
    observations = radar.observations_from_fixture(fixture)
    assert observations[0].latest == "0.2.0"
    assert radar.evaluate(observations, {"observed": {}})


def test_debian_epoch_version_is_parsed_after_colon():
    assert radar.numeric_version("1:150.0.7871.46-1") == "150.0.7871.46"
    assert radar.is_newer("1:150.0.7871.46-1", "150.0.7871.46") is False


def test_known_health_failure_is_baselined_then_transition_notified():
    obs = radar.Observation(
        key="auth", name="Auth", kind="health", status="error",
        details="expired", error="expired",
    )
    assert radar.evaluate([obs], {"observed": {}}) == []
    assert radar.evaluate([obs], {"observed": {"auth": {"status": "error"}}}) == []
    findings = radar.evaluate([obs], {"observed": {"auth": {"status": "ok"}}})
    assert len(findings) == 1


def test_model_catalog_addition_produces_safe_proposal():
    obs = radar.Observation(
        key="codex_model_catalog",
        name="Codex subscription model catalog",
        kind="inventory",
        current=["gpt-5.5", "gpt-5.6"],
        source="live",
        details={"configured_models": ["gpt-5.5"]},
    )
    findings = radar.evaluate(
        [obs],
        {"observed": {"codex_model_catalog": {"current": ["gpt-5.5"]}}},
    )
    assert len(findings) == 1
    finding = findings[0]
    assert finding.finding_type == "model_catalog"
    assert finding.verdict == "🧪 доступна новая модель"
    assert finding.details["added"] == ["gpt-5.6"]
    assert finding.details["automatic_routing_change"] is False
    assert "coding" in finding.details["role_suggestions"]["gpt-5.6"]


def test_active_model_removal_is_urgent():
    obs = radar.Observation(
        key="antigravity_model_catalog",
        name="Antigravity model catalog",
        kind="inventory",
        current=["gemini-3.1-pro-low"],
        source="live",
        details={"configured_models": ["claude-sonnet-4-6", "gemini-3.1-pro-low"]},
    )
    findings = radar.evaluate(
        [obs],
        {"observed": {"antigravity_model_catalog": {"current": ["claude-sonnet-4-6", "gemini-3.1-pro-low"]}}},
    )
    assert len(findings) == 1
    finding = findings[0]
    assert finding.verdict == "🚨 активная модель исчезла"
    assert finding.details["active_removed"] == ["claude-sonnet-4-6"]


def test_model_catalog_baseline_is_silent():
    obs = radar.Observation(
        key="codex_model_catalog",
        name="Codex subscription model catalog",
        kind="inventory",
        current=["gpt-5.5"],
        source="live",
        details={"configured_models": ["gpt-5.5"]},
    )
    assert radar.evaluate([obs], {"observed": {}}) == []


def test_telegram_card_prints_compact_interactive_delivery(monkeypatch, tmp_path, capsys):
    from hermes_cli import update_radar_actions as actions

    fixture = tmp_path / "fixture.json"
    fixture.write_text(json.dumps({"observations": [{
        "key": "cliproxy",
        "name": "CLIProxyAPI",
        "kind": "release",
        "current": "7.2.50",
        "latest": "7.2.51",
        "source": "https://example.test/release",
    }]}))
    state = tmp_path / "state.json"
    monkeypatch.setattr(actions, "ACTION_FILE", tmp_path / "actions.json")
    monkeypatch.setattr(actions, "RADAR_STATE_FILE", state)
    monkeypatch.setattr(sys, "argv", [
        "update_radar.py",
        "--fixture", str(fixture),
        "--state", str(state),
        "--telegram-card",
        "--no-agent",
        "--dry-run",
    ])

    assert radar.main() == 0
    output = capsys.readouterr().out
    assert "🛰 Обновления Hermes" in output
    assert "✅ Рекомендуемые обновления" in output
    assert "UPDATE_RADAR_ACTIONS:" in output
    assert "https://example.test" not in output


def test_model_catalog_report_never_auto_switches():
    finding = radar.Finding(
        key="codex_model_catalog",
        name="Codex subscription model catalog",
        finding_type="model_catalog",
        current=["gpt-5.5"],
        latest=["gpt-5.5", "gpt-5.6"],
        status="ok",
        verdict="🧪 доступна новая модель",
        reason="changed",
        source="live",
        details={
            "added": ["gpt-5.6"],
            "removed": [],
            "active_removed": [],
            "role_suggestions": {"gpt-5.6": ["coding", "planning"]},
            "automatic_routing_change": False,
        },
    )
    report = radar.deterministic_report([finding])
    assert "автоматически не менялась" in report
    assert "gpt-5.6" in report
