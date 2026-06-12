"""Regression tests for the code-review findings (one class per finding)."""

import pytest
from helpers import QueueProposer, QueueReviewer, make_item
from pydantic import ValidationError

from wizard.agents.orchestrator import Orchestrator
from wizard.config import RunConfig
from wizard.matching.evidence import find_quote
from wizard.matching.tag_map import map_ai_type
from wizard.models.plan import ItemVerdict, Proposal, Review
from wizard.models.system_card import SystemCard


class TestFuzzyEvidenceSlack:
    """Finding 2: same-length sliding windows must give real slack above 0.90.

    The quote sits mid-corpus: with the old 1.2×-length windows the best
    achievable ratio there was ~0.909, so any real drift failed; only
    end-of-corpus truncation accidentally rescued short fixtures.
    """

    PAD = "unrelated sentences about governance procedures and committee structures. " * 5

    def test_two_char_drift_in_mid_corpus_quote_passes(self):
        corpus = (
            self.PAD
            + "the model is re-calibrated on a fixed cadence with dataset hashes recorded"
            + self.PAD
        )
        # drift: "recalibrated" (hyphen dropped) + one inserted comma
        assert find_quote(
            "the model is recalibrated on a fixed cadence, with dataset hashes", corpus
        )

    def test_paraphrase_still_rejected(self):
        corpus = (
            self.PAD
            + "the model is re-calibrated on a fixed cadence with dataset hashes recorded"
            + self.PAD
        )
        assert not find_quote("the model gets retrained regularly with hash tracking", corpus)


class TestAgenticSlugMapping:
    """Finding 7: 'Agents & Agentic Systems' label form must map."""

    def test_ampersand_dropped_slug_maps(self):
        assert (
            map_ai_type("agents-agentic-systems:autonomous-agents")
            == "agents-and-agentic-systems"
        )

    def test_label_roundtrip_through_system_card(self, mcas_raw):
        raw = dict(mcas_raw)
        raw["classification"] = {
            "sectors": ["Finance and insurance"],
            "target_systems": [
                {"category": "Agents & Agentic Systems", "subcategory": "Autonomous Agents"}
            ],
        }
        card = SystemCard.from_card_json(raw)
        [slug] = card.target_system_slugs
        assert map_ai_type(slug) == "agents-and-agentic-systems"


class TestArticlelessOpenIssues:
    """Finding 3: open issues without article refs get synthetic keys."""

    @pytest.fixture()
    def card(self, mcas_raw):
        raw = dict(mcas_raw)
        raw["open_issues"] = [
            "Article 13.2: machine-readable IFU not confirmed",
            "No documented human oversight escalation procedure beyond committees",
        ]
        return SystemCard.from_card_json(raw)

    def test_synthetic_key_assigned(self, card):
        per_issue = card.open_issue_keys()
        assert per_issue[0] == {"article-13"}
        assert per_issue[1] == {"open-issue-2"}

    def test_synthetic_keys_are_card_known(self, card):
        # G2 must not strip a legitimate open-issue cover claim
        assert "open-issue-2" in card.article_keys()

    def test_uncovered_articleless_issue_becomes_gap(self, card, world):
        test_cands, checklist_cands = world
        tid = test_cands[0].item.slug
        proposal = Proposal(items=[make_item(tid, covers=("article-13",))])
        accept = Review(
            verdicts=[ItemVerdict(item_id=tid, verdict="accept")], coverage_ok=True
        )
        plan = Orchestrator(
            test_proposer=QueueProposer(proposal),
            checklist_proposer=QueueProposer(Proposal()),
            reviewer=QueueReviewer(accept),
        ).run(card, test_cands, checklist_cands)
        assert any("open-issue-2" in g for g in plan.gaps)

    def test_covering_articleless_issue_clears_gap(self, card, world):
        test_cands, checklist_cands = world
        tid = test_cands[0].item.slug
        proposal = Proposal(items=[make_item(tid, covers=("article-13", "open-issue-2"))])
        accept = Review(
            verdicts=[ItemVerdict(item_id=tid, verdict="accept")], coverage_ok=True
        )
        plan = Orchestrator(
            test_proposer=QueueProposer(proposal),
            checklist_proposer=QueueProposer(Proposal()),
            reviewer=QueueReviewer(accept),
        ).run(card, test_cands, checklist_cands)
        assert not any("open-issue-2" in g for g in plan.gaps)
        assert "open-issue-2" in plan.coverage


class TestPostReviewDatasetCascade:
    """Finding 5: reviewer rejecting a test must cascade to its dataset."""

    def test_orphan_dataset_dropped_after_review(self, mcas_card, world):
        test_cands, checklist_cands = world
        tid = test_cands[0].item.slug
        did = test_cands[1].item.slug
        proposal = Proposal(
            items=[make_item(tid), make_item(did, item_type="dataset", paired_test_id=tid)]
        )
        review = Review(
            verdicts=[
                ItemVerdict(item_id=tid, verdict="reject", reasons=["weak-rationale"]),
                ItemVerdict(item_id=did, verdict="accept"),
            ],
            coverage_ok=True,
        )
        plan = Orchestrator(
            test_proposer=QueueProposer(proposal),
            checklist_proposer=QueueProposer(Proposal()),
            reviewer=QueueReviewer(review),
            max_rounds=1,
        ).run(mcas_card, test_cands, checklist_cands)
        assert plan.datasets == []
        assert any("dataset-unpaired" in w for w in plan.warnings)


class TestDuplicateVerdicts:
    """Finding 6: worst verdict must win within a single review."""

    def test_reject_then_accept_does_not_flip(self, mcas_card, world):
        test_cands, checklist_cands = world
        tid = test_cands[0].item.slug
        proposal = Proposal(items=[make_item(tid)])
        duplicate = Review(
            verdicts=[
                ItemVerdict(item_id=tid, verdict="reject", reasons=["id-not-found"]),
                ItemVerdict(item_id=tid, verdict="accept"),
            ],
            coverage_ok=True,
            notes_for_revision="remove it",
        )
        plan = Orchestrator(
            test_proposer=QueueProposer(proposal),
            checklist_proposer=QueueProposer(Proposal()),
            reviewer=QueueReviewer(duplicate),
            max_rounds=1,
        ).run(mcas_card, test_cands, checklist_cands)
        # the reject row must win over the later accept row for the same id
        assert tid not in [i.item_id for i in plan.tests]
        # and the loop must agree: the run did not converge
        assert plan.status == "draft"


class TestStallNotesFallback:
    """Finding 10: non-convergence without notes must still change the prompt."""

    def test_synthesized_notes_on_empty_reviewer_notes(self, mcas_card, world):
        test_cands, checklist_cands = world
        tid = test_cands[0].item.slug
        proposer = QueueProposer(Proposal(items=[make_item(tid)]))
        stall = Review(verdicts=[], coverage_ok=False, notes_for_revision="")
        Orchestrator(
            test_proposer=proposer,
            checklist_proposer=QueueProposer(Proposal()),
            reviewer=QueueReviewer(stall),
            max_rounds=2,
        ).run(mcas_card, test_cands, checklist_cands)
        assert proposer.received_notes[1]  # round 2 received a non-empty nudge


class TestConfigEnvErrors:
    """Finding 9: junk env values must raise ValidationError, not ValueError."""

    def test_non_numeric_max_rounds(self):
        with pytest.raises(ValidationError):
            RunConfig.from_env({"WIZARD_MAX_ROUNDS": "unlimited"})


class TestUnmappedTagsSurface:
    """Finding 8: unmapped categories must reach plan warnings."""

    def test_unknown_category_warned_on_plan(self, mcas_raw, seed_tools, seed_checklists):
        from helpers import ScriptedClient

        from wizard.agents.runner import WizardPlanRunner

        raw = dict(mcas_raw)
        raw["classification"] = {
            "sectors": ["Finance and insurance"],
            "target_systems": [
                {"category": "Quantum Computing", "subcategory": "Qubit Stuff"},
                {"category": "Natural Language Processing", "subcategory": "Question Answering"},
            ],
        }
        card = SystemCard.from_card_json(raw)

        client = ScriptedClient([Proposal(), Proposal(), Review(coverage_ok=True)])
        plan = WizardPlanRunner(
            client=client, tools=seed_tools, checklists=seed_checklists, config=RunConfig()
        ).run(card)
        assert any("tag-unmapped" in w and "quantum-computing" in w for w in plan.warnings)


class TestProposerPromptNamesPairing:
    """Finding 4a: the system prompt must name the paired_test_id field."""

    def test_prompt_mentions_field(self):
        from wizard.agents.llm import _PROPOSER_SYSTEM

        assert "paired_test_id" in _PROPOSER_SYSTEM


class TestFailedPlanAPI:
    """Finding 4b (A4): the API returns 502 with the failed plan, stored."""

    def test_502_with_stored_failed_plan(self, mcas_raw):
        from datetime import datetime, timezone

        from fastapi.testclient import TestClient

        from wizard.api.app import create_app
        from wizard.models.plan import AssessmentPlan

        failed = AssessmentPlan(
            plan_id="plan-failed",
            qualification_id=mcas_raw["qualification_id"],
            system_name=mcas_raw["system_name"],
            created_at=datetime.now(timezone.utc),
            status="failed",
            warnings=["run-failed: RuntimeError: api down"],
        )

        class Provider:
            def list_qualifications(self):
                return []

            def get_system_card(self, qid):
                return SystemCard.from_card_json(mcas_raw)

        class Runner:
            def run(self, card, config=None):
                return failed

        api = TestClient(create_app(qualification_provider=Provider(), plan_runner=Runner()))
        resp = api.post(
            "/api/plans", json={"qualification_id": mcas_raw["qualification_id"]}
        )
        assert resp.status_code == 502
        assert resp.json()["status"] == "failed"
        # stored and retrievable for the audit trail
        assert api.get("/api/plans/plan-failed").status_code == 200


class TestRunFailurePolicy:
    """Finding 4b (A4): agent exceptions become a failed plan, not a 500."""

    def test_runner_returns_failed_plan(self, mcas_card, seed_tools):
        from wizard.agents.runner import WizardPlanRunner

        class ExplodingClient:
            def __init__(self):
                self.messages = self

            def parse(self, **kwargs):
                raise RuntimeError("api down")

        plan = WizardPlanRunner(
            client=ExplodingClient(), tools=seed_tools, checklists=[], config=RunConfig()
        ).run(mcas_card)
        assert plan.status == "failed"
        assert plan.tests == plan.datasets == plan.checklists == []
        assert any(w.startswith("run-failed: RuntimeError") for w in plan.warnings)
        assert plan.run_config  # config still echoed on failure
