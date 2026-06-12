"""End-to-end offline pipeline: card → prefilter → LLM adapters (stubbed) →
orchestrator → plan. Proves the adapters satisfy the orchestrator protocols
and the whole chain holds together without any network access."""

from helpers import ScriptedClient

from wizard.agents.llm import LLMChecklistProposer, LLMReviewer, LLMTestProposer
from wizard.agents.orchestrator import Orchestrator
from wizard.models.plan import ItemVerdict, Proposal, ProposedItem, Review


def test_full_offline_run(mcas_card, world):
    test_cands, checklist_cands = world

    fairness = next(c.item.slug for c in test_cands if c.item.name == "AI Fairness 360")
    transparency = next(
        c.item.slug for c in checklist_cands if c.item.name == "Transparency_Checklist"
    )

    tests_proposal = Proposal(
        items=[
            ProposedItem(
                item_id=fairness,
                item_type="test",
                priority="must",
                rationale="Fairness audits claimed quarterly; verify independently.",
                evidence=["quarterly fairness audits"],
                # article-14 via the oversight-related fairness monitoring claims;
                # Transparency_Checklist can only support article-13 (its questions)
                covers=["article-10", "article-12", "article-14"],
            )
        ]
    )
    checks_proposal = Proposal(
        items=[
            ProposedItem(
                item_id=transparency,
                item_type="checklist",
                priority="must",
                rationale="Article 13 open issue on machine-readable IFU.",
                evidence=["no machine-readable counterpart (JSON/XML) is confirmed"],
                covers=["article-13"],
            )
        ]
    )
    accept = Review(
        verdicts=[
            ItemVerdict(item_id=fairness, verdict="accept"),
            ItemVerdict(item_id=transparency, verdict="accept"),
        ],
        coverage_ok=True,
    )

    # one scripted client per agent (each consumes its own queue)
    test_proposer = LLMTestProposer(client=ScriptedClient([tests_proposal]))
    checklist_proposer = LLMChecklistProposer(client=ScriptedClient([checks_proposal]))
    known = {c.item.slug for c in test_cands} | {c.item.slug for c in checklist_cands}
    reviewer = LLMReviewer(client=ScriptedClient([accept]), known_item_ids=known)

    plan = Orchestrator(
        test_proposer=test_proposer,
        checklist_proposer=checklist_proposer,
        reviewer=reviewer,
    ).run(mcas_card, test_cands, checklist_cands)

    assert plan.status == "reviewed"
    assert [i.item_id for i in plan.tests] == [fairness]
    assert [i.item_id for i in plan.checklists] == [transparency]
    assert plan.coverage["article-13"] == [transparency]
    # all 4 MCAS open-issue articles covered → no synthetic gaps
    assert plan.gaps == []
