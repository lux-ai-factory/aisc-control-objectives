"""SystemCard — the typed view over Qualification.systemCardJson.

The card is the wizard's input; the profile extractor reads it. These tests
pin the parsing, including the two card shapes in circulation.
"""

from __future__ import annotations

from wizard.models.system_card import SystemCard


def test_parses_the_frozen_mcas_card(mcas_card):
    assert mcas_card.system_name == "MicroCredit Assist Score (MCAS)"
    assert mcas_card.system_version == "v1.2.0"
    assert mcas_card.qualification_id
    assert mcas_card.findings


def test_a_minimal_card_needs_no_findings():
    card = SystemCard.from_card_json(
        {
            "system_name": "X",
            "system_version": "1",
            "qualification_id": "q1",
            "classification": {"sectors": [], "target_systems": []},
        }
    )
    assert card.findings == []


def test_a_finding_without_article_or_references_parses():
    """The running qualification emits {title, summary, points} only; the
    article fields are a hint some card versions carry, never a requirement."""
    card = SystemCard.from_card_json(
        {
            "system_name": "X",
            "system_version": "1",
            "qualification_id": "q1",
            "classification": {"sectors": [], "target_systems": []},
            "findings": [{"title": "Data", "summary": "s", "points": ["p"]}],
        }
    )
    assert card.findings[0].article == ""
    assert card.findings[0].references == []
