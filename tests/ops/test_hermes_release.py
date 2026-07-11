import json
import os
import subprocess
from pathlib import Path

from ops.deployment.hermes_release import MANIFEST, ReleaseManager


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "test@example.test"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "Test"], check=True)
    (repo / "hermes_cli").mkdir()
    (repo / "hermes_cli" / "main.py").write_text("", encoding="utf-8")
    (repo / "gateway").mkdir()
    (repo / "gateway" / "task_runtime.py").write_text("", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "initial"], check=True)
    return repo


def _manager(tmp_path: Path) -> ReleaseManager:
    repo = _repo(tmp_path)
    root = tmp_path / "runtime"
    python = root / "shared" / "venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    python.chmod(0o755)
    web_dist = root / "shared" / "web_dist"
    web_dist.mkdir(parents=True)
    (web_dist / "index.html").write_text("ok", encoding="utf-8")
    return ReleaseManager(root, repo)


def test_stage_creates_manifest_and_shared_venv_link(tmp_path):
    manager = _manager(tmp_path)
    release = manager.stage("HEAD")
    manifest = json.loads((release / MANIFEST).read_text())
    assert manifest["commit"] == release.name
    assert os.readlink(release / ".venv") == "../../shared/venv"
    assert os.readlink(release / "hermes_cli" / "web_dist") == "../../../shared/web_dist"


def test_activate_and_rollback_swap_atomic_links(tmp_path, monkeypatch):
    manager = _manager(tmp_path)
    first = manager.stage("HEAD")
    monkeypatch.setattr(manager, "validate", lambda release: None)
    manager.activate(first)

    (manager.repo / "next.txt").write_text("next", encoding="utf-8")
    subprocess.run(["git", "-C", str(manager.repo), "add", "."], check=True)
    subprocess.run(["git", "-C", str(manager.repo), "commit", "-qm", "next"], check=True)
    second = manager.stage("HEAD")
    manager.activate(second)

    assert os.readlink(manager.current) == f"releases/{second.name}"
    assert os.readlink(manager.previous) == f"releases/{first.name}"
    manager.rollback()
    assert os.readlink(manager.current) == f"releases/{first.name}"
    assert os.readlink(manager.previous) == f"releases/{second.name}"
