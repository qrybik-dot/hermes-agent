import importlib.util
import sys
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[2] / "scripts" / "update_radar.py"
spec = importlib.util.spec_from_file_location("update_radar_current_version", MODULE_PATH)
radar = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = radar
assert spec.loader is not None
spec.loader.exec_module(radar)


def test_uses_release_lock_when_git_times_out(monkeypatch, tmp_path):
    lock_path = tmp_path / "ops/production/components.lock.yaml"
    lock_path.parent.mkdir(parents=True)
    lock_path.write_text(
        "components:\n  hermes:\n    upstream_release: v2026.7.1\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(radar, "REPO", tmp_path)
    monkeypatch.setattr(radar, "run", lambda command, timeout=30: (124, "timeout"))

    assert radar.current_hermes() == "2026.7.1"


def test_never_compares_python_package_version(monkeypatch, tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "hermes-agent"\nversion = "0.18.0"\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(radar, "REPO", tmp_path)
    monkeypatch.setattr(radar, "run", lambda command, timeout=30: (124, "timeout"))

    assert radar.current_hermes() == ""
