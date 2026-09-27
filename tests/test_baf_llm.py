"""The shared LLM building module, baf_llm.py (pipeline 2026-09-24-llm-keys, 01-specs.md section 3).

Proves S3.2 to S3.5, S3.11 and S5.3 (build, repr). The same cases run in
apps/control-objectives/tests/test_baf_llm.py against B's copy; only MODULE and LEGACY
differ between the two files. The module is imported inside a fixture so a missing
module fails each test rather than stopping collection of the suite.

No network: the platform and the model endpoint are fake servers on 127.0.0.1.
"""
from __future__ import annotations

import dataclasses
import importlib
import json
import logging
import os
import uuid

import pytest

pytest.importorskip("baf")

try:
    from tests.fake_http import Canned, FakeServer, chat_completion, closed_port_url, new_key
except ImportError:  # control-objectives: tests/ is not a package there
    from fake_http import Canned, FakeServer, chat_completion, closed_port_url, new_key

MODULE = "aisc_control_objectives.baf_llm"
LEGACY = "aisc_control_objectives.llm"

PID = str(uuid.uuid4())
TOKEN = "pytest-internal-" + uuid.uuid4().hex


@pytest.fixture
def m():
    return importlib.import_module(MODULE)


@pytest.fixture
def platform():
    server = FakeServer()
    yield server
    server.close()


@pytest.fixture
def model_server():
    server = FakeServer()
    yield server
    server.close()


def platform_env(platform, **extra):
    return {"PLATFORM_URL": platform.url, "PLATFORM_INTERNAL_TOKEN": TOKEN, **extra}


def answer(platform, system, body, status=200, delay=0.0):
    platform.reply(f"/internal/projects/{PID}/llm/{system}", body, status=status, delay=delay)


# ── S3.2 what the module defines ─────────────────────────────────────────────


def test_s3_2_providers_and_optional_key_moved_unchanged(m):
    legacy = importlib.import_module(LEGACY)
    assert set(m.PROVIDERS) == set(legacy.PROVIDERS)
    for name, (wrapper, prop, env_var) in m.PROVIDERS.items():
        old = legacy.PROVIDERS[name]
        assert (wrapper, prop, env_var) == (old[0], old[1], old[2]), name
    assert m.OPTIONAL_KEY == frozenset({"ollama", "compatible"})


def test_s3_2_the_two_systems(m):
    assert m.SYSTEMS == ("card_agent", "risk_mapper")


def test_s3_2_llm_config_is_frozen_with_optional_key_and_base_url(m):
    c = m.LlmConfig("ollama", "gemma3")
    assert (c.provider, c.model, c.api_key, c.base_url) == ("ollama", "gemma3", None, None)
    with pytest.raises(dataclasses.FrozenInstanceError):
        c.model = "other"


def test_s3_2_s5_3_llm_config_never_shows_its_key(m):
    key = new_key()
    c = m.LlmConfig("openai", "gpt-4o-mini", api_key=key, base_url=None)
    for text in (repr(c), str(c), f"{c}", "%r" % (c,)):
        assert key not in text
        assert "api_key=<set>" in text
    assert "api_key=None" in repr(m.LlmConfig("ollama", "gemma3"))


def test_s3_2_config_from_env_defaults_to_mistral(m):
    c = m.config_from_env({})
    assert (c.provider, c.model, c.api_key, c.base_url) == ("mistral", "mistral-large-latest", None, None)


def test_s3_2_config_from_env_reads_bafs_variables_and_the_providers_key(m):
    c = m.config_from_env({"BAF_LLM_PROVIDER": "openai", "BAF_LLM_MODEL": "gpt-x",
                           "OPENAI_API_KEY": "k-openai", "MISTRAL_API_KEY": "k-mistral"})
    assert (c.provider, c.model, c.api_key) == ("openai", "gpt-x", "k-openai")


def test_s3_2_config_from_env_arguments_win(m):
    env = {"BAF_LLM_PROVIDER": "openai", "BAF_LLM_MODEL": "gpt-x", "MISTRAL_API_KEY": "k-mistral"}
    c = m.config_from_env(env, provider="mistral", model="small")
    assert (c.provider, c.model, c.api_key) == ("mistral", "small", "k-mistral")


def test_s3_2_config_from_env_compatible_reads_its_neutral_key_and_base_url(m):
    c = m.config_from_env({"BAF_LLM_PROVIDER": "compatible", "BAF_LLM_MODEL": "local",
                           "BAF_LLM_API_KEY": "gw", "OPENAI_API_KEY": "not-this",
                           "BAF_LLM_BASE_URL": "http://127.0.0.1:9/v1"})
    assert (c.provider, c.api_key, c.base_url) == ("compatible", "gw", "http://127.0.0.1:9/v1")


def test_s3_2_config_from_env_ollama_has_no_key(m):
    c = m.config_from_env({"BAF_LLM_PROVIDER": "ollama", "BAF_LLM_MODEL": "gemma3"})
    assert c.api_key is None


def test_s3_2_resolve_error_is_a_runtime_error(m):
    assert issubclass(m.ResolveError, RuntimeError)


# ── S3.3 build_llm(config) ───────────────────────────────────────────────────


def test_s3_3_an_unknown_provider_lists_the_providers(m):
    with pytest.raises(ValueError, match="mistral"):
        m.build_llm(m.LlmConfig("telepathy", "x"))


def test_s3_3_a_hosted_provider_without_a_key_names_its_variable(m):
    with pytest.raises(ValueError, match="MISTRAL_API_KEY"):
        m.build_llm(m.LlmConfig("mistral", "mistral-large-latest"))


def test_s3_3_the_key_goes_into_bafs_property_store(m):
    from baf import nlp
    from baf.core.agent import Agent

    key = new_key()
    agent = Agent("pytest_baf_llm_agent")
    llm = m.build_llm(m.LlmConfig("mistral", "mistral-large-latest", api_key=key), agent=agent)
    assert agent.get_property(nlp.MISTRAL_API_KEY) == key
    assert llm.client is not None and llm.name == "mistral-large-latest"


def test_s3_3_ollama_base_url_is_bafs_property(m):
    from baf import nlp
    from baf.core.agent import Agent

    agent = Agent("pytest_baf_llm_ollama")
    m.build_llm(m.LlmConfig("ollama", "gemma3", base_url="http://127.0.0.1:11434"), agent=agent)
    assert agent.get_property(nlp.OLLAMA_BASE_URL) == "http://127.0.0.1:11434"


def test_s3_3_compatible_needs_a_base_url(m):
    with pytest.raises(ValueError, match="base"):
        m.build_llm(m.LlmConfig("compatible", "local"))


def test_s3_3_compatible_without_a_key_gets_the_placeholder(m):
    llm = m.build_llm(m.LlmConfig("compatible", "local", base_url="http://127.0.0.1:9/v1"))
    assert llm.client.api_key == "not-needed"
    assert str(llm.client.base_url).rstrip("/") == "http://127.0.0.1:9/v1"


def test_s3_3_each_build_gets_its_own_agent_so_keys_never_cross_projects(m):
    from baf import nlp

    k1, k2 = new_key(), new_key()
    a = m.build_llm(m.LlmConfig("mistral", "mistral-large-latest", api_key=k1))
    b = m.build_llm(m.LlmConfig("mistral", "mistral-large-latest", api_key=k2))
    assert a._nlp_engine is not b._nlp_engine
    assert a._nlp_engine.get_property(nlp.MISTRAL_API_KEY) == k1
    assert b._nlp_engine.get_property(nlp.MISTRAL_API_KEY) == k2
    assert a.client.api_key == k1 and b.client.api_key == k2


def test_s3_3_building_never_writes_the_environment(m, monkeypatch):
    for name in ("MISTRAL_API_KEY", "OPENAI_API_KEY", "BAF_LLM_API_KEY", "BAF_LLM_BASE_URL"):
        monkeypatch.delenv(name, raising=False)
    before = dict(os.environ)
    m.build_llm(m.LlmConfig("mistral", "mistral-large-latest", api_key=new_key()))
    m.build_llm(m.LlmConfig("compatible", "local", api_key=new_key(), base_url="http://127.0.0.1:9/v1"))
    m.build_llm(m.LlmConfig("ollama", "gemma3", base_url="http://127.0.0.1:11434"))
    assert dict(os.environ) == before


def test_s3_3_the_completer_passes_system_and_user(m):
    class Fake:
        calls = []

        def predict(self, message, parameters=None, session=None, system_message=None):
            self.calls.append((message, system_message, parameters))
            return "ok"

    fake = Fake()
    assert m.completer(fake)("sys", "usr", temperature=0.0) == "ok"
    assert fake.calls == [("usr", "sys", {"temperature": 0.0})]


# ── S3.4 resolve ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize("project", [None, ""])
def test_s3_4_no_project_means_the_environment(m, platform, project):
    assert m.resolve(project, "card_agent", env=platform_env(platform)) is None
    assert platform.requests == []


@pytest.mark.parametrize("missing", ["PLATFORM_URL", "PLATFORM_INTERNAL_TOKEN"])
def test_s3_4_an_unconfigured_platform_means_the_environment(m, platform, missing):
    env = platform_env(platform)
    del env[missing]
    assert m.resolve(PID, "card_agent", env=env) is None
    assert platform.requests == []


def test_s3_4_it_asks_the_internal_route_with_the_token(m, platform):
    answer(platform, "risk_mapper", {"configured": False})
    assert m.resolve(PID, "risk_mapper", env=platform_env(platform)) is None
    seen = platform.seen(f"/internal/projects/{PID}/llm/risk_mapper")
    assert len(seen) == 1 and seen[0].method == "GET"
    assert seen[0].header("X-AISC-Service-Token") == TOKEN


def test_s3_4_a_configured_answer_becomes_a_config(m, platform):
    key = new_key()
    answer(platform, "card_agent", {"configured": True, "provider": "compatible", "model": "local",
                                    "base_url": "http://127.0.0.1:9/v1", "api_key": key})
    c = m.resolve(PID, "card_agent", env=platform_env(platform))
    assert c == m.LlmConfig("compatible", "local", api_key=key, base_url="http://127.0.0.1:9/v1")


@pytest.mark.parametrize("status, detail", [
    (404, "no project"), (409, "the stored key for openai cannot be decrypted; enter it again"),
    (401, "a service token is needed"), (503, "PLATFORM_SECRETS_KEY is not set"),
])
def test_s3_4_every_other_answer_fails_closed_naming_project_and_detail(m, platform, status, detail):
    answer(platform, "card_agent", {"detail": detail}, status=status)
    with pytest.raises(m.ResolveError) as err:
        m.resolve(PID, "card_agent", env=platform_env(platform))
    assert PID in str(err.value) and detail in str(err.value)
    assert TOKEN not in str(err.value)


def test_s3_4_bad_json_fails_closed(m, platform):
    platform.reply(f"/internal/projects/{PID}/llm/card_agent", raw=b"<html>oops</html>")
    with pytest.raises(m.ResolveError):
        m.resolve(PID, "card_agent", env=platform_env(platform))


def test_s3_4_a_slow_platform_times_out(m, platform):
    answer(platform, "card_agent", {"configured": False}, delay=3.0)
    with pytest.raises(m.ResolveError) as err:
        m.resolve(PID, "card_agent", env=platform_env(platform, LLM_RESOLVE_TIMEOUT="0.5"))
    assert PID in str(err.value) and TOKEN not in str(err.value)


def test_s3_4_the_timeout_argument_bounds_it_too(m, platform):
    answer(platform, "card_agent", {"configured": False}, delay=3.0)
    with pytest.raises(m.ResolveError):
        m.resolve(PID, "card_agent", env=platform_env(platform), timeout=0.5)


def test_s3_4_a_platform_that_is_down_fails_closed(m):
    env = {"PLATFORM_URL": closed_port_url(), "PLATFORM_INTERNAL_TOKEN": TOKEN}
    with pytest.raises(m.ResolveError) as err:
        m.resolve(PID, "card_agent", env=env)
    assert TOKEN not in str(err.value)


# ── S3.5 config_for ──────────────────────────────────────────────────────────


def test_s3_5_the_platform_choice_wins(m, platform):
    answer(platform, "card_agent", {"configured": True, "provider": "ollama", "model": "gemma3",
                                    "base_url": "http://127.0.0.1:11434", "api_key": None})
    fallback = m.LlmConfig("mistral", "small", api_key="fallback-key")
    c = m.config_for(PID, "card_agent", env=platform_env(platform), fallback=fallback)
    assert c == m.LlmConfig("ollama", "gemma3", base_url="http://127.0.0.1:11434")


def test_s3_5_no_choice_uses_the_fallback_then_the_environment(m, platform):
    answer(platform, "card_agent", {"configured": False})
    fallback = m.LlmConfig("ollama", "fallback-model")
    assert m.config_for(PID, "card_agent", env=platform_env(platform), fallback=fallback) == fallback
    env = platform_env(platform, BAF_LLM_PROVIDER="ollama", BAF_LLM_MODEL="env-model")
    assert m.config_for(PID, "card_agent", env=env) == m.LlmConfig("ollama", "env-model")
    assert m.config_for(None, "card_agent", env={"BAF_LLM_PROVIDER": "ollama", "BAF_LLM_MODEL": "e"}) == \
        m.LlmConfig("ollama", "e")


def test_s3_5_d4_a_failing_platform_is_never_replaced_by_the_environment(m, platform):
    answer(platform, "card_agent", {"detail": "boom"}, status=503)
    with pytest.raises(m.ResolveError):
        m.config_for(PID, "card_agent", env=platform_env(platform, BAF_LLM_PROVIDER="ollama"),
                     fallback=m.LlmConfig("ollama", "local"))


# ── S3.11 the resolved config reaches the model; S5.3 no key in logs ─────────


def _serve_chat(model_server, content="hello"):
    model_server.reply("/v1/chat/completions", lambda seen: Canned(
        200, json.dumps(chat_completion(content, json.loads(seen.body or b"{}").get("model", "x"))).encode()),
        method="POST")


def test_s3_11_a_resolved_compatible_config_sends_its_key_and_model(m, platform, model_server, monkeypatch):
    env_key, key = new_key(), new_key()
    monkeypatch.setenv("BAF_LLM_API_KEY", env_key)
    monkeypatch.setenv("OPENAI_API_KEY", env_key)
    _serve_chat(model_server)
    answer(platform, "card_agent", {"configured": True, "provider": "compatible", "model": "resolved-model",
                                    "base_url": model_server.url + "/v1", "api_key": key})
    config = m.config_for(PID, "card_agent", env=platform_env(platform))
    text = m.completer(m.build_llm(config))("system prompt", "user prompt")
    assert text == "hello"
    (sent,) = model_server.seen("/v1/chat/completions")
    assert sent.header("Authorization") == f"Bearer {key}"
    assert json.loads(sent.body)["model"] == "resolved-model"
    assert env_key not in json.dumps(sent.headers)


def test_s5_3_building_and_predicting_log_no_key(m, model_server, caplog):
    key = new_key()
    _serve_chat(model_server)
    with caplog.at_level(logging.DEBUG):
        for name in ("", "baf", "httpx", "httpcore", "openai", "urllib3"):
            logging.getLogger(name).setLevel(logging.DEBUG)
        config = m.LlmConfig("compatible", "local", api_key=key, base_url=model_server.url + "/v1")
        llm = m.build_llm(config)
        m.completer(llm)("s", "u")
        m.build_llm(m.LlmConfig("mistral", "mistral-large-latest", api_key=key))
        with pytest.raises(ValueError) as err:
            m.build_llm(m.LlmConfig("telepathy", "x", api_key=key))
    assert key not in caplog.text
    assert key not in str(err.value)
