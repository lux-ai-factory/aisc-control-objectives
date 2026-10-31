"""The model, configured BAF's way.

One mechanism for the platform: the qualification filler reaches its model
through a BAF wrapper and a BAF property store, and so does this. There is no
second LLM stack here, which is what these tests hold.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import BaseModel, ValidationError

from aisc_control_objectives.llm import (
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


class TestBuildLlm:
    def test_a_local_model_needs_no_key(self, monkeypatch):
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        llm = build_llm("ollama", "mistral:latest")
        assert llm is not None


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
    def test_no_temperature_is_sent_unless_asked_for(self):
        """The shared completer (the same file as qualification's) sends none by default: some models
        (OpenAI's reasoning ones) refuse every value but their own. The risk mapper asks for 0.0 itself
        (tests/test_server.py)."""
        llm = FakeLLM()
        completer(llm)("s", "u")
        assert llm.calls[0]["parameters"] is None


class TestJsonObject:
    """Models fence their JSON, introduce it, and apologise after it."""

    def test_a_bare_object(self):
        assert json_object('{"a": 1}') == {"a": 1}

    def test_a_fenced_object(self):
        assert json_object('```json\n{"a": 1}\n```') == {"a": 1}

    def test_an_object_with_prose_around_it(self):
        assert json_object('Here you go:\n{"a": 1}\nHope that helps!') == {"a": 1}

    def test_a_trailing_brace_does_not_ruin_it(self):
        """Observed from a local model on its first answer: one brace too many.
        Scanning to the LAST brace makes that unparseable and fails the run."""
        assert json_object('{"ok": true}}') == {"ok": True}

    def test_trailing_prose_after_the_object(self):
        assert json_object('{"ok": true}\n\nLet me know if you need more!') == {"ok": True}

    def test_a_nested_object_is_not_truncated(self):
        answer = '{"a": {"b": 1}, "c": {"d": 2}}'
        assert json_object(answer) == {"a": {"b": 1}, "c": {"d": 2}}

    def test_a_nested_object_inside_a_fence_is_not_truncated(self):
        answer = '```json\n{"a": {"b": 1}, "c": {"d": 2}}\n```'
        assert json_object(answer) == {"a": {"b": 1}, "c": {"d": 2}}

    def test_a_brace_in_prose_before_the_object_is_skipped(self):
        assert json_object('I thought about {this} and decided:\n{"ok": true}') == {"ok": True}

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

    def test_an_answer_that_does_not_fit_is_read_each_way_once(self):
        """As given, then with its fields decoded: two readings, no third to fetch the error."""
        calls = []

        class Counted(Framing):
            @classmethod
            def model_validate(cls, obj, *args, **kwargs):
                calls.append(obj)
                return super().model_validate(obj, *args, **kwargs)

        with pytest.raises(ValueError, match="Counted") as raised:
            parse_into('{"frames": [{"wrong": 1}], "note": "x"}', Counted)
        assert len(calls) == 2
        assert isinstance(raised.value.__cause__, ValidationError)
