"""Propose → review loop (SPEC §5.2–§5.3), tested with fakes — no API calls.

Invariants under test:
- happy path produces a reviewed plan with items split by type
- hallucinated item_ids are auto-rejected deterministically (defense in depth,
  before the reviewer ever sees them)
- reviewer 'revise' verdicts trigger another proposer round carrying the notes
- the loop hard-stops at max_rounds with warnings
- open issues not covered by accepted items surface as gaps/warnings
"""

import json
from pathlib import Path

import pytest

from wizard.agents.orchestrator import Orchestrator
from wizard.matching.prefilter import prefilter_checklists, prefilter_tools
from wizard.models.catalogue import CatalogueTool, ChecklistDoc
from wizard.models.plan import Proposal, ProposedItem, Review, ItemVerdict
from wizard.models.system_card import SystemCard

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="module")
def mcas() -> SystemCard:
    return SystemCard.from_card_json(
        json.loads((FIXTURES / "mcas_system_card.json").read_text())
    )


@pytest.fixture()
def candidates(mcas):
    tools = [
        CatalogueTool.from_seed(t)
        for t in json.loads((FIXTURES / "tools_seed.json").read_text())
    ]
    checklists = [
        ChecklistDoc.from_seed(c)
        for c in json.loads((FIXTURES / "controls_seed.json").read_text())
    ]
    return prefilter_tools(mcas, tools), prefilter_checklists(mcas, checklists)


# a real quote from the MCAS card — survives the (default-on) evidence guard
REAL_QUOTE = "Quarterly fairness audits compare approval, default, and override rates"


def item(item_id, item_type="test", covers=("article-10",), priority="must", paired_test_id=None):
    return ProposedItem(
        item_id=item_id,
        item_type=item_type,
        priority=priority,
        rationale="r",
        evidence=[REAL_QUOTE],
        covers=list(covers),
        paired_test_id=paired_test_id if item_type == "dataset" else None,
    )


class FakeProposer:
    """Returns queued proposals; records the revision notes it receives."""

    def __init__(self, *proposals: Proposal):
        self.proposals = list(proposals)
        self.received_notes: list[str | None] = []
        self.calls = 0

    def propose(self, card, candidates, revision_notes=None, prior=None) -> Proposal:
        self.calls += 1
        self.received_notes.append(revision_notes)
        return self.proposals[min(self.calls - 1, len(self.proposals) - 1)]


class FakeReviewer:
    def __init__(self, *reviews: Review):
        self.reviews = list(reviews)
        self.calls = 0

    def review(self, card, proposal: Proposal) -> Review:
        self.calls += 1
        return self.reviews[min(self.calls - 1, len(self.reviews) - 1)]


def accept_all(proposal: Proposal) -> Review:
    return Review(
        verdicts=[ItemVerdict(item_id=i.item_id, verdict="accept") for i in proposal.items],
        coverage_ok=True,
    )


# MCAS open issues map to articles 13, 14, 10, 12 — cover them all
FULL_COVERS = ["article-13", "article-14", "article-10", "article-12"]


class TestHappyPath:
    def test_single_round_reviewed_plan(self, mcas, candidates):
        test_cands, checklist_cands = candidates
        test_id = test_cands[0].item.slug
        checklist_id = checklist_cands[0].item.slug

        tests_proposal = Proposal(items=[item(test_id, "test", FULL_COVERS)])
        checks_proposal = Proposal(items=[item(checklist_id, "checklist", FULL_COVERS)])

        proposer_tests = FakeProposer(tests_proposal)
        proposer_checks = FakeProposer(checks_proposal)

        class R(FakeReviewer):
            def review(self, card, proposal):
                self.calls += 1
                return accept_all(proposal)

        orch = Orchestrator(
            test_proposer=proposer_tests,
            checklist_proposer=proposer_checks,
            reviewer=R(),
        )
        plan = orch.run(mcas, test_cands, checklist_cands)

        assert plan.status == "reviewed"
        assert plan.review_rounds == 1
        assert [i.item_id for i in plan.tests] == [test_id]
        assert [i.item_id for i in plan.checklists] == [checklist_id]
        assert plan.qualification_id == mcas.qualification_id
        # coverage matrix: every covered key lists the covering items
        assert test_id in plan.coverage["article-13"]

    def test_dataset_items_split_into_datasets(self, mcas, candidates):
        test_cands, checklist_cands = candidates
        tid = test_cands[0].item.slug
        proposal = Proposal(
            items=[
                item(tid, "test", FULL_COVERS),
                item(tid, "dataset", ["article-10"], paired_test_id=tid),
            ]
        )
        orch = Orchestrator(
            test_proposer=FakeProposer(proposal),
            checklist_proposer=FakeProposer(Proposal()),
            reviewer=type("R", (FakeReviewer,), {"review": lambda s, c, p: accept_all(p)})(),
        )
        plan = orch.run(mcas, test_cands, checklist_cands)
        assert len(plan.datasets) == 1


class TestHallucinatedIds:
    def test_unknown_id_auto_rejected_before_review(self, mcas, candidates):
        test_cands, checklist_cands = candidates
        good_id = test_cands[0].item.slug
        proposal = Proposal(
            items=[item(good_id, "test", FULL_COVERS), item("made-up-tool-9000", "test")]
        )

        seen_by_reviewer: list[list[str]] = []

        class SpyReviewer(FakeReviewer):
            def review(self, card, p):
                seen_by_reviewer.append([i.item_id for i in p.items])
                return accept_all(p)

        orch = Orchestrator(
            test_proposer=FakeProposer(proposal),
            checklist_proposer=FakeProposer(Proposal()),
            reviewer=SpyReviewer(),
        )
        plan = orch.run(mcas, test_cands, checklist_cands)

        assert "made-up-tool-9000" not in [i.item_id for i in plan.tests]
        assert "made-up-tool-9000" not in seen_by_reviewer[0]
        assert any("made-up-tool-9000" in w for w in plan.warnings)


class TestRevisionLoop:
    def test_revise_triggers_second_round_with_notes(self, mcas, candidates):
        test_cands, checklist_cands = candidates
        tid = test_cands[0].item.slug

        round1 = Proposal(items=[item(tid, "test", ["article-10"])])
        round2 = Proposal(items=[item(tid, "test", FULL_COVERS)])
        proposer = FakeProposer(round1, round2)

        revise = Review(
            verdicts=[ItemVerdict(item_id=tid, verdict="revise", reasons=["weak-coverage"])],
            coverage_ok=False,
            notes_for_revision="Cover articles 13/14/12 too.",
        )
        reviewer = FakeReviewer(revise, accept_all(round2))

        orch = Orchestrator(
            test_proposer=proposer,
            checklist_proposer=FakeProposer(Proposal()),
            reviewer=reviewer,
        )
        plan = orch.run(mcas, test_cands, checklist_cands)

        assert plan.review_rounds == 2
        assert proposer.calls == 2
        assert proposer.received_notes[0] is None
        assert "Cover articles 13/14/12" in proposer.received_notes[1]
        assert plan.status == "reviewed"

    def test_max_rounds_cutoff_with_warnings(self, mcas, candidates):
        test_cands, checklist_cands = candidates
        tid = test_cands[0].item.slug
        proposal = Proposal(items=[item(tid, "test", ["article-10"])])
        always_revise = Review(
            verdicts=[ItemVerdict(item_id=tid, verdict="revise", reasons=["weak"])],
            coverage_ok=False,
            notes_for_revision="never satisfied",
        )
        orch = Orchestrator(
            test_proposer=FakeProposer(proposal),
            checklist_proposer=FakeProposer(Proposal()),
            reviewer=FakeReviewer(always_revise),
            max_rounds=3,
        )
        plan = orch.run(mcas, test_cands, checklist_cands)

        assert plan.review_rounds == 3
        assert plan.status == "draft"  # never reached 'reviewed'
        assert plan.warnings  # outstanding reviewer objections recorded


class TestCoverage:
    def test_uncovered_open_issues_become_gaps(self, mcas, candidates):
        test_cands, checklist_cands = candidates
        tid = test_cands[0].item.slug
        # covers only article-13; MCAS open issues also touch 14, 10, 12
        proposal = Proposal(items=[item(tid, "test", ["article-13"])])
        orch = Orchestrator(
            test_proposer=FakeProposer(proposal),
            checklist_proposer=FakeProposer(Proposal()),
            reviewer=type("R", (FakeReviewer,), {"review": lambda s, c, p: accept_all(p)})(),
        )
        plan = orch.run(mcas, test_cands, checklist_cands)
        gap_text = " ".join(plan.gaps)
        for key in ("article-14", "article-10", "article-12"):
            assert key in gap_text
        assert "article-13" not in gap_text
