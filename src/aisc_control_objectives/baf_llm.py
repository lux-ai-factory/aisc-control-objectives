"""One way to build a BAF LLM, for both agentic systems.

This file exists twice, byte for byte: in the card agent (qualification) and in the
risk mapper (control-objectives). A parity test in the top-level repository fails
when the two copies differ, so change both or neither. It imports only the standard
library and BAF, so the same bytes work in both places.

Where a configuration comes from:

- `config_from_env(env)`: the service's environment, as before this module existed
  (BAF_LLM_PROVIDER, BAF_LLM_MODEL, BAF_LLM_BASE_URL and the provider's own key
  variable).
- `resolve(project, system)`: the choice an admin saved for this project on the
  platform, asked from the platform's internal route with PLATFORM_URL and
  PLATFORM_INTERNAL_TOKEN.
- `config_for(project, system, fallback=...)`: the project's choice, else the
  fallback, else the environment. Once a project is known and the platform is
  configured, a platform failure is an error (ResolveError), never a silent switch
  to the environment's model.

`build_llm(config)` then builds the BAF wrapper on a fresh BAF agent, so a key set
for one project is never visible to a build for another, and never touches
os.environ.
"""
from __future__ import annotations

import json
import os
import socket
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from urllib.parse import quote

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

#: provider name -> (BAF wrapper, the property holding its key, the env var it
#: comes from). No two providers read the same variable, and a provider that
#: needs no credential says None twice: a local model has nothing to
#: authenticate with, and a generic endpoint usually does not either.
#: Every BAF wrapper whose SDK these services ship: the ten OpenAI-compatible
#: ones (openai), Claude (anthropic) and a local model (ollama). Replicate and
#: the local-transformers wrapper are left out: their SDKs are not dependencies
#: here, and BAF fails at the first prediction rather than at startup.
PROVIDERS: dict[str, tuple[type, object | None, str | None]] = {
    "anthropic": (LLMAnthropic, nlp.ANTHROPIC_API_KEY, "ANTHROPIC_API_KEY"),
    # Its own neutral variable, not OpenAI's: this provider is for a vLLM, an
    # LM Studio or a gateway, whose token is not an OpenAI key. Optional,
    # because a local endpoint usually has none.
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

#: The agentic systems the platform keeps a choice for.
SYSTEMS = ("card_agent", "risk_mapper")
SYSTEM_LABELS = {"card_agent": "card agent", "risk_mapper": "risk mapper"}

#: How long the platform may take to answer, unless LLM_RESOLVE_TIMEOUT says otherwise.
DEFAULT_RESOLVE_TIMEOUT = 5.0
#: The most of an error answer that is read to find its `detail`.
MAX_ERROR_BYTES = 64 * 1024

Completer = Callable[..., str]


@dataclass(frozen=True, repr=False)
class LlmConfig:
    """Which model to build, and with what. Its repr never shows the key."""

    provider: str
    model: str
    api_key: str | None = None
    base_url: str | None = None

    def __repr__(self) -> str:
        key = "<set>" if self.api_key else "None"
        return (f"LlmConfig(provider={self.provider!r}, model={self.model!r}, "
                f"api_key={key}, base_url={self.base_url!r})")

    __str__ = __repr__


class ResolveError(RuntimeError):
    """The platform could not say which model this project uses. The message names
    the project and the platform's reason, never the token or a key."""


# ── where a configuration comes from ────────────────────────────────────────


def config_from_env(env: Mapping[str, str] | None = None, provider: str | None = None,
                    model: str | None = None) -> LlmConfig:
    """The configuration the environment describes: arguments first, then
    BAF_LLM_PROVIDER / BAF_LLM_MODEL, then mistral / mistral-large-latest."""
    env = os.environ if env is None else env
    provider = (provider or env.get("BAF_LLM_PROVIDER") or DEFAULT_PROVIDER).lower()
    model = model or env.get("BAF_LLM_MODEL") or DEFAULT_MODEL
    env_var = PROVIDERS[provider][2] if provider in PROVIDERS else None
    api_key = (env.get(env_var) or None) if env_var else None
    return LlmConfig(provider, model, api_key=api_key, base_url=env.get("BAF_LLM_BASE_URL") or None)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """A redirect is an error: the service token never travels to another address."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def resolve(project, system: str, env: Mapping[str, str] | None = None,
            timeout: float | None = None) -> LlmConfig | None:
    """The project's choice for this system, from the platform.

    None means "use your own configuration": no project, the platform not
    configured here (PLATFORM_URL or PLATFORM_INTERNAL_TOKEN unset), or no choice
    saved. Any other outcome raises ResolveError."""
    env = os.environ if env is None else env
    if not project:
        return None
    base = (env.get("PLATFORM_URL") or "").rstrip("/")
    token = env.get("PLATFORM_INTERNAL_TOKEN") or ""
    if not base or not token:
        return None
    seconds = timeout if timeout is not None else float(env.get("LLM_RESOLVE_TIMEOUT") or DEFAULT_RESOLVE_TIMEOUT)
    label = SYSTEM_LABELS.get(system, system)
    prefix = f"the platform could not resolve the {label} model for project {project}"
    url = f"{base}/internal/projects/{quote(str(project), safe='')}/llm/{quote(system, safe='')}"
    request = urllib.request.Request(url, headers={"X-AISC-Service-Token": token,
                                                   "Accept": "application/json"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
    try:
        with opener.open(request, timeout=seconds) as response:
            raw = response.read()
    except urllib.error.HTTPError as error:
        raise ResolveError(f"{prefix}: {_detail_of(error)}") from None
    except (TimeoutError, socket.timeout):
        raise ResolveError(f"{prefix}: the platform did not answer within {seconds} s") from None
    except urllib.error.URLError as error:
        if isinstance(error.reason, (TimeoutError, socket.timeout)):
            raise ResolveError(f"{prefix}: the platform did not answer within {seconds} s") from None
        raise ResolveError(f"{prefix}: the platform is not reachable") from None
    except OSError:
        raise ResolveError(f"{prefix}: the platform is not reachable") from None
    return _config_from_answer(raw, prefix)


def _detail_of(error: urllib.error.HTTPError) -> str:
    """The platform's `detail` when its error answer has one, else the status."""
    try:
        body = json.loads(error.read(MAX_ERROR_BYTES))
    except (ValueError, OSError):
        body = None
    finally:
        error.close()
    if isinstance(body, dict) and isinstance(body.get("detail"), str):
        return body["detail"]
    return f"HTTP {error.code}"


def _config_from_answer(raw: bytes, prefix: str) -> LlmConfig | None:
    not_a_choice = ResolveError(f"{prefix}: the platform's answer is not a model choice")
    try:
        body = json.loads(raw)
    except ValueError:
        raise not_a_choice from None
    if not isinstance(body, dict):
        raise not_a_choice
    if body.get("configured") is False:
        return None
    if body.get("configured") is not True:
        raise not_a_choice
    provider, model = body.get("provider"), body.get("model")
    api_key, base_url = body.get("api_key"), body.get("base_url")
    if not (isinstance(provider, str) and provider and isinstance(model, str) and model):
        raise not_a_choice
    if not all(v is None or isinstance(v, str) for v in (api_key, base_url)):
        raise not_a_choice
    return LlmConfig(provider, model, api_key=api_key, base_url=base_url)


def config_for(project, system: str, env: Mapping[str, str] | None = None,
               fallback: LlmConfig | None = None) -> LlmConfig:
    """The project's choice, else the fallback, else the environment.
    A ResolveError is not caught: the caller fails instead of switching model."""
    return resolve(project, system, env=env) or fallback or config_from_env(env)


# ── building the model ──────────────────────────────────────────────────────


def build_llm(config: LlmConfig, agent: Agent | None = None, agent_name: str = "llm"):
    """A BAF LLM for this configuration, through BAF's property store."""
    provider = (config.provider or "").lower()
    if provider not in PROVIDERS:
        raise ValueError(
            f"{provider!r} is not a provider this service configures; "
            f"expected one of {', '.join(sorted(PROVIDERS))}"
        )
    wrapper, key_property, env_var = PROVIDERS[provider]

    # The agent is BAF's configuration scope: properties live on it, and the LLM
    # reads its credential from there. A new one per build, so a key set for one
    # project can never be read by a build for another.
    agent = agent if agent is not None else Agent(agent_name)
    key = config.api_key or None
    base_url = config.base_url or None

    if key and key_property is not None:
        agent.set_property(key_property, key)
    elif provider not in OPTIONAL_KEY:
        raise ValueError(
            f"{provider} needs {env_var}: set it in the environment, "
            "or store a key for the project"
        )

    parameters: dict[str, object] = {}
    if provider == "ollama" and base_url:
        # Ollama's endpoint is a BAF property of its own.
        agent.set_property(nlp.OLLAMA_BASE_URL, base_url)
    elif provider == "compatible":
        if not base_url:
            raise ValueError(
                "compatible needs a base URL (BAF_LLM_BASE_URL, or one stored for the "
                "project): it is the provider for an endpoint you host, so there is no "
                "default to fall back on"
            )
        parameters["base_url"] = base_url
        # The OpenAI SDK refuses to construct without a key even when the
        # endpoint is local and wants none, so an endpoint with no credential
        # gets a placeholder, which such a server ignores.
        parameters["api_key"] = key or "not-needed"

    llm = wrapper(agent=agent, name=config.model, parameters=parameters)
    # BAF initialises its LLMs when the agent runs, and these services drive the
    # flow themselves, so without this the first predict fails on a client that
    # was never built.
    llm.initialize()
    return llm


def completer(llm) -> Completer:
    """Adapt a BAF LLM to the two-prompt call the services make.

    They ask for (system, user); BAF's predict takes the user message and the
    system message separately, which is the same thing said its way.
    """

    def complete(system: str, user: str, temperature: float | None = None) -> str:
        # No temperature unless one is asked for: some models (OpenAI's
        # reasoning ones) refuse every value but their default, and with none
        # given BAF falls back to the LLM's own parameters.
        return llm.predict(
            user,
            parameters=None if temperature is None else {"temperature": temperature},
            system_message=system,
        )

    return complete
