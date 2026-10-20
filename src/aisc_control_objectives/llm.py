"""The model, configured BAF's way.

One mechanism for the platform. The qualification app's ontology filler reaches
its model through a BAF wrapper, with the credential held in BAF's property
store and read from the environment; this does the same, with the same variable
names, so there is one place a model is named and one place a key lives.

Switching model is two environment variables:

    BAF_LLM_PROVIDER=ollama|mistral|openai|anthropic|compatible|...
    BAF_LLM_MODEL=mistral:latest

`ollama` and `compatible` are the escape hatches: a model on this machine, or
any endpoint speaking OpenAI chat-completions (vLLM, LM Studio, a gateway),
through BAF_LLM_BASE_URL.

BAF's LLMs are text in, text out, so the JSON this service asks for is extracted
and validated here rather than requested as a provider-side schema. That is the
same bargain the filler takes, and it works on providers that have no
structured-output mode at all.

The provider table and the building itself live in `baf_llm.py`, a byte-for-byte
copy of the card agent's module; this one holds the environment-only API and the
JSON helpers. A project's own model and key (the platform's "Models and API keys"
page) reach the risk mapper through `baf_llm.config_for`, wired in `server.py`.
"""

from __future__ import annotations

import json
import os
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from aisc_control_objectives import baf_llm
from aisc_control_objectives.baf_llm import (  # noqa: F401  re-exported for callers of this module
    DEFAULT_MODEL,
    DEFAULT_PROVIDER,
    OPTIONAL_KEY,
    PROVIDERS,
    Completer,
    completer,
)

T = TypeVar("T", bound=BaseModel)


def build_llm(provider: str, model: str, agent=None):
    """A BAF LLM, configured from the environment through BAF's property store."""
    config = baf_llm.config_from_env(os.environ, provider, model)
    return baf_llm.build_llm(config, agent=agent, agent_name="control_objectives_llm")


def json_object(text: str) -> dict:
    """The first JSON object in a model's answer, or an empty one.

    Models fence their JSON, introduce it, apologise after it, and sometimes
    close it twice. So this scans forward from each "{" and keeps the first
    one that decodes as a complete object, ignoring whatever follows it.
    Taking everything up to the last brace would make an answer such as
    `{"ok": true}}` unparseable.
    """
    text = text or ""
    decoder = json.JSONDecoder()
    position = text.find("{")
    while position != -1:
        try:
            parsed, _ = decoder.raw_decode(text, position)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, dict):
            return parsed
        position = text.find("{", position + 1)
    return {}


def _try_json(value: Any) -> Any:
    """Decode a value that is itself a JSON-encoded string; else return it."""
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (ValueError, TypeError):
            return value
    return value


def parse_into(text: str, output_format: type[T]) -> T:
    """Validate a model's answer into `output_format`.

    Tolerates providers that stringify a field's value, or wrap and stringify
    the whole payload under the schema's own field name. Raises with the answer
    quoted when there is no JSON at all, because the alternative is a stack
    trace that says nothing about what the model actually said.
    """
    obj = json_object(text)
    if not obj:
        excerpt = (text or "").strip()[:200] or "(empty answer)"
        raise ValueError(f"no JSON object in the model's answer: {excerpt!r}")

    try:
        return output_format.model_validate(obj)
    except ValidationError:
        pass
    # A field arrived as a JSON string ({"frames": "[...]"}): decode each
    # string-encoded value in place and retry.
    decoded = {key: _try_json(value) for key, value in obj.items()}
    try:
        return output_format.model_validate(decoded)
    except ValidationError:
        pass
    # The whole payload was wrapped under a single key and stringified.
    if len(obj) == 1:
        inner = _try_json(next(iter(obj.values())))
        if isinstance(inner, (dict, list)):
            try:
                return output_format.model_validate(inner)
            except ValidationError:
                pass
    try:
        return output_format.model_validate(obj)
    except ValidationError as exc:
        raise ValueError(
            f"the model's answer does not fit {output_format.__name__}: {exc}"
        ) from exc
