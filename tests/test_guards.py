"""Orchestrator-level guard behavior under each configured policy (WP1).

Uses the real MCAS card so evidence verification runs against real text.
"""

from helpers import FULL_COVERS, REAL_QUOTE, QueueProposer, SpyAcceptReviewer, make_item

from wizard.agents.orchestrator import Orchestrator
from wizard.config import GuardsConfig
from wizard.models.plan import Proposal

FAKE_QUOTE = "the system was certified against ISO 42001 in March 2024"


def guarded_item(item_id, **kwargs):
    kwargs.setdefault("covers", FULL_COVERS)
    return make_item(item_id, **kwargs)


def run(mcas_card, world, tests_proposal, guards=GuardsConfig(), checks_proposal=None):
    test_cands, checklist_cands = world
    reviewer = SpyAcceptReviewer()
    orch = Orchestrator(
        test_proposer=QueueProposer(tests_proposal),
        checklist_proposer=QueueProposer(checks_proposal or Proposal()),
        reviewer=reviewer,
        guards=guards,
    )
    return orch.run(mcas_card, test_cands, checklist_cands), reviewer


class TestDedup:
    def test_duplicate_item_merged_before_review(self, mcas_card, world):
        # the LLM proposing the same id twice must NOT cause it to be flagged as
        # a duplicate and dropped — it is merged into one row before review
        tid = world[0][0].item.slug
        proposal = Proposal(
            items=[
                guarded_item(tid, covers=["article-13"], score=3),
                guarded_item(tid, covers=["article-14"], score=5),
            ]
        )
        plan, reviewer = run(mcas_card, world, proposal)
        # one row reaches the reviewer and the plan
        assert [i.item_id for i in reviewer.seen[0].items] == [tid]
        kept = [i for i in plan.tests if i.item_id == tid]
        assert len(kept) == 1
        # covers unioned, highest score wins
        assert set(kept[0].covers) >= {"article-13", "article-14"}
        assert kept[0].score == 5
        assert any(w.startswith("duplicate-item") and tid in w for w in plan.warnings)


class TestScore:
    def test_zero_score_item_excluded(self, mcas_card, world):
        good_id = world[0][0].item.slug
        other_id = world[0][1].item.slug
        proposal = Proposal(
            items=[guarded_item(good_id, score=4), guarded_item(other_id, score=0)]
        )
        plan, reviewer = run(mcas_card, world, proposal)
        kept = [i.item_id for i in plan.tests]
        assert good_id in kept and other_id not in kept
        assert other_id not in [i.item_id for i in reviewer.seen[0].items]
        assert any(w.startswith("excluded-zero-score") and other_id in w for w in plan.warnings)


class TestEvidencePolicies:
    def test_drop_removes_zero_evidence_items_before_review(self, mcas_card, world):
        good_id = world[0][0].item.slug
        other_id = world[0][1].item.slug
        proposal = Proposal(
            items=[
                guarded_item(good_id),
                guarded_item(other_id, evidence=[FAKE_QUOTE]),
            ]
        )
        plan, reviewer = run(mcas_card, world, proposal)  # default guards: drop
        assert other_id not in [i.item_id for i in plan.tests]
        assert other_id not in [i.item_id for i in reviewer.seen[0].items]
        assert any(w.startswith("evidence-empty") and other_id in w for w in plan.warnings)

    def test_partial_strip_keeps_item(self, mcas_card, world):
        good_id = world[0][0].item.slug
        proposal = Proposal(items=[guarded_item(good_id, evidence=[REAL_QUOTE, FAKE_QUOTE])])
        plan, reviewer = run(mcas_card, world, proposal)
        [kept] = reviewer.seen[0].items
        assert kept.evidence == [REAL_QUOTE]
        assert any(w.startswith("evidence-not-found") for w in plan.warnings)

    def test_demote_lowers_score_to_one(self, mcas_card, world):
        good_id = world[0][0].item.slug
        proposal = Proposal(items=[guarded_item(good_id, evidence=[FAKE_QUOTE])])
        plan, _ = run(mcas_card, world, proposal, guards=GuardsConfig(evidence="demote"))
        [kept] = plan.tests
        assert kept.score == 1
        assert any(w.startswith("evidence-empty(demoted)") for w in plan.warnings)

    def test_off_passes_everything(self, mcas_card, world):
        good_id = world[0][0].item.slug
        proposal = Proposal(items=[guarded_item(good_id, evidence=[FAKE_QUOTE], score=5)])
        plan, _ = run(mcas_card, world, proposal, guards=GuardsConfig(evidence="off"))
        [kept] = plan.tests
        assert kept.evidence == [FAKE_QUOTE]
        assert kept.score == 5


class TestCoverageClaims:
    def test_unknown_key_stripped(self, mcas_card, world):
        good_id = world[0][0].item.slug
        # article-99 is not referenced anywhere in the MCAS card
        proposal = Proposal(items=[guarded_item(good_id, covers=["article-13", "article-99"])])
        plan, _ = run(mcas_card, world, proposal)
        [kept] = plan.tests
        assert kept.covers == ["article-13"]
        assert any("coverage-claim-unknown-key" in w for w in plan.warnings)

    def test_checklist_claim_must_be_supported_by_its_questions(self, mcas_card, world):
        # Transparency_Checklist questions cite Article 13 only — a claim of
        # article-10 is unsupported even though the card knows article-10
        transparency = next(
            c.item.slug for c in world[1] if c.item.name == "Transparency_Checklist"
        )
        proposal = Proposal(
            items=[
                guarded_item(
                    transparency, item_type="checklist", covers=["article-13", "article-10"]
                )
            ]
        )
        plan, _ = run(mcas_card, world, Proposal(), checks_proposal=proposal)
        [kept] = plan.checklists
        assert kept.covers == ["article-13"]
        assert any("coverage-claim-unsupported" in w for w in plan.warnings)

    def test_off_leaves_claims(self, mcas_card, world):
        good_id = world[0][0].item.slug
        proposal = Proposal(items=[guarded_item(good_id, covers=["article-99"])])
        plan, _ = run(mcas_card, world, proposal, guards=GuardsConfig(coverage_claims="off"))
        assert plan.tests[0].covers == ["article-99"]


class TestDatasetPairing:
    def test_dataset_with_present_test_kept(self, mcas_card, world):
        tid = world[0][0].item.slug
        did = world[0][1].item.slug
        proposal = Proposal(
            items=[guarded_item(tid), guarded_item(did, item_type="dataset", paired_test_id=tid)]
        )
        plan, _ = run(mcas_card, world, proposal)
        assert [i.item_id for i in plan.datasets] == [did]

    def test_dataset_cascade_dropped_with_its_test(self, mcas_card, world):
        tid = world[0][0].item.slug
        did = world[0][1].item.slug
        proposal = Proposal(
            items=[
                guarded_item(tid, evidence=[FAKE_QUOTE]),  # dropped by evidence guard
                guarded_item(did, item_type="dataset", paired_test_id=tid),
            ]
        )
        plan, _ = run(mcas_card, world, proposal)
        assert plan.datasets == []
        assert any(w.startswith("dataset-unpaired") for w in plan.warnings)

    def test_dataset_with_no_pairing_dropped_not_crashed(self, mcas_card, world):
        # a weaker LLM may emit a dataset with paired_test_id=None; the guard
        # must drop it with a warning, not let it crash the run
        tid = world[0][0].item.slug
        did = world[0][1].item.slug
        proposal = Proposal(
            items=[
                guarded_item(tid),
                guarded_item(did, item_type="dataset", paired_test_id=None),
            ]
        )
        plan, _ = run(mcas_card, world, proposal)
        assert plan.datasets == []
        assert any(w.startswith("dataset-unpaired") for w in plan.warnings)

    def test_off_skips_reference_check(self, mcas_card, world):
        did = world[0][1].item.slug
        proposal = Proposal(
            items=[guarded_item(did, item_type="dataset", paired_test_id="not-proposed-test")]
        )
        plan, _ = run(mcas_card, world, proposal, guards=GuardsConfig(dataset_pairing="off"))
        assert [i.item_id for i in plan.datasets] == [did]


class TestGuardReport:
    def test_reviewer_sees_guard_report(self, mcas_card, world):
        good_id = world[0][0].item.slug
        proposal = Proposal(items=[guarded_item(good_id), guarded_item("invented-id-42")])
        _, reviewer = run(mcas_card, world, proposal)
        report = reviewer.seen[0].guard_report
        assert any("invented-id-42" in line for line in report)
