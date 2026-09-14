"""Composition root (server): the model is built BAF's way, once, at startup.

No network: building a local model's wrapper contacts no provider.
"""

import os

import pytest

from aisc_control_objectives import server
from aisc_control_objectives.config import RunConfig


def test_the_completer_is_built_from_the_configured_provider(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    complete = server._build_completer(RunConfig(provider="ollama", model="mistral:latest"))
    assert callable(complete)


def test_a_provider_missing_its_key_fails_at_startup_naming_the_variable(monkeypatch):
    """Better here, where the message is readable, than as a provider error on
    every card page."""
    monkeypatch.delenv("MISTRAL_API_KEY", raising=False)
    with pytest.raises(ValueError, match="MISTRAL_API_KEY"):
        server._build_completer(RunConfig(provider="mistral", model="mistral-large-latest"))


def test_a_blank_objectives_file_variable_means_the_bundled_csv():
    """Templated env files leave `WIZARD_OBJECTIVES_FILE=` empty; that is
    'no override', not 'the current directory'."""
    from aisc_control_objectives.control_objectives import default_csv_path

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
        """monkeypatch.setenv, not os.environ directly: _load_dotenv writes
        through setdefault, so without this the keys leak into every later
        test in the session and the class becomes order-dependent."""
        monkeypatch.setattr(server, "_repo_root", lambda: tmp_path)
        (tmp_path / ".env").write_text(text)
        for name in ("A_KEY", "B_KEY", "C_KEY", "D_KEY"):
            monkeypatch.delenv(name, raising=False)
        server._load_dotenv()
        for name in ("A_KEY", "B_KEY", "C_KEY", "D_KEY"):
            if name in os.environ:
                monkeypatch.setenv(name, os.environ[name])

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
