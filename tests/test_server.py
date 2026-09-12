"""Composition root (server) — the LLM client is always the LiteLLM gateway.

No network: constructing the client does not call any provider, and no key is
required at construction (LiteLLM reads the model's provider key at call time).
"""

from wizard import server
from wizard.llm_client import LiteLLMClient


def test_build_client_is_litellm(monkeypatch):
    # no provider key set — construction must still succeed
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    client = server._build_client()
    assert isinstance(client, LiteLLMClient)
    assert hasattr(client.messages, "parse")


def test_a_blank_objectives_file_variable_means_the_bundled_csv():
    """Templated env files leave `WIZARD_OBJECTIVES_FILE=` empty; that is
    'no override', not 'the current directory'."""
    from wizard.control_objectives import default_csv_path

    path, name = server.objectives_source({"WIZARD_OBJECTIVES_FILE": ""})
    assert path is None
    assert name == default_csv_path().name
    path, name = server.objectives_source({"WIZARD_OBJECTIVES_FILE": "/tmp/x/newer.csv"})
    assert str(path) == "/tmp/x/newer.csv"
    assert name == "newer.csv"
