#!/usr/bin/env python3
"""Validate the production model-routing and managed-delegation safety policy."""
from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any, Mapping

import yaml

EXPECTED_ROLES = {
    "simple": ("custom", "gemini-3.5-flash-low"),
    "parser": ("custom", "gemini-3.5-flash-low"),
    "planning": ("openai-codex", "gpt-5.6-sol"),
    "coding": ("openai-codex", "gpt-5.6-terra"),
    "server_debug": ("openai-codex", "gpt-5.6-sol"),
    "research": ("custom", "gemini-3.1-pro-low"),
    "expert_analysis": ("custom", "gemini-3.1-pro-low"),
}
QUALITY_LOCKED_ROLES = ("planning", "coding", "server_debug")
TERRA_FALLBACK_ROLES = ("agentic", "long_context_extract")
FORBIDDEN_MODEL_ALIASES = {"gpt-5.6", "latest", "auto-latest"}


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _first_entry(value: Any) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    if isinstance(value, list) and value and isinstance(value[0], Mapping):
        return value[0]
    return {}


def _route_label(entry: Mapping[str, Any]) -> str:
    return f"{entry.get('provider')}/{entry.get('model')}"


def _iter_model_values(value: Any):
    if isinstance(value, Mapping):
        model = value.get("model")
        if isinstance(model, str):
            yield model.strip()
        for nested in value.values():
            yield from _iter_model_values(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from _iter_model_values(nested)


def validate_config(config: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []

    model = _mapping(config.get("model"))
    if (model.get("provider"), model.get("default")) != ("custom", "gemini-3.5-flash-low"):
        errors.append("default route must remain custom/gemini-3.5-flash-low")

    roles = _mapping(config.get("model_roles"))
    for role, expected in EXPECTED_ROLES.items():
        entry = _mapping(roles.get(role))
        actual = (entry.get("provider"), entry.get("model"))
        if actual != expected:
            errors.append(f"model_roles.{role} must be {expected[0]}/{expected[1]}, got {actual[0]}/{actual[1]}")

    delegation = _mapping(config.get("delegation"))
    expected_delegation = {
        "max_concurrent_children": 1,
        "max_spawn_depth": 1,
        "orchestrator_enabled": False,
        "subagent_auto_approve": False,
    }
    for key, expected in expected_delegation.items():
        if delegation.get(key) != expected:
            errors.append(f"delegation.{key} must be {expected!r}, got {delegation.get(key)!r}")

    experiments = _mapping(config.get("routing_experiments"))
    if experiments.get("agentic_enabled") is not False:
        errors.append("routing_experiments.agentic_enabled must stay false during controlled rollout")

    routing = _mapping(config.get("routing_policy"))
    if routing.get("max_model_fallbacks") != 1:
        errors.append("routing_policy.max_model_fallbacks must be 1")

    role_fallbacks = _mapping(config.get("role_fallbacks"))
    for role in QUALITY_LOCKED_ROLES:
        role_cfg = _mapping(role_fallbacks.get(role))
        for bucket in ("public", "sensitive"):
            entry = _first_entry(role_cfg.get(bucket) or role_cfg.get("default"))
            if (entry.get("provider"), entry.get("model")) != ("custom", "gemini-3.1-pro-low"):
                errors.append(
                    f"role_fallbacks.{role}.{bucket} must be custom/gemini-3.1-pro-low, got {_route_label(entry)}"
                )
            if entry.get("only_before_tools") is not True:
                errors.append(f"role_fallbacks.{role}.{bucket}.only_before_tools must be true")
            if entry.get("isolate_context") is not True:
                errors.append(f"role_fallbacks.{role}.{bucket}.isolate_context must be true")
            if entry.get("allowed_tools") != []:
                errors.append(f"role_fallbacks.{role}.{bucket}.allowed_tools must be []")

    for role in TERRA_FALLBACK_ROLES:
        role_cfg = _mapping(role_fallbacks.get(role))
        for bucket in ("public", "sensitive"):
            entry = _first_entry(role_cfg.get(bucket) or role_cfg.get("default"))
            if (entry.get("provider"), entry.get("model")) != ("openai-codex", "gpt-5.6-terra"):
                errors.append(
                    f"role_fallbacks.{role}.{bucket} must be openai-codex/gpt-5.6-terra, got {_route_label(entry)}"
                )
            if entry.get("only_before_tools") is not True:
                errors.append(f"role_fallbacks.{role}.{bucket}.only_before_tools must be true")
            if entry.get("isolate_context") is not True:
                errors.append(f"role_fallbacks.{role}.{bucket}.isolate_context must be true")
            if entry.get("allowed_tools") != []:
                errors.append(f"role_fallbacks.{role}.{bucket}.allowed_tools must be []")

    active_sections = {
        "model": model,
        "model_roles": roles,
        "role_fallbacks": role_fallbacks,
        "codex_quality_fallback": config.get("codex_quality_fallback"),
        "fallback_providers": config.get("fallback_providers"),
        "delegation": delegation,
    }
    for model_name in _iter_model_values(active_sections):
        lowered = model_name.lower()
        if lowered in FORBIDDEN_MODEL_ALIASES or lowered.endswith(":latest"):
            errors.append(f"unpinned model alias is forbidden: {model_name}")
        if lowered == "gpt-5.5":
            errors.append("gpt-5.5 must not be active; keep it only in rollback documentation/backups")

    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config", nargs="?", default="/home/hermes/.hermes/config.yaml")
    args = parser.parse_args()
    config_path = Path(args.config)
    payload = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    errors = validate_config(payload)
    if errors:
        print("FAIL")
        for error in errors:
            print(f"- {error}")
        return 1
    print("PASS")
    print("managed delegation: sequential, flat, manual approval")
    print("roles: coding=Terra; planning/server_debug=Sol; default=Gemini Flash")
    print("fallbacks: one bounded switch; quality roles isolated before tools")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
