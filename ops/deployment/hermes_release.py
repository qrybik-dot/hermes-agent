#!/usr/bin/env python3
"""Build and atomically activate immutable Hermes runtime releases."""
from __future__ import annotations

import argparse
import datetime as dt
import io
import json
import os
import pwd
import grp
import shutil
import socket
import subprocess
import tarfile
import time
from pathlib import Path


DEFAULT_ROOT = Path("/home/hermes/hermes-runtime")
DEFAULT_REPO = Path("/home/hermes/hermes-prod-rollout-20260710")
SERVICE = "hermes-gateway.service"
COMPANION_SERVICES = ("hermes-dashboard.service",)
MANIFEST = ".hermes-release.json"


def run(*args: str, cwd: Path | None = None, timeout: int = 120) -> str:
    return subprocess.run(
        list(args), cwd=cwd, check=True, capture_output=True, text=True,
        timeout=timeout,
    ).stdout.strip()


def git(repo: Path, *args: str) -> str:
    return run("git", "-c", f"safe.directory={repo}", "-C", str(repo), *args)


def release_name(commit: str) -> str:
    if len(commit) != 40 or any(ch not in "0123456789abcdef" for ch in commit):
        raise ValueError(f"invalid commit: {commit!r}")
    return commit


def _safe_members(archive: tarfile.TarFile, destination: Path):
    base = destination.resolve()
    for member in archive.getmembers():
        target = (destination / member.name).resolve()
        if target != base and base not in target.parents:
            raise ValueError(f"unsafe archive member: {member.name}")
        yield member


class ReleaseManager:
    def __init__(self, root: Path, repo: Path):
        self.root = root
        self.repo = repo
        self.releases = root / "releases"
        self.shared_venv = root / "shared" / "venv"
        self.shared_web_dist = root / "shared" / "web_dist"
        self.current = root / "current"
        self.previous = root / "previous"

    def ensure_layout(self) -> None:
        self.releases.mkdir(parents=True, exist_ok=True)
        (self.root / "shared").mkdir(parents=True, exist_ok=True)
        if not (self.shared_venv / "bin" / "python").is_file():
            raise RuntimeError(f"shared venv is unavailable: {self.shared_venv}")

    def resolve_commit(self, revision: str) -> str:
        commit = git(self.repo, "rev-parse", f"{revision}^{{commit}}")
        return release_name(commit)

    def stage(self, revision: str) -> Path:
        self.ensure_layout()
        commit = self.resolve_commit(revision)
        destination = self.releases / commit
        if destination.is_dir():
            manifest = self.read_manifest(destination)
            if manifest.get("commit") != commit:
                raise RuntimeError(f"release manifest mismatch: {destination}")
            return destination

        staging = self.releases / f".staging-{commit}-{os.getpid()}"
        if staging.exists():
            shutil.rmtree(staging)
        staging.mkdir(mode=0o755)
        try:
            payload = subprocess.run(
                ["git", "-c", f"safe.directory={self.repo}", "-C", str(self.repo),
                 "archive", "--format=tar", commit],
                check=True, capture_output=True, timeout=120,
            ).stdout
            with tarfile.open(fileobj=io.BytesIO(payload), mode="r:") as archive:
                archive.extractall(staging, members=_safe_members(archive, staging))
            os.symlink("../../shared/venv", staging / ".venv")
            if self.shared_web_dist.is_dir() and (staging / "hermes_cli").is_dir():
                os.symlink(
                    "../../../shared/web_dist",
                    staging / "hermes_cli" / "web_dist",
                )
            manifest = {
                "schema": 1,
                "commit": commit,
                "tree": git(self.repo, "rev-parse", f"{commit}^{{tree}}"),
                "created_at": dt.datetime.now(dt.timezone.utc).replace(
                    microsecond=0
                ).isoformat().replace("+00:00", "Z"),
            }
            (staging / MANIFEST).write_text(
                json.dumps(manifest, sort_keys=True) + "\n", encoding="utf-8"
            )
            os.replace(staging, destination)
        except Exception:
            shutil.rmtree(staging, ignore_errors=True)
            raise
        return destination

    @staticmethod
    def read_manifest(release: Path) -> dict:
        return json.loads((release / MANIFEST).read_text(encoding="utf-8"))

    def validate(self, release: Path) -> None:
        manifest = self.read_manifest(release)
        if release.name != manifest.get("commit"):
            raise RuntimeError("release directory and manifest commit differ")
        run(
            str(self.shared_venv / "bin" / "python"), "-c",
            "import hermes_cli.main, gateway.task_runtime",
            cwd=release, timeout=90,
        )

    def _link_target(self, link: Path) -> str | None:
        return os.readlink(link) if link.is_symlink() else None

    def _replace_link(self, link: Path, target: str) -> None:
        temporary = self.root / f".{link.name}.tmp-{os.getpid()}"
        temporary.unlink(missing_ok=True)
        os.symlink(target, temporary)
        os.replace(temporary, link)

    def activate(self, release: Path) -> None:
        self.validate(release)
        target = f"releases/{release.name}"
        old = self._link_target(self.current)
        if old == target:
            return
        if old:
            self._replace_link(self.previous, old)
        self._replace_link(self.current, target)

    def rollback(self) -> None:
        prior = self._link_target(self.previous)
        active = self._link_target(self.current)
        if not prior:
            raise RuntimeError("no previous release is available")
        self._replace_link(self.current, prior)
        if active:
            self._replace_link(self.previous, active)

    def status(self) -> dict:
        def describe(link: Path) -> dict | None:
            target = self._link_target(link)
            if not target:
                return None
            release = self.root / target
            return {"target": target, "manifest": self.read_manifest(release)}
        return {"root": str(self.root), "current": describe(self.current),
                "previous": describe(self.previous)}


def _service_active(service: str) -> bool:
    return subprocess.run(
        ["systemctl", "is-active", "--quiet", service], check=False
    ).returncode == 0


def _service_healthy(service: str) -> bool:
    if not _service_active(service):
        return False
    if service == "hermes-dashboard.service":
        try:
            with socket.create_connection(("127.0.0.1", 9119), timeout=0.5):
                return True
        except OSError:
            return False
    return True


def restart_and_check(timeout: int = 45, stabilization_seconds: int = 5) -> None:
    companions = [service for service in COMPANION_SERVICES if _service_active(service)]
    run("systemctl", "restart", SERVICE, timeout=240)
    for service in companions:
        run("systemctl", "restart", service, timeout=120)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        active = _service_active(SERVICE)
        pid = run("systemctl", "show", SERVICE, "-p", "MainPID", "--value")
        companions_healthy = all(_service_healthy(service) for service in companions)
        if active and pid.isdigit() and int(pid) > 0 and companions_healthy:
            time.sleep(stabilization_seconds)
            if _service_active(SERVICE) and all(
                _service_healthy(service) for service in companions
            ):
                return
        time.sleep(1)
    raise RuntimeError(f"{SERVICE} did not become healthy")


def require_root() -> None:
    if os.geteuid() != 0:
        raise PermissionError("service activation requires root")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--repo", type=Path, default=DEFAULT_REPO)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("stage", "deploy"):
        command = sub.add_parser(name)
        command.add_argument("revision")
    sub.add_parser("rollback")
    sub.add_parser("status")
    args = parser.parse_args()

    manager = ReleaseManager(args.root, args.repo)
    if args.command == "status":
        print(json.dumps(manager.status(), sort_keys=True))
        return 0
    if args.command == "stage":
        release = manager.stage(args.revision)
        manager.validate(release)
        print(release)
        return 0

    require_root()
    if args.command == "rollback":
        manager.rollback()
        restart_and_check()
    else:
        release = manager.stage(args.revision)
        manager.validate(release)
        old = manager._link_target(manager.current)
        manager.activate(release)
        try:
            restart_and_check()
        except Exception:
            if old and manager._link_target(manager.current) != old:
                manager.rollback()
                restart_and_check()
            raise
    print(json.dumps(manager.status(), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
