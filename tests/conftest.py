"""Shared fixtures: the frozen system card and the objectives catalogue."""

import json
from pathlib import Path

import pytest

from wizard.control_objectives import load_control_objectives
from wizard.models.system_card import SystemCard

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="session")
def fixtures_dir() -> Path:
    return FIXTURES


@pytest.fixture(scope="session")
def mcas_raw() -> dict:
    return json.loads((FIXTURES / "mcas_system_card.json").read_text())


@pytest.fixture(scope="session")
def mcas_card(mcas_raw) -> SystemCard:
    return SystemCard.from_card_json(mcas_raw)


@pytest.fixture(scope="session")
def objectives():
    return load_control_objectives()
