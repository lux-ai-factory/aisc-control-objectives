"""The LLM client — a single, provider-agnostic gateway over LiteLLM.

Callers (today: `wizard.profiling.ProfileExtractor`) depend only on a small
duck-typed surface:

    response = client.messages.parse(model=..., system=..., messages=[...],
                                     output_format=<pydantic class>, ...)
    obj = response.parsed_output   # a validated pydantic instance

`LiteLLMClient` provides that surface over `litellm.completion`. The provider is
selected by the model string ("anthropic/claude-opus-4-8", "openai/gpt-4o-mini",
"gemini/…", "azure/…", "bedrock/…", "ollama/…"); LiteLLM reads the matching API
key from the environment (.env).

Translation from the parse-shaped call:
- `system` → a leading `{"role": "system"}` chat message.
- content blocks (`[{"type": "text", "text": ...}]`) are flattened into one
  user message; any `cache_control` on them is dropped.
- `output_format` (a pydantic class) → `response_format` for structured output.
- `output_config={"effort": ...}` → `reasoning_effort`; `temperature` passes through.
- `thinking` and other vendor-specific kwargs are ignored.

`litellm.drop_params` is enabled so params a given provider does not support are
dropped rather than raising. Validation of the answer is lenient about one thing
providers get wrong: a field, or the whole payload, arriving as a JSON string.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any


class _Parsed:
    def __init__(self, parsed_output: Any):
        self.parsed_output = parsed_output


def _flatten_content(messages: list[dict]) -> str:
    """Collapse Anthropic-style content blocks into a single text string."""
    parts: list[str] = []
    for message in messages:
        content = message.get("content")
        if isinstance(content, str):
            parts.append(content)
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    parts.append(block.get("text", ""))
    return "\n\n".join(part for part in parts if part)


def _strip_fences(text: str) -> str:
    """Drop a leading ```json / ``` fence and its closing ``` if present —
    some providers wrap structured output in a Markdown code block."""
    stripped = text.strip()
    if not stripped.startswith("```"):
        return stripped
    body = stripped[3:]
    if "\n" in body:
        first, rest = body.split("\n", 1)
        # drop a bare language tag line ("json") if that's all the first line is
        body = rest if first.strip().isalpha() or first.strip() == "" else body
    if body.rstrip().endswith("```"):
        body = body.rstrip()[:-3]
    return body.strip()


def _try_json(value: Any) -> Any:
    """Decode a value that is itself a JSON-encoded string; otherwise return it
    unchanged. Used to unwrap fields a provider stringified."""
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (ValueError, TypeError):
            return value
    return value


def _coerce(content: Any, output_format: type | None) -> Any:
    if output_format is None:
        return content
    if isinstance(content, output_format):
        return content
    if isinstance(content, (dict, list)):
        obj = content
    else:
        obj = json.loads(_strip_fences(str(content)))
    return _validate_lenient(obj, output_format)


def _validate_lenient(obj: Any, output_format: type) -> Any:
    """Validate `obj` against `output_format`, tolerating providers that return
    JSON-as-string where a structured array/object is expected. Observed with
    Claude via LiteLLM tool-use, which sometimes stringifies a field's value, or
    wraps (and stringifies) the whole payload under the schema's own field name.
    Falls back to the natural validation error if no recovery applies."""
    from pydantic import ValidationError

    try:
        return output_format.model_validate(obj)
    except ValidationError:
        pass
    if isinstance(obj, dict):
        # A field arrived as a JSON string (e.g. {"frames": "[...]"}) — decode
        # each string-encoded value in place and retry.
        decoded = {key: _try_json(value) for key, value in obj.items()}
        try:
            return output_format.model_validate(decoded)
        except ValidationError:
            pass
        # The whole payload was wrapped under a single key and stringified
        # (e.g. {"frames": "{\"frames\": [...]}"}) — validate the inner object.
        if len(obj) == 1:
            inner = _try_json(next(iter(obj.values())))
            if isinstance(inner, (dict, list)):
                try:
                    return output_format.model_validate(inner)
                except ValidationError:
                    pass
    # Nothing recovered — re-run to raise the natural validation error.
    return output_format.model_validate(obj)


class _LiteLLMMessages:
    def __init__(self, completion: Callable[..., Any], extra: dict):
        self._completion = completion
        self._extra = extra

    def parse(
        self,
        *,
        model: str,
        messages: list[dict],
        system: str | None = None,
        max_tokens: int | None = None,
        output_format: type | None = None,
        output_config: dict | None = None,
        temperature: float | None = None,
        **_ignored: Any,  # Anthropic-only kwargs such as `thinking`
    ) -> _Parsed:
        chat: list[dict] = []
        if system:
            chat.append({"role": "system", "content": system})
        chat.append({"role": "user", "content": _flatten_content(messages)})

        kwargs: dict[str, Any] = {"model": model, "messages": chat}
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        if output_format is not None:
            kwargs["response_format"] = output_format
        if output_config and output_config.get("effort"):
            kwargs["reasoning_effort"] = output_config["effort"]
        if temperature is not None:
            kwargs["temperature"] = temperature
        kwargs.update(self._extra)

        response = self._completion(**kwargs)
        content = response.choices[0].message.content
        if content is None:
            # A refusal, a tool call or a truncated answer: say so, rather than
            # surfacing json.loads("None") to whoever reads the error.
            raise ValueError(f"{model} returned no content")
        return _Parsed(_coerce(content, output_format))


class LiteLLMClient:
    """The parse-shaped client backed by LiteLLM. Pass `completion` to inject a
    fake in tests; otherwise `litellm.completion` is used. Extra keyword args
    (e.g. `api_base`, `api_key`, `temperature`) are forwarded on every call."""

    def __init__(self, completion: Callable[..., Any] | None = None, **extra: Any):
        if completion is None:
            import litellm

            # drop provider-unsupported params instead of raising
            litellm.drop_params = True
            completion = litellm.completion
        self.messages = _LiteLLMMessages(completion, extra)
