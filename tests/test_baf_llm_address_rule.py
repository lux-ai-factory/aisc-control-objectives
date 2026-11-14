"""A model address that came from the platform for a project is checked against the project's network rule
before the model is built: an internal address the rule does not allow is refused. A configuration from the
environment is the operator's choice and carries no rule.

The same cases run against the other app's copy of baf_llm.py; only MODULE differs between the two files."""
from __future__ import annotations

import importlib
import json

import pytest

pytest.importorskip("baf")

MODULE = "aisc_control_objectives.baf_llm"


@pytest.fixture
def m():
    return importlib.import_module(MODULE)


def _answer(base_url: str, **rule) -> bytes:
    return json.dumps({"configured": True, "provider": "compatible", "model": "m", "base_url": base_url,
                       "api_key": None, **rule}).encode()


def test_a_project_base_url_the_platform_rule_refuses_is_never_built(m):
    config = m._config_from_answer(_answer("http://10.0.0.5/v1", allowed_hosts=[], denied_addresses=[],
                                           floor_hosts=[]), "prefix")
    with pytest.raises(m.ResolveError, match="internal address"):
        m.build_llm(config)


def test_a_project_base_url_on_a_denied_address_is_never_built(m):
    config = m._config_from_answer(_answer("http://169.254.169.254/v1", allowed_hosts=["169.254.169.254"],
                                           denied_addresses=["169.254.169.254"], floor_hosts=[]), "prefix")
    with pytest.raises(m.ResolveError, match="never"):
        m.build_llm(config)


def test_the_rule_from_the_platform_is_kept_on_the_configuration(m):
    config = m._config_from_answer(_answer("https://models.example/v1", allowed_hosts=[], denied_addresses=[],
                                           floor_hosts=[]), "prefix")
    assert getattr(config, "rule", None) == {"allowed_hosts": [], "denied_addresses": [], "floor_hosts": []}


def test_an_environment_configuration_is_not_checked(m):
    config = m.config_from_env({"BAF_LLM_PROVIDER": "compatible", "BAF_LLM_BASE_URL": "http://ollama:11434/v1"})
    assert hasattr(config, "rule"), "a configuration does not carry a rule at all"
    assert config.rule is None
