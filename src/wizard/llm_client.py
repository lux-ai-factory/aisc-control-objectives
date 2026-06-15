"""The LLM client — a single, provider-agnostic gateway over LiteLLM.

Every model runs through here, so the agents are not tied to any one vendor.
The agents (`wizard.agents.llm`) depend only on a small duck-typed surface:

    response = client.messages.parse(model=..., system=..., messages=[...],
                                     output_format=<pydantic class>, ...)
    obj = response.parsed_output   # a validated pydantic instance

`LiteLLMClient` provides that surface over `litellm.completion`. The provider is
selected by the model string ("anthropic/claude-opus-4-8", "openai/gpt-4o-mini",
"gemini/…", "azure/…", "bedrock/…", "ollama/…"); LiteLLM reads the matching API
key from the environment (.env).

Translation from the parse-shaped call:
- `system` → a leading `{"role": "system"}` chat message.
- the content blocks (`[{"type": "text", "text": ...}]`, with optional
  `cache_control`) are flattened into one user message. (The codebase's prompt
  blocks still carry `cache_control`; provider-specific prompt caching is not
  re-implemented here, so it degrades to a plain prompt.)
- `output_format` (a pydantic class) → `response_format` for structured output.
- `output_config={"effort": ...}` → `reasoning_effort`.
- `thinking` and any other vendor-specific kwargs are ignored.

`litellm.drop_params` is enabled so params a given provider does not support are
dropped rather than raising.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol


class ParseResult(Protocol):
    """The envelope `messages.parse` returns: the validated structured output
    is on `.parsed_output`."""

    parsed_output: Any


class Messages(Protocol):
    def parse(self, **kwargs: Any) -> ParseResult: ...


class LLMClient(Protocol):
    """The minimal surface the agents need from an LLM client: a `messages`
    namespace whose `parse(...)` returns a validated `.parsed_output`. Both the
    native Anthropic SDK and `LiteLLMClient` satisfy this structurally — the
    agents depend on this Protocol, not on any concrete client."""

    messages: Messages


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


def _coerce(content: Any, output_format: type | None) -> Any:
    if output_format is None:
        return content
    if isinstance(content, output_format):
        return content
    if isinstance(content, (dict, list)):
        return output_format.model_validate(content)
    return output_format.model_validate_json(_strip_fences(str(content)))


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
        **_ignored: Any,
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
        kwargs.update(self._extra)

        response = self._completion(**kwargs)
        content = response.choices[0].message.content
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
