"""Propose → review loop (SPEC §5.2–§5.3), tested with fakes — no API calls.

Invariants under test:
- happy path produces a reviewed plan with items split by type
- hallucinated item_ids are auto-rejected deterministically (defense in depth,
  before the reviewer ever sees them)
- reviewer 'revise' verdicts trigger another proposer round carrying the notes
- the loop hard-stops at max_rounds with warnings
- open issues not covered by accepted items surface as gaps/warnings
"""

from helpers import (
    FULL_COVERS,
    QueueProposer,
    QueueReviewer,
    SpyAcceptReviewer,
    accept_all,
    make_item,
)

from wizard.agents.orchestrator import Orchestrator
from wizard.models.plan import (
    DimensionFrame,
    DimensionFraming,
    ItemVerdict,
    Proposal,
    Review,
)


class QueueFramer:
    """Framer fake: returns a fixed framing and records the dimensions it saw."""

    def __init__(self, framing: DimensionFraming):
        self.framing = framing
        self.seen_dimensions: list[list[str]] = []

    def frame(self, card, dimension_slugs):
        self.seen_dimensions.append(list(dimension_slugs))
        return self.framing


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


class TestDimensionEnrichment:
    def test_accepted_items_carry_candidate_dimensions(self, mcas_card, world):
        test_cands, checklist_cands = world
        # pick a test candidate whose tool carries a dimension tag
        cand = next(
            c for c in test_cands if c.item.dimension_slugs()
        )
        tid = cand.item.slug
        expected = cand.item.dimension_slugs()

        orch = Orchestrator(
            test_proposer=QueueProposer(
                Proposal(items=[make_item(tid, "test", FULL_COVERS)])
            ),
            checklist_proposer=QueueProposer(),
            reviewer=AcceptAllReviewer(),
        )
        plan = orch.run(mcas_card, test_cands, checklist_cands)

        item = next(i for i in plan.tests if i.item_id == tid)
        assert item.dimension_slugs == expected
        assert expected  # guard: the fixture really exercises a dimension

    def test_checklist_dimension_enriched(self, mcas_card, world):
        test_cands, checklist_cands = world
        cand = next(c for c in checklist_cands if c.item.dimension_slugs())
        cid = cand.item.slug

        orch = Orchestrator(
            test_proposer=QueueProposer(),
            checklist_proposer=QueueProposer(
                Proposal(items=[make_item(cid, "checklist", FULL_COVERS)])
            ),
            reviewer=AcceptAllReviewer(),
        )
        plan = orch.run(mcas_card, test_cands, checklist_cands)
        item = next(i for i in plan.checklists if i.item_id == cid)
        assert item.dimension_slugs == cand.item.dimension_slugs()


class TestDimensionAssembly:
    def test_no_framer_still_populates_dimensions(self, mcas_card, world):
        test_cands, checklist_cands = world
        cand = next(c for c in test_cands if c.item.dimension_slugs())
        tid = cand.item.slug
        orch = Orchestrator(
            test_proposer=QueueProposer(
                Proposal(items=[make_item(tid, "test", FULL_COVERS)])
            ),
            checklist_proposer=QueueProposer(),
            reviewer=AcceptAllReviewer(),
        )
        plan = orch.run(mcas_card, test_cands, checklist_cands)
        # the accepted item's dimension surfaces as an in-scope assessment
        slugs = {d.dimension_slug for d in plan.dimensions}
        assert set(cand.item.dimension_slugs()) <= slugs

    def test_framer_receives_candidate_dimensions(self, mcas_card, world):
        test_cands, checklist_cands = world
        cand = next(c for c in test_cands if c.item.dimension_slugs())
        tid = cand.item.slug
        framer = QueueFramer(
            DimensionFraming(
                frames=[
                    DimensionFrame(
                        dimension_slug=cand.item.dimension_slugs()[0],
                        in_scope=True,
                        residual_gaps=["something still missing"],
                    )
                ]
            )
        )
        orch = Orchestrator(
            test_proposer=QueueProposer(
                Proposal(items=[make_item(tid, "test", FULL_COVERS)])
            ),
            checklist_proposer=QueueProposer(),
            reviewer=AcceptAllReviewer(),
            framer=framer,
        )
        plan = orch.run(mcas_card, test_cands, checklist_cands)

        # framer saw the union of candidate dimensions
        assert framer.seen_dimensions
        assert cand.item.dimension_slugs()[0] in framer.seen_dimensions[0]
        # the framed dimension carries the recommendation and a partial status
        da = next(
            d for d in plan.dimensions
            if d.dimension_slug == cand.item.dimension_slugs()[0]
        )
        assert tid in da.recommended_item_ids
        assert da.status == "partial"

    def test_high_risk_floor_applied_via_orchestrator(self, mcas_card, world):
        test_cands, checklist_cands = world
        cand = next(c for c in test_cands if c.item.dimension_slugs())
        tid = cand.item.slug
        dim = cand.item.dimension_slugs()[0]
        framer = QueueFramer(
            DimensionFraming(
                frames=[DimensionFrame(dimension_slug=dim, residual_gaps=[])]
            )
        )
        orch = Orchestrator(
            test_proposer=QueueProposer(
                Proposal(items=[make_item(tid, "test", FULL_COVERS)])
            ),
            checklist_proposer=QueueProposer(),
            reviewer=AcceptAllReviewer(),
            framer=framer,
            high_risk=True,
        )
        plan = orch.run(mcas_card, test_cands, checklist_cands)
        da = next(d for d in plan.dimensions if d.dimension_slug == dim)
        # no accepted control for the dim + high risk → floored off "covered"
        assert da.status == "partial"


class TestHallucinatedIds:
    def test_unknown_id_auto_rejected_before_review(self, mcas_card, world):
        test_cands, checklist_cands = world
        good_id = test_cands[0].item.slug
        proposal = Proposal(
            items=[make_item(good_id, "test", FULL_COVERS), make_item("made-up-tool-9000")]
        )

        reviewer = SpyAcceptReviewer()
        orch = Orchestrator(
            test_proposer=QueueProposer(proposal),
            checklist_proposer=QueueProposer(),
            reviewer=reviewer,
        )
        plan = orch.run(mcas_card, test_cands, checklist_cands)

        assert "made-up-tool-9000" not in [i.item_id for i in plan.tests]
        assert "made-up-tool-9000" not in [i.item_id for i in reviewer.seen[0].items]
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
