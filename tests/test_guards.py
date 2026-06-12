"""Orchestrator-level guard behavior under each configured policy (WP1).

Uses the real MCAS card so evidence verification runs against real text.
"""

import json
from pathlib import Path

import pytest

from wizard.agents.orchestrator import Orchestrator
from wizard.config import GuardsConfig
from wizard.matching.prefilter import prefilter_checklists, prefilter_tools
from wizard.models.catalogue import CatalogueTool, ChecklistDoc
from wizard.models.plan import ItemVerdict, Proposal, ProposedItem, Review
from wizard.models.system_card import SystemCard

FIXTURES = Path(__file__).parent / "fixtures"

REAL_QUOTE = "Quarterly fairness audits compare approval, default, and override rates"
FAKE_QUOTE = "the system was certified against ISO 42001 in March 2024"
FULL_COVERS = ["article-13", "article-14", "article-10", "article-12"]


@pytest.fixture(scope="module")
def mcas() -> SystemCard:
    return SystemCard.from_card_json(
        json.loads((FIXTURES / "mcas_system_card.json").read_text())
    )


@pytest.fixture(scope="module")
def world(mcas):
    tools = [
        CatalogueTool.from_seed(t)
        for t in json.loads((FIXTURES / "tools_seed.json").read_text())
    ]
    checklists = [
        ChecklistDoc.from_seed(c)
        for c in json.loads((FIXTURES / "controls_seed.json").read_text())
    ]
    return prefilter_tools(mcas, tools), prefilter_checklists(mcas, checklists)


def make_item(item_id, item_type="test", evidence=None, covers=None, paired=None):
    return ProposedItem(
        item_id=item_id,
        item_type=item_type,
        priority="must",
        rationale="r",
        evidence=[REAL_QUOTE] if evidence is None else evidence,
        covers=FULL_COVERS if covers is None else covers,
        paired_test_id=paired,
    )


class OneShotProposer:
    def __init__(self, proposal):
        self.proposal = proposal

    def propose(self, card, candidates, revision_notes=None, prior=None):
        return self.proposal


class SpyAcceptReviewer:
    """Accepts everything; records the proposals it was shown."""

    def __init__(self):
        self.seen: list[Proposal] = []

    def review(self, card, proposal):
        self.seen.append(proposal)
        return Review(
            verdicts=[ItemVerdict(item_id=i.item_id, verdict="accept") for i in proposal.items],
            coverage_ok=True,
        )


def run(mcas, world, tests_proposal, guards=GuardsConfig(), checks_proposal=None):
    test_cands, checklist_cands = world
    reviewer = SpyAcceptReviewer()
    orch = Orchestrator(
        test_proposer=OneShotProposer(tests_proposal),
        checklist_proposer=OneShotProposer(checks_proposal or Proposal()),
        reviewer=reviewer,
        guards=guards,
    )
    return orch.run(mcas, test_cands, checklist_cands), reviewer


class TestEvidencePolicies:
    def test_drop_removes_zero_evidence_items_before_review(self, mcas, world):
        good_id = world[0][0].item.slug
        other_id = world[0][1].item.slug
        proposal = Proposal(
            items=[
                make_item(good_id),
                make_item(other_id, evidence=[FAKE_QUOTE]),
            ]
        )
        plan, reviewer = run(mcas, world, proposal)  # default guards: drop
        assert other_id not in [i.item_id for i in plan.tests]
        assert other_id not in [i.item_id for i in reviewer.seen[0].items]
        assert any(w.startswith("evidence-empty") and other_id in w for w in plan.warnings)

    def test_partial_strip_keeps_item(self, mcas, world):
        good_id = world[0][0].item.slug
        proposal = Proposal(items=[make_item(good_id, evidence=[REAL_QUOTE, FAKE_QUOTE])])
        plan, reviewer = run(mcas, world, proposal)
        [kept] = reviewer.seen[0].items
        assert kept.evidence == [REAL_QUOTE]
        assert any(w.startswith("evidence-not-found") for w in plan.warnings)

    def test_demote_keeps_item_as_optional(self, mcas, world):
        good_id = world[0][0].item.slug
        proposal = Proposal(items=[make_item(good_id, evidence=[FAKE_QUOTE])])
        plan, _ = run(mcas, world, proposal, guards=GuardsConfig(evidence="demote"))
        [kept] = plan.tests
        assert kept.priority == "optional"
        assert any(w.startswith("evidence-empty(demoted)") for w in plan.warnings)

    def test_off_passes_everything(self, mcas, world):
        good_id = world[0][0].item.slug
        proposal = Proposal(items=[make_item(good_id, evidence=[FAKE_QUOTE])])
        plan, _ = run(mcas, world, proposal, guards=GuardsConfig(evidence="off"))
        [kept] = plan.tests
        assert kept.evidence == [FAKE_QUOTE]
        assert kept.priority == "must"


class TestCoverageClaims:
    def test_unknown_key_stripped(self, mcas, world):
        good_id = world[0][0].item.slug
        # article-99 is not referenced anywhere in the MCAS card
        proposal = Proposal(items=[make_item(good_id, covers=["article-13", "article-99"])])
        plan, _ = run(mcas, world, proposal)
        [kept] = plan.tests
        assert kept.covers == ["article-13"]
        assert any("coverage-claim-unknown-key" in w for w in plan.warnings)

    def test_checklist_claim_must_be_supported_by_its_questions(self, mcas, world):
        # Transparency_Checklist questions cite Article 13 only — a claim of
        # article-10 is unsupported even though the card knows article-10
        transparency = next(
            c.item.slug for c in world[1] if c.item.name == "Transparency_Checklist"
        )
        proposal = Proposal(
            items=[
                make_item(
                    transparency, item_type="checklist", covers=["article-13", "article-10"]
                )
            ]
        )
        plan, _ = run(mcas, world, Proposal(), checks_proposal=proposal)
        [kept] = plan.checklists
        assert kept.covers == ["article-13"]
        assert any("coverage-claim-unsupported" in w for w in plan.warnings)

    def test_off_leaves_claims(self, mcas, world):
        good_id = world[0][0].item.slug
        proposal = Proposal(items=[make_item(good_id, covers=["article-99"])])
        plan, _ = run(
            mcas, world, proposal, guards=GuardsConfig(coverage_claims="off")
        )
        assert plan.tests[0].covers == ["article-99"]


class TestDatasetPairing:
    def test_dataset_with_present_test_kept(self, mcas, world):
        tid = world[0][0].item.slug
        did = world[0][1].item.slug
        proposal = Proposal(
            items=[make_item(tid), make_item(did, item_type="dataset", paired=tid)]
        )
        plan, _ = run(mcas, world, proposal)
        assert [i.item_id for i in plan.datasets] == [did]

    def test_dataset_cascade_dropped_with_its_test(self, mcas, world):
        tid = world[0][0].item.slug
        did = world[0][1].item.slug
        proposal = Proposal(
            items=[
                make_item(tid, evidence=[FAKE_QUOTE]),  # dropped by evidence guard
                make_item(did, item_type="dataset", paired=tid),
            ]
        )
        plan, _ = run(mcas, world, proposal)
        assert plan.datasets == []
        assert any(w.startswith("dataset-unpaired") for w in plan.warnings)

    def test_off_skips_reference_check(self, mcas, world):
        did = world[0][1].item.slug
        proposal = Proposal(
            items=[make_item(did, item_type="dataset", paired="not-proposed-test")]
        )
        plan, _ = run(
            mcas, world, proposal, guards=GuardsConfig(dataset_pairing="off")
        )
        assert [i.item_id for i in plan.datasets] == [did]


class TestGuardReport:
    def test_reviewer_sees_guard_report(self, mcas, world):
        good_id = world[0][0].item.slug
        proposal = Proposal(
            items=[make_item(good_id), make_item("invented-id-42")]
        )
        _, reviewer = run(mcas, world, proposal)
        report = reviewer.seen[0].guard_report
        assert any("invented-id-42" in line for line in report)
