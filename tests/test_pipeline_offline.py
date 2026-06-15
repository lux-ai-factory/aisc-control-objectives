"""End-to-end offline pipeline: card → prefilter → LLM adapters (stubbed) →
orchestrator → plan. Proves the adapters satisfy the orchestrator protocols
and the whole chain holds together without any network access."""

from helpers import ScriptedClient

from wizard.agents.llm import (
    LLMChecklistProposer,
    LLMDimensionFramer,
    LLMReviewer,
    LLMTestProposer,
)
from wizard.agents.orchestrator import Orchestrator
from wizard.models.plan import (
    DimensionFrame,
    DimensionFraming,
    ItemVerdict,
    Proposal,
    ProposedItem,
    Review,
)


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
                score=5,
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
                score=5,
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

    framing = DimensionFraming(
        frames=[
            DimensionFrame(
                dimension_slug="diversity-non-discrimination-fairness",
                in_scope=True,
                residual_gaps=["independent fairness verification not evidenced"],
            ),
            DimensionFrame(dimension_slug="transparency", in_scope=True),
        ]
    )

    # one scripted client per agent (each consumes its own queue)
    framer = LLMDimensionFramer(client=ScriptedClient([framing]))
    test_proposer = LLMTestProposer(client=ScriptedClient([tests_proposal]))
    checklist_proposer = LLMChecklistProposer(client=ScriptedClient([checks_proposal]))
    known = {c.item.slug for c in test_cands} | {c.item.slug for c in checklist_cands}
    reviewer = LLMReviewer(client=ScriptedClient([accept]), known_item_ids=known)

    plan = Orchestrator(
        test_proposer=test_proposer,
        checklist_proposer=checklist_proposer,
        reviewer=reviewer,
        framer=framer,
    ).run(mcas_card, test_cands, checklist_cands)

    assert plan.status == "reviewed"
    assert [i.item_id for i in plan.tests] == [fairness]
    assert [i.item_id for i in plan.checklists] == [transparency]
    assert plan.coverage["article-13"] == [transparency]
    # all 4 MCAS open-issue articles covered → no synthetic gaps
    assert plan.gaps == []

    # Phase 0: dimensions survive the whole chain into the assembled plan.
    fairness_item = next(i for i in plan.tests if i.item_id == fairness)
    assert fairness_item.dimension_slugs == ["diversity-non-discrimination-fairness"]
    transparency_item = next(i for i in plan.checklists if i.item_id == transparency)
    assert transparency_item.dimension_slugs == ["transparency"]

    # Phase 1: the framed dimensions appear with grouped recommendations.
    fairness_dim = next(
        d for d in plan.dimensions
        if d.dimension_slug == "diversity-non-discrimination-fairness"
    )
    assert fairness == fairness_dim.recommended_item_ids[0]
    # a residual gap with a recommendation → partial
    assert fairness_dim.status == "partial"
    transparency_dim = next(
        d for d in plan.dimensions if d.dimension_slug == "transparency"
    )
    assert transparency in transparency_dim.recommended_item_ids
