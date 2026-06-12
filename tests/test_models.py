"""Domain models: system card, catalogue entries, checklists, agent I/O."""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from wizard.models.catalogue import CatalogueTool, ChecklistDoc
from wizard.models.plan import ItemVerdict, Proposal, ProposedItem, Review
from wizard.models.system_card import SystemCard

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="module")
def mcas_card() -> SystemCard:
    raw = json.loads((FIXTURES / "mcas_system_card.json").read_text())
    return SystemCard.from_card_json(raw)


class TestSystemCard:
    def test_identity(self, mcas_card):
        assert mcas_card.system_name == "MicroCredit Assist Score (MCAS)"
        assert mcas_card.system_version == "v1.2.0"
        assert mcas_card.qualification_id == "cmpeno6uw0001h9ig8l1d5b27"

    def test_sector_slugs_derived_from_labels(self, mcas_card):
        assert mcas_card.sector_slugs == {"finance-and-insurance"}

    def test_target_system_slugs_derived_from_labels(self, mcas_card):
        # label "Tabular & Structured Data" / "Tabular Classification & Regression"
        # must slugify to the qualification slug form (category:subcategory)
        assert (
            "tabular-structured-data:tabular-classification-regression"
            in mcas_card.target_system_slugs
        )
        # "Retrieval-Augmented Generation (RAG)" — parens dropped
        assert (
            "knowledge-retrieval:retrieval-augmented-generation-rag"
            in mcas_card.target_system_slugs
        )
        assert len(mcas_card.target_system_slugs) == 6

    def test_findings_parsed(self, mcas_card):
        assert len(mcas_card.findings) == 4
        articles = {f.article for f in mcas_card.findings}
        assert articles == {"Article 10", "Article 12", "Article 13", "Article 14"}

    def test_open_issues(self, mcas_card):
        assert len(mcas_card.open_issues) == 4

    def test_article_keys_aggregate_findings_and_open_issues(self, mcas_card):
        keys = mcas_card.article_keys()
        # findings reference Articles 10/12/13/14/16/19 + Annex IV
        for expected in ("article-10", "article-12", "article-13", "article-14", "annex-iv"):
            assert expected in keys

    def test_open_issue_keys_indexed_per_issue(self, mcas_card):
        per_issue = mcas_card.open_issue_keys()
        assert len(per_issue) == 4
        # first open issue is "Article 13.2: The IFU ..."
        assert per_issue[0] == {"article-13"}


class TestCatalogueTool:
    def test_from_seed_entry(self):
        tools = json.loads((FIXTURES / "tools_seed.json").read_text())
        fairness = next(t for t in tools if t["name"] == "AI Fairness 360")
        tool = CatalogueTool.from_seed(fairness)
        assert tool.slug
        assert "finance-and-insurance" in tool.tag_slugs
        assert "natural-language-processing" in tool.tag_slugs
        # article keys extracted from metadata.target_legal_requirements
        assert isinstance(tool.article_keys(), set)

    def test_all_seed_entries_parse(self):
        tools = json.loads((FIXTURES / "tools_seed.json").read_text())
        parsed = [CatalogueTool.from_seed(t) for t in tools]
        assert len(parsed) == len(tools)


class TestChecklistDoc:
    def test_from_seed_entry(self):
        controls = json.loads((FIXTURES / "controls_seed.json").read_text())
        accuracy = next(c for c in controls if c["name"] == "Accuracy_Checklist")
        doc = ChecklistDoc.from_seed(accuracy)
        assert doc.control_topic == "Accuracy"
        # question articles ("Article 15") aggregate into article keys
        assert "article-15" in doc.article_keys()

    def test_all_seed_entries_parse(self):
        controls = json.loads((FIXTURES / "controls_seed.json").read_text())
        parsed = [ChecklistDoc.from_seed(c) for c in controls]
        assert len(parsed) == len(controls)


class TestAgentSchemas:
    def test_proposal_roundtrip(self):
        payload = {
            "items": [
                {
                    "item_id": "ai-fairness-360",
                    "item_type": "test",
                    "priority": "must",
                    "rationale": "Quarterly fairness audits are claimed; verify with a standard toolkit.",
                    "evidence": ["quarterly fairness audits compare approval, default, and override rates"],
                    "covers": ["article-10"],
                }
            ],
            "coverage_gaps": ["article-12: no live-inference logging test available"],
        }
        proposal = Proposal.model_validate(payload)
        assert proposal.items[0].priority == "must"

    def test_invalid_priority_rejected(self):
        with pytest.raises(ValidationError):
            ProposedItem.model_validate(
                {
                    "item_id": "x",
                    "item_type": "test",
                    "priority": "nice-to-have",
                    "rationale": "r",
                    "evidence": [],
                    "covers": [],
                }
            )

    def test_review_verdicts(self):
        review = Review.model_validate(
            {
                "verdicts": [
                    {"item_id": "x", "verdict": "revise", "reasons": ["weak-rationale"]}
                ],
                "coverage_ok": False,
                "notes_for_revision": "Cover Article 14 explicitly.",
            }
        )
        assert review.verdicts[0].verdict == "revise"
        assert not review.coverage_ok

    def test_verdict_literal_enforced(self):
        with pytest.raises(ValidationError):
            ItemVerdict.model_validate({"item_id": "x", "verdict": "maybe", "reasons": []})

    def test_dataset_requires_paired_test_id(self):
        # G3: schema-level enforcement → the LLM gets a structured retry
        with pytest.raises(ValidationError):
            ProposedItem.model_validate(
                {
                    "item_id": "some-dataset",
                    "item_type": "dataset",
                    "priority": "should",
                    "rationale": "r",
                    "evidence": [],
                    "covers": [],
                }
            )

    def test_dataset_with_pairing_valid(self):
        item = ProposedItem.model_validate(
            {
                "item_id": "some-dataset",
                "item_type": "dataset",
                "priority": "should",
                "rationale": "r",
                "evidence": [],
                "covers": [],
                "paired_test_id": "some-test",
            }
        )
        assert item.paired_test_id == "some-test"

    def test_non_dataset_needs_no_pairing(self):
        item = ProposedItem.model_validate(
            {
                "item_id": "t",
                "item_type": "test",
                "priority": "must",
                "rationale": "r",
                "evidence": [],
                "covers": [],
            }
        )
        assert item.paired_test_id is None

    def test_proposal_carries_guard_report(self):
        proposal = Proposal.model_validate(
            {"items": [], "coverage_gaps": [], "guard_report": ["evidence-empty: x"]}
        )
        assert proposal.guard_report == ["evidence-empty: x"]
