"""WizardPlanRunner — the composition root behind the API's PlanRunner port.

Verifies the production wiring (prefilter → proposers → reviewer →
orchestrator) with a scripted client, including config-driven model/lens
selection and the effective-config echo on the plan.
"""

import json

from helpers import FULL_COVERS, ScriptedClient, accept_all, make_item

from wizard.agents.runner import WizardPlanRunner
from wizard.config import DEFAULT_MODEL, ReviewConfig, RunConfig
from wizard.models.plan import DimensionFraming, Proposal, Review


def fairness_proposal():
    return Proposal(items=[make_item("ai-fairness-360", covers=FULL_COVERS)])


def test_runner_end_to_end(mcas_card, seed_tools, seed_checklists):
    proposal = fairness_proposal()
    # call order: framer, test proposer, checklist proposer, reviewer
    client = ScriptedClient(
        [DimensionFraming(), proposal, Proposal(), accept_all(proposal)]
    )

    runner = WizardPlanRunner(client=client, tools=seed_tools, checklists=seed_checklists)
    plan = runner.run(mcas_card)

    assert plan.status == "reviewed"
    assert plan.qualification_id == mcas_card.qualification_id
    assert [i.item_id for i in plan.tests] == ["ai-fairness-360"]

    # the reviewer call must receive known ids from BOTH tracks
    prompt = json.dumps(client.calls[3]["messages"])
    assert "ai-fairness-360" in prompt  # a tool candidate
    assert "transparency" in prompt.lower()  # a checklist candidate


def test_runner_respects_model_override(mcas_card, seed_tools, seed_checklists):
    client = ScriptedClient(
        [DimensionFraming(), Proposal(), Proposal(), Review(coverage_ok=True)]
    )
    WizardPlanRunner(
        client=client,
        tools=seed_tools,
        checklists=seed_checklists,
        config=RunConfig(model="claude-sonnet-4-6"),
    ).run(mcas_card)
    assert all(c["model"] == "claude-sonnet-4-6" for c in client.calls)


def test_reviewer_model_override_hits_only_reviewer(mcas_card, seed_tools, seed_checklists):
    client = ScriptedClient(
        [DimensionFraming(), Proposal(), Proposal(), Review(coverage_ok=True)]
    )
    WizardPlanRunner(
        client=client,
        tools=seed_tools,
        checklists=seed_checklists,
        config=RunConfig(review=ReviewConfig(reviewer_model="claude-sonnet-4-6")),
    ).run(mcas_card)
    # framer + 2 proposers use the base model; only the reviewer is overridden
    non_reviewer, reviewer_call = client.calls[:3], client.calls[3]
    assert all(c["model"] == DEFAULT_MODEL for c in non_reviewer)
    assert reviewer_call["model"] == "claude-sonnet-4-6"


def test_lenses_config_builds_multilens_reviewer(mcas_card, seed_tools, seed_checklists):
    # 1 framer call + 2 proposer calls + 3 lens reviews
    client = ScriptedClient(
        [DimensionFraming(), Proposal(), Proposal()]
        + [Review(coverage_ok=True) for _ in range(3)]
    )
    plan = WizardPlanRunner(
        client=client,
        tools=seed_tools,
        checklists=seed_checklists,
        config=RunConfig(review=ReviewConfig(lenses=["relevance", "coverage", "parsimony"])),
    ).run(mcas_card)
    assert len(client.calls) == 6
    assert plan.status == "reviewed"


def test_plan_echoes_effective_config(mcas_card, seed_tools, seed_checklists):
    client = ScriptedClient(
        [DimensionFraming(), Proposal(), Proposal(), Review(coverage_ok=True)]
    )
    cfg = RunConfig(max_rounds=2)
    plan = WizardPlanRunner(
        client=client, tools=seed_tools, checklists=seed_checklists, config=cfg
    ).run(mcas_card)
    assert plan.run_config == cfg.model_dump()


def test_per_run_config_overrides_instance_config(mcas_card, seed_tools, seed_checklists):
    client = ScriptedClient(
        [DimensionFraming(), Proposal(), Proposal(), Review(coverage_ok=True)]
    )
    runner = WizardPlanRunner(
        client=client, tools=seed_tools, checklists=seed_checklists, config=RunConfig()
    )
    plan = runner.run(mcas_card, config=RunConfig(model="claude-sonnet-4-6"))
    assert all(c["model"] == "claude-sonnet-4-6" for c in client.calls)
    assert plan.run_config["model"] == "claude-sonnet-4-6"
