"""Composition root (server) — the LLM client is always the LiteLLM gateway.

No network: constructing the client does not call any provider, and no key is
required at construction (LiteLLM reads the model's provider key at call time).
"""

import os

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


class TestDotenv:
    """The hand-rolled parser stays (ten lines against a new direct
    dependency), but what it accepts is pinned: a key that looks set and is
    not surfaces only as an opaque provider error on a card page."""

    def _load(self, tmp_path, monkeypatch, text):
        monkeypatch.setattr(server, "_repo_root", lambda: tmp_path)
        (tmp_path / ".env").write_text(text)
        for name in ("A_KEY", "B_KEY", "C_KEY", "D_KEY"):
            monkeypatch.delenv(name, raising=False)
        server._load_dotenv()

    def test_plain_assignment(self, tmp_path, monkeypatch):
        self._load(tmp_path, monkeypatch, "A_KEY=plain\n")
        assert os.environ["A_KEY"] == "plain"

    def test_export_prefix_is_not_part_of_the_name(self, tmp_path, monkeypatch):
        self._load(tmp_path, monkeypatch, "export B_KEY=exported\n")
        assert os.environ["B_KEY"] == "exported"

    def test_a_trailing_comment_is_not_part_of_the_value(self, tmp_path, monkeypatch):
        self._load(tmp_path, monkeypatch, "C_KEY=secret  # the good one\n")
        assert os.environ["C_KEY"] == "secret"

    def test_a_hash_inside_quotes_is_part_of_the_value(self, tmp_path, monkeypatch):
        self._load(tmp_path, monkeypatch, 'D_KEY="pa#ss"\n')
        assert os.environ["D_KEY"] == "pa#ss"

    def test_an_existing_variable_wins(self, tmp_path, monkeypatch):
        monkeypatch.setenv("A_KEY", "from the shell")
        monkeypatch.setattr(server, "_repo_root", lambda: tmp_path)
        (tmp_path / ".env").write_text("A_KEY=from the file\n")
        server._load_dotenv()
        assert os.environ["A_KEY"] == "from the shell"
