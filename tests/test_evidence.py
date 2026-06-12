"""G1 — evidence-quote verification against the system card (WP1).

Quotes must be verbatim-ish: exact after normalization, or fuzzy ≥ 0.90
(survives punctuation drift, rejects paraphrase).
"""

import json
from pathlib import Path

import pytest

from wizard.matching.evidence import card_corpus, find_quote, verify_evidence
from wizard.models.plan import ProposedItem
from wizard.models.system_card import SystemCard

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="module")
def mcas() -> SystemCard:
    return SystemCard.from_card_json(
        json.loads((FIXTURES / "mcas_system_card.json").read_text())
    )


@pytest.fixture(scope="module")
def corpus(mcas) -> str:
    return card_corpus(mcas)


class TestFindQuote:
    def test_exact_quote_from_finding_points(self, corpus):
        assert find_quote(
            "Quarterly fairness audits compare approval, default, and override rates", corpus
        )

    def test_case_and_whitespace_insensitive(self, corpus):
        assert find_quote(
            "quarterly  FAIRNESS audits compare approval, default,\nand override rates",
            corpus,
        )

    def test_punctuation_drift_passes_fuzzy(self, corpus):
        # commas elided by the model — still the same sentence
        assert find_quote(
            "quarterly fairness audits compare approval default and override rates", corpus
        )

    def test_paraphrase_rejected(self, corpus):
        assert not find_quote("fairness is audited every three months by the bank", corpus)

    def test_fabricated_quote_rejected(self, corpus):
        assert not find_quote(
            "the system underwent ISO 42001 certification in 2024", corpus
        )

    def test_open_issue_text_is_in_corpus(self, corpus):
        assert find_quote(
            "no machine-readable counterpart (JSON/XML) is confirmed", corpus
        )

    def test_empty_quote_rejected(self, corpus):
        assert not find_quote("", corpus)
        assert not find_quote("   ", corpus)


class TestVerifyEvidence:
    def _item(self, evidence):
        return ProposedItem(
            item_id="x",
            item_type="test",
            priority="must",
            rationale="r",
            evidence=evidence,
            covers=[],
        )

    def test_keeps_only_verifiable_quotes(self, mcas):
        item = self._item(
            [
                "Quarterly fairness audits compare approval, default, and override rates",
                "completely invented claim about certification",
            ]
        )
        verified = verify_evidence(item, mcas)
        assert len(verified) == 1
        assert "fairness audits" in verified[0]

    def test_all_invented_returns_empty(self, mcas):
        assert verify_evidence(self._item(["made up", "also made up"]), mcas) == []
