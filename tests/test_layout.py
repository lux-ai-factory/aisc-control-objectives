"""The service's pages are server-rendered (templates/, static/). The standalone React app that was in
frontend/ called an API that no longer exists and was never deployed (code review 2026-10-05)."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_there_is_no_standalone_frontend():
    assert not (ROOT / "frontend").exists()
