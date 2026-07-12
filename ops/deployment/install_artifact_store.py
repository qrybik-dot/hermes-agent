#!/usr/bin/env python3
"""Install Artifact Store directories, service units, gateway flag, and backup coverage."""
from __future__ import annotations

import argparse
import datetime as dt
import os
import pwd
import grp
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
ARTIFACT_ROOT = Path("/srv/hermes-artifacts")
GROUP = "hermes-artifacts"
SYSTEMD = Path("/etc/systemd/system")
UNITS = (
    "hermes-artifact-maintenance.service",
    "hermes-artifact-maintenance.timer",
    "hermes-artifact-knowledge-sync.service",
    "hermes-artifact-knowledge-sync.timer",
)
DROPIN = SYSTEMD / "hermes-gateway.service.d" / "30-artifact-store.conf"
BACKUP_SCRIPT = Path("/usr/local/sbin/hermes-restic-backup")
BACKUP_EXCLUDES = Path("/etc/hermes-backup/restic-excludes.txt")
ARTIFACT_RUNTIME = Path("/opt/hermes-artifact-runtime")


def run(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(list(args), check=check, text=True, capture_output=True)


def require_root() -> None:
    if os.geteuid() != 0:
        raise PermissionError("artifact store installation requires root")


def ensure_group() -> None:
    try:
        grp.getgrnam(GROUP)
    except KeyError:
        run("groupadd", "--system", GROUP)
    for user in ("hermes", "hermes-knowledge", "hermes-backup"):
        try:
            pwd.getpwnam(user)
        except KeyError:
            continue
        run("usermod", "-a", "-G", GROUP, user)


def ensure_layout() -> None:
    for path in (
        ARTIFACT_ROOT,
        ARTIFACT_ROOT / "files",
        ARTIFACT_ROOT / "staging",
        ARTIFACT_ROOT / "trash",
        ARTIFACT_ROOT / "state",
        ARTIFACT_ROOT / "backup",
    ):
        path.mkdir(parents=True, exist_ok=True)
        shutil.chown(path, user="hermes", group=GROUP)
        path.chmod(0o2770)


def install_artifact_runtime() -> None:
    commit = run("git", "-C", str(REPO), "rev-parse", "HEAD").stdout.strip()
    if len(commit) != 40:
        raise RuntimeError("cannot resolve artifact runtime commit")
    releases = ARTIFACT_RUNTIME / "releases"
    releases.mkdir(parents=True, exist_ok=True)
    release = releases / commit
    if not release.exists():
        staging = releases / f".staging-{commit}-{os.getpid()}"
        shutil.rmtree(staging, ignore_errors=True)
        (staging / "hermes_cli").mkdir(parents=True)
        (staging / "ops" / "artifacts").mkdir(parents=True)
        for source in (REPO / "hermes_cli" / "__init__.py", REPO / "hermes_cli" / "artifact_store.py"):
            shutil.copy2(source, staging / "hermes_cli" / source.name)
        for source in (REPO / "ops" / "artifacts").glob("*.py"):
            shutil.copy2(source, staging / "ops" / "artifacts" / source.name)
        for directory in (staging, staging / "hermes_cli", staging / "ops", staging / "ops" / "artifacts"):
            directory.chmod(0o755)
        for source in staging.rglob("*.py"):
            source.chmod(0o644)
        os.replace(staging, release)
    current = ARTIFACT_RUNTIME / "current"
    previous = ARTIFACT_RUNTIME / "previous"
    old = os.readlink(current) if current.is_symlink() else None
    target = f"releases/{commit}"
    if old != target:
        if old:
            temporary_previous = ARTIFACT_RUNTIME / f".previous-{os.getpid()}"
            temporary_previous.unlink(missing_ok=True)
            os.symlink(old, temporary_previous)
            os.replace(temporary_previous, previous)
        temporary_current = ARTIFACT_RUNTIME / f".current-{os.getpid()}"
        temporary_current.unlink(missing_ok=True)
        os.symlink(target, temporary_current)
        os.replace(temporary_current, current)
    for path in (ARTIFACT_RUNTIME, releases, release):
        path.chmod(0o755)


def patch_knowledge_promoter() -> None:
    script = REPO / "ops" / "artifacts" / "patch_knowledge_promoter.py"
    run(sys.executable, str(script))


def patch_vps_admin() -> None:
    script = REPO / "ops" / "artifacts" / "patch_vps_admin.py"
    run(sys.executable, str(script))


def install_units() -> None:
    source_dir = REPO / "ops" / "systemd"
    for name in UNITS:
        source = source_dir / name
        if not source.is_file():
            raise FileNotFoundError(source)
        target = SYSTEMD / name
        shutil.copy2(source, target)
        target.chmod(0o644)


def install_gateway_dropin() -> None:
    DROPIN.parent.mkdir(parents=True, exist_ok=True)
    DROPIN.write_text(
        "[Service]\n"
        "Environment=\"HERMES_ARTIFACT_STORE_ENABLED=1\"\n"
        "Environment=\"HERMES_ARTIFACT_ROOT=/srv/hermes-artifacts\"\n",
        encoding="utf-8",
    )
    DROPIN.chmod(0o644)


def backup_file(path: Path) -> Path:
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = path.with_name(path.name + f".artifact-store-{stamp}.bak")
    shutil.copy2(path, backup)
    return backup


def extend_restic_backup() -> None:
    if BACKUP_SCRIPT.is_file():
        content = BACKUP_SCRIPT.read_text(encoding="utf-8")
        updated = content
        snapshot_command = "/usr/bin/python3 /opt/hermes-artifact-runtime/current/ops/artifacts/snapshot_registry.py >/dev/null\n"
        if snapshot_command not in updated:
            marker = "exec /usr/local/bin/restic"
            if marker not in updated:
                raise RuntimeError("unexpected hermes-restic-backup command shape")
            updated = updated.replace(marker, snapshot_command + marker, 1)
        if "/srv/hermes-artifacts" not in updated:
            old = "backup /srv/hermes-memory/vault \\\n"
            new = "backup /srv/hermes-memory/vault /srv/hermes-artifacts \\\n"
            if old not in updated:
                raise RuntimeError("unexpected hermes-restic-backup source list")
            updated = updated.replace(old, new, 1)
        if updated != content:
            backup_file(BACKUP_SCRIPT)
            BACKUP_SCRIPT.write_text(updated, encoding="utf-8")
            BACKUP_SCRIPT.chmod(0o755)
    if BACKUP_EXCLUDES.is_file():
        content = BACKUP_EXCLUDES.read_text(encoding="utf-8")
        additions = [
            "/srv/hermes-artifacts/staging/**",
            "/srv/hermes-artifacts/trash/**",
            "/srv/hermes-artifacts/state/**",
            "/srv/hermes-artifacts/artifact_registry.sqlite3",
            "/srv/hermes-artifacts/artifact_registry.sqlite3-wal",
            "/srv/hermes-artifacts/artifact_registry.sqlite3-shm",
        ]
        missing = [line for line in additions if line not in content]
        if missing:
            backup_file(BACKUP_EXCLUDES)
            with BACKUP_EXCLUDES.open("a", encoding="utf-8") as handle:
                if content and not content.endswith("\n"):
                    handle.write("\n")
                handle.write("\n# Hermes Artifact Store transient state\n")
                handle.write("\n".join(missing) + "\n")


def verify_units() -> None:
    paths = [str(SYSTEMD / name) for name in UNITS]
    completed = run("systemd-analyze", "verify", *paths, check=False)
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr or completed.stdout or "systemd unit verification failed")


def activate(enable: bool) -> None:
    run("systemctl", "daemon-reload")
    if enable:
        run("systemctl", "enable", "--now", "hermes-artifact-maintenance.timer")
        run("systemctl", "enable", "--now", "hermes-artifact-knowledge-sync.timer")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--no-enable", action="store_true")
    args = parser.parse_args()
    require_root()
    ensure_group()
    ensure_layout()
    install_artifact_runtime()
    patch_knowledge_promoter()
    patch_vps_admin()
    install_units()
    install_gateway_dropin()
    extend_restic_backup()
    verify_units()
    activate(enable=not args.no_enable)
    print("artifact store installation complete")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
