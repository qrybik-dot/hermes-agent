#!/usr/bin/env python3
"""Apply the qualified GPT-5.6 managed-delegation policy atomically."""
from __future__ import annotations

import argparse
import datetime as dt
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any, MutableMapping

from ruamel.yaml import YAML

from ops.model_routing.validate_managed_delegation_policy import validate_config

_MISSING = object()

UPDATES: tuple[tuple[tuple[str, ...], Any, Any], ...] = (
    (("model_roles", "planning", "model"), "gpt-5.5", "gpt-5.6-sol"),
    (("model_roles", "coding", "model"), "gpt-5.5", "gpt-5.6-terra"),
    (("model_roles", "server_debug", "model"), "gpt-5.5", "gpt-5.6-sol"),
    (("role_fallbacks", "agentic", "public", "0", "model"), "gpt-5.5", "gpt-5.6-terra"),
    (("role_fallbacks", "agentic", "sensitive", "0", "model"), "gpt-5.5", "gpt-5.6-terra"),
    (("role_fallbacks", "long_context_extract", "public", "0", "model"), "gpt-5.5", "gpt-5.6-terra"),
    (("role_fallbacks", "long_context_extract", "sensitive", "0", "model"), "gpt-5.5", "gpt-5.6-terra"),
    (("role_fallbacks", "planning", "public", "0", "only_before_tools"), _MISSING, True),
    (("role_fallbacks", "planning", "public", "0", "isolate_context"), _MISSING, True),
    (("role_fallbacks", "planning", "public", "0", "allowed_tools"), _MISSING, []),
    (("role_fallbacks", "planning", "sensitive", "0", "only_before_tools"), _MISSING, True),
    (("role_fallbacks", "planning", "sensitive", "0", "isolate_context"), _MISSING, True),
    (("role_fallbacks", "planning", "sensitive", "0", "allowed_tools"), _MISSING, []),
)


def _resolve_parent(root: Any, path: tuple[str, ...]) -> tuple[Any, str]:
    current = root
    for part in path[:-1]:
        if part.isdigit():
            index = int(part)
            if not isinstance(current, list) or index >= len(current):
                raise KeyError(".".join(path))
            current = current[index]
        else:
            if not isinstance(current, MutableMapping) or part not in current:
                raise KeyError(".".join(path))
            current = current[part]
    return current, path[-1]


def apply_to_mapping(config: MutableMapping[str, Any]) -> list[str]:
    changes: list[str] = []
    for path, expected_old, new_value in UPDATES:
        parent, leaf = _resolve_parent(config, path)
        if not isinstance(parent, MutableMapping):
            raise KeyError(".".join(path))
        current = parent.get(leaf, _MISSING)
        if current == new_value:
            continue
        if current != expected_old:
            current_label = "<missing>" if current is _MISSING else repr(current)
            expected_label = "<missing>" if expected_old is _MISSING else repr(expected_old)
            raise ValueError(
                f"unexpected value at {'.'.join(path)}: {current_label}; "
                f"expected {expected_label} or {new_value!r}"
            )
        parent[leaf] = new_value
        old_label = "<missing>" if expected_old is _MISSING else str(expected_old)
        changes.append(f"{'.'.join(path)}: {old_label} -> {new_value}")
    errors = validate_config(config)
    if errors:
        raise ValueError("policy validation failed: " + "; ".join(errors))
    return changes


def _yaml() -> YAML:
    yaml = YAML(typ="rt")
    yaml.preserve_quotes = True
    yaml.width = 4096
    return yaml


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config", nargs="?", default="/home/hermes/.hermes/config.yaml")
    parser.add_argument("--apply", action="store_true", help="write the validated result atomically")
    args = parser.parse_args()

    config_path = Path(args.config).expanduser().resolve()
    yaml = _yaml()
    with config_path.open("r", encoding="utf-8") as handle:
        config = yaml.load(handle) or {}
    changes = apply_to_mapping(config)

    if not args.apply:
        print("DRY_RUN_PASS")
        for change in changes:
            print(change)
        if not changes:
            print("already compliant")
        return 0

    timestamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_dir = config_path.parent / "backups" / f"managed-delegation-{timestamp}"
    backup_dir.mkdir(parents=True, exist_ok=False)
    backup_path = backup_dir / config_path.name
    shutil.copy2(config_path, backup_path)

    stat = config_path.stat()
    fd, temporary_name = tempfile.mkstemp(prefix=f".{config_path.name}.", dir=config_path.parent)
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            yaml.dump(config, handle)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary_path, stat.st_mode)
        try:
            os.chown(temporary_path, stat.st_uid, stat.st_gid)
        except PermissionError:
            pass
        with temporary_path.open("r", encoding="utf-8") as handle:
            reloaded = _yaml().load(handle) or {}
        errors = validate_config(reloaded)
        if errors:
            raise ValueError("written policy validation failed: " + "; ".join(errors))
        os.replace(temporary_path, config_path)
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise

    print("APPLY_PASS")
    print(f"BACKUP={backup_path}")
    for change in changes:
        print(change)
    if not changes:
        print("already compliant")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
