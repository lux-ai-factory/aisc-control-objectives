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

BAF's LLMs are text in, text out, so the JSON the wizard asks for is extracted
and validated here rather than requested as a provider-side schema. That is the
same bargain the filler takes, and it works on providers that have no
structured-output mode at all.
"""

from __future__ import annotations

import json
import os
from typing import Any, Callable, TypeVar

from baf import nlp
from baf.core.agent import Agent
from baf.nlp.llm.llm_anthropic import LLMAnthropic
from baf.nlp.llm.llm_deepseek import LLMDeepSeek
from baf.nlp.llm.llm_google import LLMGoogle
from baf.nlp.llm.llm_groq import LLMGroq
from baf.nlp.llm.llm_meta import LLMMeta
from baf.nlp.llm.llm_mistral import LLMMistral
from baf.nlp.llm.llm_ollama import LLMOllama
from baf.nlp.llm.llm_openai_api import LLMOpenAI
from baf.nlp.llm.llm_openai_compatible import LLMOpenAICompatible
from baf.nlp.llm.llm_openrouter import LLMOpenRouter
from baf.nlp.llm.llm_qwen import LLMQwen
from baf.nlp.llm.llm_together import LLMTogether
from baf.nlp.llm.llm_xai import LLMxAI
from pydantic import BaseModel, ValidationError

#: provider name -> (BAF wrapper, the property holding its key, the env var it
#: comes from). No two providers read the same variable, and a provider that
#: needs no credential says None twice. The table mirrors the qualification
#: filler's, so a deployment configures both services the same way.
PROVIDERS: dict[str, tuple[type, object | None, str | None]] = {
    "anthropic": (LLMAnthropic, nlp.ANTHROPIC_API_KEY, "ANTHROPIC_API_KEY"),
    # Its own neutral variable, not OpenAI's: this provider is for a vLLM, an
    # LM Studio or a gateway, whose token is not an OpenAI key.
    "compatible": (LLMOpenAICompatible, nlp.OPENAI_API_KEY, "BAF_LLM_API_KEY"),
    "deepseek": (LLMDeepSeek, nlp.DEEPSEEK_API_KEY, "DEEPSEEK_API_KEY"),
    "google": (LLMGoogle, nlp.GOOGLE_API_KEY, "GOOGLE_API_KEY"),
    "groq": (LLMGroq, nlp.GROQ_API_KEY, "GROQ_API_KEY"),
    "meta": (LLMMeta, nlp.META_API_KEY, "META_API_KEY"),
    "mistral": (LLMMistral, nlp.MISTRAL_API_KEY, "MISTRAL_API_KEY"),
    "ollama": (LLMOllama, None, None),
    "openai": (LLMOpenAI, nlp.OPENAI_API_KEY, "OPENAI_API_KEY"),
    "openrouter": (LLMOpenRouter, nlp.OPENROUTER_API_KEY, "OPENROUTER_API_KEY"),
    "qwen": (LLMQwen, nlp.QWEN_API_KEY, "QWEN_API_KEY"),
    "together": (LLMTogether, nlp.TOGETHER_API_KEY, "TOGETHER_API_KEY"),
    "xai": (LLMxAI, nlp.XAI_API_KEY, "XAI_API_KEY"),
}

#: Providers that work without a credential: a model on this machine, and an
#: endpoint you host, which may or may not ask for a token.
OPTIONAL_KEY = frozenset({"ollama", "compatible"})

DEFAULT_PROVIDER = "mistral"
DEFAULT_MODEL = "mistral-large-latest"

#: What the loop calls: (system, user) -> the model's answer as text.
Completer = Callable[..., str]

T = TypeVar("T", bound=BaseModel)


def build_llm(provider: str, model: str, agent: Agent | None = None):
    """A BAF LLM, configured from the environment through BAF's property store."""
    provider = (provider or DEFAULT_PROVIDER).lower()
    if provider not in PROVIDERS:
        raise ValueError(
            f"{provider!r} is not a provider this service configures; "
            f"expected one of {', '.join(sorted(PROVIDERS))}"
        )
    wrapper, key_property, env_var = PROVIDERS[provider]

    # The agent is BAF's configuration scope: properties live on it, and the
    # LLM reads its credential from there rather than from us.
    agent = agent or Agent("wizard_llm")
    base_url = os.environ.get("BAF_LLM_BASE_URL")
    key = os.environ.get(env_var) if env_var else None

    if key:
        agent.set_property(key_property, key)
    elif provider not in OPTIONAL_KEY:
        raise ValueError(
            f"{provider} needs {env_var} in the environment; set it, or use "
            "BAF_LLM_PROVIDER=ollama for a model on this machine"
        )

    parameters: dict[str, Any] = {}
    if provider == "ollama" and base_url:
        # Ollama's endpoint is a BAF property of its own.
        agent.set_property(nlp.OLLAMA_BASE_URL, base_url)
    elif provider == "compatible":
        if not base_url:
            raise ValueError(
                "compatible needs BAF_LLM_BASE_URL: it is the provider for an "
                "endpoint you host, so there is no default to fall back on"
            )
        parameters["base_url"] = base_url
        # The OpenAI SDK refuses to construct without a key even when the
        # endpoint is local and wants none; vLLM and LM Studio ignore it.
        parameters["api_key"] = key or "not-needed"

    llm = wrapper(agent=agent, name=model, parameters=parameters)
    # BAF initialises its LLMs when the agent runs, and this service drives the
    # flow itself, so nobody else will: without this the first predict fails on
    # a client that was never built.
    llm.initialize()
    return llm


def completer(llm) -> Completer:
    """Adapt a BAF LLM to the two-prompt call the wizard makes.

    The wizard asks for (system, user); BAF's predict takes the user message
    and the system message separately, which is the same thing said its way.
    """

    def complete(system: str, user: str, temperature: float = 0) -> str:
        return llm.predict(
            user, parameters={"temperature": temperature}, system_message=system
        )

    return complete


def json_object(text: str) -> dict:
    """The first JSON object in a model's answer, or an empty one.

    Models fence their JSON, introduce it, apologise after it, and sometimes
    close it twice. So this scans forward from each "{" and keeps the first
    one that decodes as a complete object, ignoring whatever follows it: a
    local model's very first answer here was `{"ok": true}}`, and taking
    everything up to the LAST brace would have made that unparseable and
    failed the run.
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
    # A field arrived as a JSON string ({"frames": "[...]"}) — decode each
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
