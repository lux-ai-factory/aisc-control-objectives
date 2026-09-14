"""The model, configured BAF's way.

One mechanism for the platform: the qualification filler reaches its model
through a BAF wrapper and a BAF property store, and so does this. There is no
second LLM stack here, which is what these tests hold.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import BaseModel

from wizard.llm import (
    DEFAULT_MODEL,
    DEFAULT_PROVIDER,
    OPTIONAL_KEY,
    PROVIDERS,
    build_llm,
    completer,
    json_object,
    parse_into,
)

SRC = Path(__file__).resolve().parents[1] / "src"


class Frame(BaseModel):
    dimension_slug: str


class Framing(BaseModel):
    frames: list[Frame] = []


def test_no_second_llm_stack_in_the_source():
    """The overlap is the thing being removed: BAF is the only way to a model."""
    offenders = [
        path.relative_to(SRC)
        for path in SRC.rglob("*.py")
        if "litellm" in path.read_text().lower()
    ]
    assert offenders == []
    assert "litellm" not in (SRC.parent / "pyproject.toml").read_text().lower()


class TestProviders:
    def test_every_provider_maps_to_a_baf_wrapper(self):
        from baf.nlp.llm.llm import LLM

        assert PROVIDERS
        for name, (wrapper, _, _) in PROVIDERS.items():
            assert issubclass(wrapper, LLM), name

    def test_no_two_providers_read_the_same_variable(self):
        """A shared variable makes one provider silently answer for another."""
        variables = [var for _, _, var in PROVIDERS.values() if var]
        assert len(variables) == len(set(variables))

    def test_the_keyless_providers_are_the_ones_you_host(self):
        assert OPTIONAL_KEY <= set(PROVIDERS)
        assert OPTIONAL_KEY == {"ollama", "compatible"}

    def test_the_default_is_a_provider_we_configure(self):
        assert DEFAULT_PROVIDER in PROVIDERS
        assert DEFAULT_MODEL


class TestBuildLlm:
    def test_an_unknown_provider_names_the_ones_that_exist(self):
        with pytest.raises(ValueError, match="not a provider"):
            build_llm("altavista", "some-model")

    def test_a_provider_without_its_key_says_which_variable(self, monkeypatch):
        monkeypatch.delenv("MISTRAL_API_KEY", raising=False)
        with pytest.raises(ValueError, match="MISTRAL_API_KEY"):
            build_llm("mistral", "mistral-large-latest")

    def test_a_local_model_needs_no_key(self, monkeypatch):
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        llm = build_llm("ollama", "mistral:latest")
        assert llm is not None

    def test_a_self_hosted_endpoint_needs_its_base_url(self, monkeypatch):
        monkeypatch.delenv("BAF_LLM_BASE_URL", raising=False)
        with pytest.raises(ValueError, match="BAF_LLM_BASE_URL"):
            build_llm("compatible", "whatever")


class FakeLLM:
    """Stands in for a BAF LLM: records the call, returns a queued answer."""

    def __init__(self, answer: str = "{}"):
        self.answer = answer
        self.calls: list[dict] = []

    def predict(self, message, parameters=None, session=None, system_message=None):
        self.calls.append(
            {"message": message, "parameters": parameters, "system_message": system_message}
        )
        return self.answer


class TestCompleter:
    def test_it_makes_the_two_prompt_call_baf_takes(self):
        llm = FakeLLM("hello")
        complete = completer(llm)
        assert complete("the rules", "the card") == "hello"
        call = llm.calls[0]
        assert call["system_message"] == "the rules"
        assert call["message"] == "the card"

    def test_a_draft_is_reproducible_by_default(self):
        llm = FakeLLM()
        completer(llm)("s", "u")
        assert llm.calls[0]["parameters"]["temperature"] == 0

    def test_temperature_can_be_raised(self):
        llm = FakeLLM()
        completer(llm)("s", "u", temperature=0.7)
        assert llm.calls[0]["parameters"]["temperature"] == 0.7


class TestJsonObject:
    """Models fence their JSON, introduce it, and apologise after it."""

    def test_a_bare_object(self):
        assert json_object('{"a": 1}') == {"a": 1}

    def test_a_fenced_object(self):
        assert json_object('```json\n{"a": 1}\n```') == {"a": 1}

    def test_an_object_with_prose_around_it(self):
        assert json_object('Here you go:\n{"a": 1}\nHope that helps!') == {"a": 1}

    def test_a_refusal_is_not_an_object(self):
        assert json_object("I cannot help with that.") == {}

    def test_nonsense_is_not_an_object(self):
        assert json_object("{{{{") == {}

    def test_an_empty_answer_is_not_an_object(self):
        assert json_object("") == {}


class TestParseInto:
    def test_it_validates_into_the_model(self):
        framing = parse_into('{"frames": [{"dimension_slug": "fairness"}]}', Framing)
        assert framing.frames[0].dimension_slug == "fairness"

    def test_a_field_arriving_as_a_json_string_is_recovered(self):
        """Observed with several providers: a field's value is stringified."""
        text = json.dumps({"frames": json.dumps([{"dimension_slug": "privacy"}])})
        assert parse_into(text, Framing).frames[0].dimension_slug == "privacy"

    def test_the_whole_payload_wrapped_and_stringified_is_recovered(self):
        inner = json.dumps({"frames": [{"dimension_slug": "safety"}]})
        assert parse_into(json.dumps({"frames": inner}), Framing).frames[0].dimension_slug == "safety"

    def test_an_answer_with_no_json_says_so_and_quotes_the_answer(self):
        with pytest.raises(ValueError, match="no JSON object"):
            parse_into("I cannot help with that.", Framing)

    def test_an_answer_that_does_not_fit_the_shape_says_so(self):
        with pytest.raises(ValueError, match="Framing"):
            parse_into('{"frames": [{"wrong": 1}]}', Framing)
