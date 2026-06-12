"""Shared fixtures: the frozen seed data every test file works against."""

import json
from pathlib import Path

import pytest

from wizard.matching.prefilter import prefilter_checklists, prefilter_tools
from wizard.models.catalogue import CatalogueTool, ChecklistDoc
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
def seed_tools_raw() -> list[dict]:
    return json.loads((FIXTURES / "tools_seed.json").read_text())


@pytest.fixture(scope="session")
def seed_checklists_raw() -> list[dict]:
    return json.loads((FIXTURES / "controls_seed.json").read_text())


@pytest.fixture(scope="session")
def seed_tools(seed_tools_raw) -> list[CatalogueTool]:
    return [CatalogueTool.from_seed(t) for t in seed_tools_raw]


@pytest.fixture(scope="session")
def seed_checklists(seed_checklists_raw) -> list[ChecklistDoc]:
    return [ChecklistDoc.from_seed(c) for c in seed_checklists_raw]


@pytest.fixture(scope="session")
def world(mcas_card, seed_tools, seed_checklists):
    """(test_candidates, checklist_candidates) prefiltered for the MCAS card."""
    return (
        prefilter_tools(mcas_card, seed_tools),
        prefilter_checklists(mcas_card, seed_checklists),
    )
