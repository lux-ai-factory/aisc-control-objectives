"""Propose → review loop (SPEC §5.2–§5.3), tested with fakes — no API calls.

Invariants under test:
- happy path produces a reviewed plan with items split by type
- hallucinated item_ids are auto-rejected deterministically (defense in depth,
  before the reviewer ever sees them)
- reviewer 'revise' verdicts trigger another proposer round carrying the notes
- the loop hard-stops at max_rounds with warnings
- open issues not covered by accepted items surface as gaps/warnings
"""

from helpers import FULL_COVERS, QueueProposer, QueueReviewer, accept_all, make_item

from wizard.agents.orchestrator import Orchestrator
from wizard.models.plan import ItemVerdict, Proposal, Review


class AcceptAllReviewer:
    def review(self, card, proposal):
        return accept_all(proposal)


class TestHappyPath:
    def test_single_round_reviewed_plan(self, mcas_card, world):
        test_cands, checklist_cands = world
        test_id = test_cands[0].item.slug
        checklist_id = checklist_cands[0].item.slug

        orch = Orchestrator(
            test_proposer=QueueProposer(Proposal(items=[make_item(test_id, "test", FULL_COVERS)])),
            checklist_proposer=QueueProposer(
                Proposal(items=[make_item(checklist_id, "checklist", FULL_COVERS)])
            ),
            reviewer=AcceptAllReviewer(),
        )
        plan = orch.run(mcas_card, test_cands, checklist_cands)

        assert plan.status == "reviewed"
        assert plan.review_rounds == 1
        assert [i.item_id for i in plan.tests] == [test_id]
        assert [i.item_id for i in plan.checklists] == [checklist_id]
        assert plan.qualification_id == mcas_card.qualification_id
        # coverage matrix: every covered key lists the covering items
        assert test_id in plan.coverage["article-13"]

    def test_dataset_items_split_into_datasets(self, mcas_card, world):
        test_cands, checklist_cands = world
        tid = test_cands[0].item.slug
        proposal = Proposal(
            items=[
                make_item(tid, "test", FULL_COVERS),
                make_item(tid, "dataset", ["article-10"], paired_test_id=tid),
            ]
        )
        orch = Orchestrator(
            test_proposer=QueueProposer(proposal),
            checklist_proposer=QueueProposer(),
            reviewer=AcceptAllReviewer(),
        )
        plan = orch.run(mcas_card, test_cands, checklist_cands)
        assert len(plan.datasets) == 1


class TestHallucinatedIds:
    def test_unknown_id_auto_rejected_before_review(self, mcas_card, world):
        test_cands, checklist_cands = world
        good_id = test_cands[0].item.slug
        proposal = Proposal(
            items=[make_item(good_id, "test", FULL_COVERS), make_item("made-up-tool-9000")]
        )

        seen_by_reviewer: list[list[str]] = []

        class SpyReviewer:
            def review(self, card, p):
                seen_by_reviewer.append([i.item_id for i in p.items])
                return accept_all(p)

        orch = Orchestrator(
            test_proposer=QueueProposer(proposal),
            checklist_proposer=QueueProposer(),
            reviewer=SpyReviewer(),
        )
        plan = orch.run(mcas_card, test_cands, checklist_cands)

        assert "made-up-tool-9000" not in [i.item_id for i in plan.tests]
        assert "made-up-tool-9000" not in seen_by_reviewer[0]
        assert any("made-up-tool-9000" in w for w in plan.warnings)


class TestRevisionLoop:
    def test_revise_triggers_second_round_with_notes(self, mcas_card, world):
        test_cands, checklist_cands = world
        tid = test_cands[0].item.slug

        round1 = Proposal(items=[make_item(tid, "test", ["article-10"])])
        round2 = Proposal(items=[make_item(tid, "test", FULL_COVERS)])
        proposer = QueueProposer(round1, round2)

        revise = Review(
            verdicts=[ItemVerdict(item_id=tid, verdict="revise", reasons=["weak-coverage"])],
            coverage_ok=False,
            notes_for_revision="Cover articles 13/14/12 too.",
        )
        reviewer = QueueReviewer(revise, accept_all(round2))

        orch = Orchestrator(
            test_proposer=proposer,
            checklist_proposer=QueueProposer(),
            reviewer=reviewer,
        )
        plan = orch.run(mcas_card, test_cands, checklist_cands)

        assert plan.review_rounds == 2
        assert proposer.calls == 2
        assert proposer.received_notes[0] is None
        assert "Cover articles 13/14/12" in proposer.received_notes[1]
        assert plan.status == "reviewed"

    def test_max_rounds_cutoff_with_warnings(self, mcas_card, world):
        test_cands, checklist_cands = world
        tid = test_cands[0].item.slug
        proposal = Proposal(items=[make_item(tid, "test", ["article-10"])])
        always_revise = Review(
            verdicts=[ItemVerdict(item_id=tid, verdict="revise", reasons=["weak"])],
            coverage_ok=False,
            notes_for_revision="never satisfied",
        )
        orch = Orchestrator(
            test_proposer=QueueProposer(proposal),
            checklist_proposer=QueueProposer(),
            reviewer=QueueReviewer(always_revise),
            max_rounds=3,
        )
        plan = orch.run(mcas_card, test_cands, checklist_cands)

        assert plan.review_rounds == 3
        assert plan.status == "draft"  # never reached 'reviewed'
        assert plan.warnings  # outstanding reviewer objections recorded


class TestCoverage:
    def test_uncovered_open_issues_become_gaps(self, mcas_card, world):
        test_cands, checklist_cands = world
        tid = test_cands[0].item.slug
        # covers only article-13; MCAS open issues also touch 14, 10, 12
        proposal = Proposal(items=[make_item(tid, "test", ["article-13"])])
        orch = Orchestrator(
            test_proposer=QueueProposer(proposal),
            checklist_proposer=QueueProposer(),
            reviewer=AcceptAllReviewer(),
        )
        plan = orch.run(mcas_card, test_cands, checklist_cands)
        gap_text = " ".join(plan.gaps)
        for key in ("article-14", "article-10", "article-12"):
            assert key in gap_text
        assert "article-13" not in gap_text
