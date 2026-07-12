from __future__ import annotations

from copy import deepcopy

from ops.model_routing.validate_managed_delegation_policy import validate_config


def valid_config():
    safe_reasons = ["rate_limit", "auth", "model_not_found", "server_error", "timeout"]

    def quality_entry():
        return {
            "provider": "custom",
            "model": "gemini-3.1-pro-low",
            "reasons": safe_reasons,
            "only_before_tools": True,
            "isolate_context": True,
            "allowed_tools": [],
        }

    def terra_entry():
        return {
            "provider": "openai-codex",
            "model": "gpt-5.6-terra",
            "reasons": safe_reasons,
            "only_before_tools": True,
            "isolate_context": True,
            "allowed_tools": [],
        }

    return {
        "model": {"provider": "custom", "default": "gemini-3.5-flash-low"},
        "model_roles": {
            "simple": {"provider": "custom", "model": "gemini-3.5-flash-low"},
            "parser": {"provider": "custom", "model": "gemini-3.5-flash-low"},
            "planning": {"provider": "openai-codex", "model": "gpt-5.6-sol"},
            "coding": {"provider": "openai-codex", "model": "gpt-5.6-terra"},
            "server_debug": {"provider": "openai-codex", "model": "gpt-5.6-sol"},
            "research": {"provider": "custom", "model": "gemini-3.1-pro-low"},
            "expert_analysis": {"provider": "custom", "model": "gemini-3.1-pro-low"},
        },
        "delegation": {
            "max_concurrent_children": 1,
            "max_spawn_depth": 1,
            "orchestrator_enabled": False,
            "subagent_auto_approve": False,
        },
        "routing_experiments": {"agentic_enabled": False},
        "routing_policy": {"max_model_fallbacks": 1},
        "role_fallbacks": {
            **{
                role: {"public": [quality_entry()], "sensitive": [quality_entry()]}
                for role in ("planning", "coding", "server_debug")
            },
            **{
                role: {"public": [terra_entry()], "sensitive": [terra_entry()]}
                for role in ("agentic", "long_context_extract")
            },
        },
        "codex_quality_fallback": {
            "enabled": True,
            "provider": "custom",
            "model": "gemini-3.1-pro-low",
        },
        "fallback_providers": [
            {"provider": "nous", "model": "stepfun/step-3.7-flash:free"},
        ],
    }


def test_valid_policy_passes():
    assert validate_config(valid_config()) == []


def test_nested_or_parallel_delegation_is_rejected():
    config = valid_config()
    config["delegation"]["max_concurrent_children"] = 2
    config["delegation"]["max_spawn_depth"] = 2
    errors = validate_config(config)
    assert any("max_concurrent_children" in error for error in errors)
    assert any("max_spawn_depth" in error for error in errors)


def test_unpinned_alias_and_active_gpt55_are_rejected():
    config = valid_config()
    config["model_roles"]["coding"]["model"] = "gpt-5.6"
    config["role_fallbacks"]["agentic"]["public"][0]["model"] = "gpt-5.5"
    errors = validate_config(config)
    assert any("unpinned model alias" in error for error in errors)
    assert any("gpt-5.5 must not be active" in error for error in errors)


def test_quality_fallback_must_be_isolated_before_tools():
    config = deepcopy(valid_config())
    entry = config["role_fallbacks"]["coding"]["sensitive"][0]
    entry["only_before_tools"] = False
    entry["allowed_tools"] = ["terminal"]
    errors = validate_config(config)
    assert any("only_before_tools" in error for error in errors)
    assert any("allowed_tools" in error for error in errors)
