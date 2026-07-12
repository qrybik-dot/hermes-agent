from __future__ import annotations

from copy import deepcopy

import pytest

from ops.model_routing.apply_managed_delegation_policy import UPDATES, apply_to_mapping
from tests.ops.test_managed_delegation_policy import valid_config


def legacy_config():
    config = valid_config()
    config["model_roles"]["planning"]["model"] = "gpt-5.5"
    config["model_roles"]["coding"]["model"] = "gpt-5.5"
    config["model_roles"]["server_debug"]["model"] = "gpt-5.5"
    for role in ("agentic", "long_context_extract"):
        for bucket in ("public", "sensitive"):
            config["role_fallbacks"][role][bucket][0]["model"] = "gpt-5.5"
    for bucket in ("public", "sensitive"):
        planning = config["role_fallbacks"]["planning"][bucket][0]
        planning.pop("only_before_tools")
        planning.pop("isolate_context")
        planning.pop("allowed_tools")
    return config


def test_apply_updates_exact_qualified_paths():
    config = legacy_config()
    changes = apply_to_mapping(config)
    assert len(changes) == len(UPDATES)
    assert config["model_roles"]["planning"]["model"] == "gpt-5.6-sol"
    assert config["model_roles"]["coding"]["model"] == "gpt-5.6-terra"
    assert config["model_roles"]["server_debug"]["model"] == "gpt-5.6-sol"
    for role in ("agentic", "long_context_extract"):
        for bucket in ("public", "sensitive"):
            assert config["role_fallbacks"][role][bucket][0]["model"] == "gpt-5.6-terra"
    for bucket in ("public", "sensitive"):
        planning = config["role_fallbacks"]["planning"][bucket][0]
        assert planning["only_before_tools"] is True
        assert planning["isolate_context"] is True
        assert planning["allowed_tools"] == []


def test_apply_is_idempotent():
    config = valid_config()
    assert apply_to_mapping(config) == []


def test_apply_refuses_unexpected_existing_model():
    config = legacy_config()
    config["model_roles"]["coding"]["model"] = "unknown-model"
    with pytest.raises(ValueError, match="unexpected value"):
        apply_to_mapping(config)


def test_apply_does_not_modify_unrelated_sections_on_failure():
    config = legacy_config()
    config["model_roles"]["coding"]["model"] = "unknown-model"
    before = deepcopy(config["fallback_providers"])
    with pytest.raises(ValueError):
        apply_to_mapping(config)
    assert config["fallback_providers"] == before
