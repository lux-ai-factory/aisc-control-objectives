"""WizardPlanRunner — the composition root behind the API's PlanRunner port.

Verifies the production wiring (prefilter → proposers → reviewer →
orchestrator) with a scripted client, including that the reviewer's known-ids
set is derived from the prefiltered candidates of *both* tracks.
"""

import json
from pathlib import Path

import pytest

from wizard.agents.runner import WizardPlanRunner
from wizard.models.catalogue import CatalogueTool, ChecklistDoc
from wizard.models.plan import ItemVerdict, Proposal, ProposedItem, Review
from wizard.models.system_card import SystemCard

FIXTURES = Path(__file__).parent / "fixtures"


class ScriptedClient:
    def __init__(self, outputs):
        self._outputs = list(outputs)
        self.calls = []
        self.messages = self

    def parse(self, **kwargs):
        self.calls.append(kwargs)
        out = self._outputs.pop(0)
        return type("P", (), {"parsed_output": out})()


@pytest.fixture(scope="module")
def card() -> SystemCard:
    return SystemCard.from_card_json(
        json.loads((FIXTURES / "mcas_system_card.json").read_text())
    )


@pytest.fixture(scope="module")
def tools() -> list[CatalogueTool]:
    return [
        CatalogueTool.from_seed(t)
        for t in json.loads((FIXTURES / "tools_seed.json").read_text())
    ]


@pytest.fixture(scope="module")
def checklists() -> list[ChecklistDoc]:
    return [
        ChecklistDoc.from_seed(c)
        for c in json.loads((FIXTURES / "controls_seed.json").read_text())
    ]


def test_runner_end_to_end(card, tools, checklists):
    proposal_tests = Proposal(
        items=[
            ProposedItem(
                item_id="ai-fairness-360",
                item_type="test",
                priority="must",
                rationale="r",
                evidence=[
                    "Quarterly fairness audits compare approval, default, and override rates"
                ],
                covers=["article-10", "article-12", "article-13", "article-14"],
            )
        ]
    )
    proposal_checks = Proposal(items=[])
    review = Review(
        verdicts=[ItemVerdict(item_id="ai-fairness-360", verdict="accept")],
        coverage_ok=True,
    )
    # call order: test proposer, checklist proposer, reviewer
    client = ScriptedClient([proposal_tests, proposal_checks, review])

    runner = WizardPlanRunner(client=client, tools=tools, checklists=checklists)
    plan = runner.run(card)

    assert plan.status == "reviewed"
    assert plan.qualification_id == card.qualification_id
    assert [i.item_id for i in plan.tests] == ["ai-fairness-360"]

    # the reviewer call must receive known ids from BOTH tracks
    reviewer_call = client.calls[2]
    prompt = json.dumps(reviewer_call["messages"])
    assert "ai-fairness-360" in prompt  # a tool candidate
    assert "Transparency_Checklist" in prompt or "transparency" in prompt.lower()


def test_runner_respects_model_override(card, tools, checklists):
    from wizard.config import RunConfig

    client = ScriptedClient(
        [Proposal(), Proposal(), Review(coverage_ok=True)]
    )
    WizardPlanRunner(
        client=client,
        tools=tools,
        checklists=checklists,
        config=RunConfig(model="claude-sonnet-4-6"),
    ).run(card)
    assert all(c["model"] == "claude-sonnet-4-6" for c in client.calls)


def test_reviewer_model_override_hits_only_reviewer(card, tools, checklists):
    from wizard.config import ReviewConfig, RunConfig

    client = ScriptedClient([Proposal(), Proposal(), Review(coverage_ok=True)])
    WizardPlanRunner(
        client=client,
        tools=tools,
        checklists=checklists,
        config=RunConfig(review=ReviewConfig(reviewer_model="claude-sonnet-4-6")),
    ).run(card)
    proposer_calls, reviewer_call = client.calls[:2], client.calls[2]
    assert all(c["model"] == "claude-opus-4-8" for c in proposer_calls)
    assert reviewer_call["model"] == "claude-sonnet-4-6"


def test_lenses_config_builds_multilens_reviewer(card, tools, checklists):
    from wizard.config import ReviewConfig, RunConfig

    # 2 proposer calls + 3 lens reviews
    client = ScriptedClient(
        [Proposal(), Proposal()]
        + [Review(coverage_ok=True) for _ in range(3)]
    )
    plan = WizardPlanRunner(
        client=client,
        tools=tools,
        checklists=checklists,
        config=RunConfig(
            review=ReviewConfig(lenses=["relevance", "coverage", "parsimony"])
        ),
    ).run(card)
    assert len(client.calls) == 5
    assert plan.status == "reviewed"


def test_plan_echoes_effective_config(card, tools, checklists):
    from wizard.config import RunConfig

    client = ScriptedClient([Proposal(), Proposal(), Review(coverage_ok=True)])
    cfg = RunConfig(max_rounds=2)
    plan = WizardPlanRunner(
        client=client, tools=tools, checklists=checklists, config=cfg
    ).run(card)
    assert plan.run_config == cfg.model_dump()


def test_per_run_config_overrides_instance_config(card, tools, checklists):
    from wizard.config import RunConfig

    client = ScriptedClient([Proposal(), Proposal(), Review(coverage_ok=True)])
    runner = WizardPlanRunner(
        client=client, tools=tools, checklists=checklists, config=RunConfig()
    )
    per_run = RunConfig(model="claude-sonnet-4-6")
    plan = runner.run(card, config=per_run)
    assert all(c["model"] == "claude-sonnet-4-6" for c in client.calls)
    assert plan.run_config["model"] == "claude-sonnet-4-6"
