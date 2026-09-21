"""RunConfig: defaults < TOML file < environment.

The pipeline it once configured is gone; what remains is the model the service
talks to, and the one loading path `server.build_app` uses.
"""

import pytest
from pydantic import ValidationError

from aisc_control_objectives.config import RunConfig
from aisc_control_objectives.llm import DEFAULT_MODEL, DEFAULT_PROVIDER, PROVIDERS


def test_the_defaults_are_baf_s_own():
    """One naming of the model across the platform: the filler's defaults."""
    config = RunConfig()
    assert (config.provider, config.model) == (DEFAULT_PROVIDER, DEFAULT_MODEL)
    assert config.provider in PROVIDERS


def test_no_file_and_no_env_yields_defaults():
    assert RunConfig.load({}, None).model == DEFAULT_MODEL


def test_a_missing_file_yields_defaults(tmp_path):
    assert RunConfig.load({}, tmp_path / "absent.toml").model == DEFAULT_MODEL


def test_file_beats_defaults(tmp_path):
    path = tmp_path / "control-objectives.toml"
    path.write_text('model = "gpt-4o-mini"\n')
    assert RunConfig.load({}, path).model == "gpt-4o-mini"


def test_env_beats_file(tmp_path):
    path = tmp_path / "control-objectives.toml"
    path.write_text('provider = "openai"\nmodel = "gpt-4o-mini"\n')
    config = RunConfig.load({"BAF_LLM_PROVIDER": "ollama", "BAF_LLM_MODEL": "mistral:latest"}, path)
    assert (config.provider, config.model) == ("ollama", "mistral:latest")


def test_a_blank_baf_variable_is_not_an_override(tmp_path):
    """Templated env files leave them empty; that must not blank the model."""
    config = RunConfig.load({"BAF_LLM_PROVIDER": "", "BAF_LLM_MODEL": ""}, None)
    assert (config.provider, config.model) == (DEFAULT_PROVIDER, DEFAULT_MODEL)


def test_unrelated_vars_are_ignored():
    assert RunConfig.load({"CONTROL_OBJECTIVES_NONSENSE": "x"}, None).model == DEFAULT_MODEL


def test_a_non_string_model_is_rejected():
    with pytest.raises(ValidationError):
        RunConfig.model_validate({"model": 3})


def test_the_retired_override_surface_is_gone():
    for name in ("from_file", "from_env", "with_overrides"):
        assert not hasattr(RunConfig, name), name
