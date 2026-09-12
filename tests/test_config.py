"""RunConfig: defaults < TOML file < environment.

The pipeline it once configured is gone; what remains is the model the service
talks to, and the one loading path `server.build_app` uses.
"""

import pytest
from pydantic import ValidationError

from wizard.config import DEFAULT_MODEL, RunConfig


def test_documented_default_model():
    assert RunConfig().model == DEFAULT_MODEL
    assert DEFAULT_MODEL == "anthropic/claude-opus-4-8"
    assert "/" in DEFAULT_MODEL  # LiteLLM routes on the provider prefix


def test_no_file_and_no_env_yields_defaults():
    assert RunConfig.load({}, None).model == DEFAULT_MODEL


def test_a_missing_file_yields_defaults(tmp_path):
    assert RunConfig.load({}, tmp_path / "absent.toml").model == DEFAULT_MODEL


def test_file_beats_defaults(tmp_path):
    path = tmp_path / "wizard.toml"
    path.write_text('model = "openai/gpt-4o-mini"\n')
    assert RunConfig.load({}, path).model == "openai/gpt-4o-mini"


def test_env_beats_file(tmp_path):
    path = tmp_path / "wizard.toml"
    path.write_text('model = "openai/gpt-4o-mini"\n')
    assert RunConfig.load({"WIZARD_MODEL": "ollama/llama3.1"}, path).model == "ollama/llama3.1"


def test_unrelated_wizard_vars_are_ignored():
    assert RunConfig.load({"WIZARD_NONSENSE": "x"}, None).model == DEFAULT_MODEL


def test_a_non_string_model_is_rejected():
    with pytest.raises(ValidationError):
        RunConfig.model_validate({"model": 3})


def test_the_retired_override_surface_is_gone():
    for name in ("from_file", "from_env", "with_overrides"):
        assert not hasattr(RunConfig, name), name
