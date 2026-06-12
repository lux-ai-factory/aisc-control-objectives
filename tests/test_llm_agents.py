"""LLM-backed Proposer/Reviewer adapters, tested with a stubbed client.

The adapters must: select only from the candidate set given in the prompt,
carry revision notes into follow-up rounds, request structured outputs with
the right schema, and use the configured model with adaptive thinking.
No network, no real anthropic client.
"""

import json
from pathlib import Path

import pytest

from wizard.agents.llm import LLMChecklistProposer, LLMReviewer, LLMTestProposer
from wizard.matching.prefilter import prefilter_checklists, prefilter_tools
from wizard.models.catalogue import CatalogueTool, ChecklistDoc
from wizard.models.plan import Proposal, ProposedItem, Review
from wizard.models.system_card import SystemCard

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="module")
def mcas() -> SystemCard:
    return SystemCard.from_card_json(
        json.loads((FIXTURES / "mcas_system_card.json").read_text())
    )


@pytest.fixture(scope="module")
def test_candidates(mcas):
    tools = [
        CatalogueTool.from_seed(t)
        for t in json.loads((FIXTURES / "tools_seed.json").read_text())
    ]
    return prefilter_tools(mcas, tools)


@pytest.fixture(scope="module")
def checklist_candidates(mcas):
    checklists = [
        ChecklistDoc.from_seed(c)
        for c in json.loads((FIXTURES / "controls_seed.json").read_text())
    ]
    return prefilter_checklists(mcas, checklists)


class StubParsed:
    def __init__(self, output):
        self.parsed_output = output


class StubMessages:
    def __init__(self, output):
        self._output = output
        self.calls: list[dict] = []

    def parse(self, **kwargs):
        self.calls.append(kwargs)
        return StubParsed(self._output)


class StubClient:
    def __init__(self, output):
        self.messages = StubMessages(output)


SAMPLE_PROPOSAL = Proposal(
    items=[
        ProposedItem(
            item_id="ai-fairness-360",
            item_type="test",
            priority="must",
            rationale="r",
            evidence=["e"],
            covers=["article-10"],
        )
    ]
)

SAMPLE_REVIEW = Review(verdicts=[], coverage_ok=True, notes_for_revision="")


class TestLLMTestProposer:
    def test_returns_parsed_proposal(self, mcas, test_candidates):
        client = StubClient(SAMPLE_PROPOSAL)
        proposer = LLMTestProposer(client=client)
        result = proposer.propose(mcas, test_candidates)
        assert isinstance(result, Proposal)
        assert result.items[0].item_id == "ai-fairness-360"

    def test_request_uses_model_thinking_and_schema(self, mcas, test_candidates):
        client = StubClient(SAMPLE_PROPOSAL)
        LLMTestProposer(client=client, model="claude-opus-4-8").propose(
            mcas, test_candidates
        )
        call = client.messages.calls[0]
        assert call["model"] == "claude-opus-4-8"
        assert call["thinking"] == {"type": "adaptive"}
        assert call["output_format"] is Proposal
        assert call["max_tokens"] >= 16000

    def test_prompt_contains_candidates_and_open_issues(self, mcas, test_candidates):
        client = StubClient(SAMPLE_PROPOSAL)
        LLMTestProposer(client=client).propose(mcas, test_candidates)
        call = client.messages.calls[0]
        prompt = json.dumps(call["messages"])
        # every candidate id must be offered to the model
        for cand in test_candidates:
            assert cand.item.slug in prompt
        # open issues are the primary matching signal (SPEC §2.1)
        assert "kill-switch" in prompt  # distinctive fragment of MCAS open issue 2

    def test_revision_notes_and_prior_included_when_given(self, mcas, test_candidates):
        client = StubClient(SAMPLE_PROPOSAL)
        proposer = LLMTestProposer(client=client)
        proposer.propose(
            mcas,
            test_candidates,
            revision_notes="Cover Article 14 explicitly.",
            prior=SAMPLE_PROPOSAL,
        )
        prompt = json.dumps(client.messages.calls[0]["messages"])
        assert "Cover Article 14 explicitly." in prompt
        assert "ai-fairness-360" in prompt  # prior proposal echoed

    def test_system_prompt_pins_selection_rules(self, mcas, test_candidates):
        client = StubClient(SAMPLE_PROPOSAL)
        LLMTestProposer(client=client).propose(mcas, test_candidates)
        system = client.messages.calls[0]["system"]
        assert "only" in system.lower()  # only candidate ids may be cited


class TestLLMChecklistProposer:
    def test_offers_checklist_candidates(self, mcas, checklist_candidates):
        client = StubClient(SAMPLE_PROPOSAL)
        LLMChecklistProposer(client=client).propose(mcas, checklist_candidates)
        prompt = json.dumps(client.messages.calls[0]["messages"])
        assert "Transparency_Checklist" in prompt


class TestLLMReviewer:
    def test_returns_parsed_review(self, mcas):
        client = StubClient(SAMPLE_REVIEW)
        reviewer = LLMReviewer(client=client, known_item_ids={"ai-fairness-360"})
        result = reviewer.review(mcas, SAMPLE_PROPOSAL)
        assert isinstance(result, Review)
        assert result.coverage_ok

    def test_review_request_schema_and_known_ids(self, mcas):
        client = StubClient(SAMPLE_REVIEW)
        LLMReviewer(client=client, known_item_ids={"id-a", "id-b"}).review(
            mcas, SAMPLE_PROPOSAL
        )
        call = client.messages.calls[0]
        assert call["output_format"] is Review
        prompt = json.dumps(call["messages"])
        assert "id-a" in prompt and "id-b" in prompt


class QueueClient:
    """messages.parse stub returning queued outputs in order."""

    def __init__(self, outputs):
        self._outputs = list(outputs)
        self.calls = []
        self.messages = self

    def parse(self, **kwargs):
        self.calls.append(kwargs)
        return type("P", (), {"parsed_output": self._outputs.pop(0)})()


class TestMultiLensReviewer:
    def _reviews(self):
        from wizard.models.plan import ItemVerdict

        relevance = Review(
            verdicts=[ItemVerdict(item_id="x", verdict="accept")],
            coverage_ok=True,
            notes_for_revision="",
        )
        coverage = Review(
            verdicts=[ItemVerdict(item_id="x", verdict="revise", reasons=["weak-coverage"])],
            coverage_ok=False,
            notes_for_revision="cover article 14",
        )
        parsimony = Review(
            verdicts=[],  # omits item x → counts as accept
            coverage_ok=True,
            notes_for_revision="",
        )
        return relevance, coverage, parsimony

    def test_one_call_per_lens_with_distinct_prompts(self, mcas):
        from wizard.agents.llm import MultiLensReviewer

        client = QueueClient(list(self._reviews()))
        MultiLensReviewer(
            client=client,
            known_item_ids={"x"},
            lenses=["relevance", "coverage", "parsimony"],
        ).review(mcas, SAMPLE_PROPOSAL)
        assert len(client.calls) == 3
        systems = [c["system"] for c in client.calls]
        assert len(set(systems)) == 3  # each lens gets its own framing

    def test_merge_worst_verdict_and_anded_coverage(self, mcas):
        from wizard.agents.llm import MultiLensReviewer

        client = QueueClient(list(self._reviews()))
        merged = MultiLensReviewer(
            client=client,
            known_item_ids={"x"},
            lenses=["relevance", "coverage", "parsimony"],
        ).review(mcas, SAMPLE_PROPOSAL)

        [verdict] = merged.verdicts
        assert verdict.item_id == "x"
        assert verdict.verdict == "revise"  # worst across accept/revise/(omitted)
        assert not merged.coverage_ok  # AND of True/False/True
        assert "[coverage]" in merged.notes_for_revision
        assert "cover article 14" in merged.notes_for_revision

    def test_reject_beats_revise(self, mcas):
        from wizard.agents.llm import MultiLensReviewer
        from wizard.models.plan import ItemVerdict

        reviews = [
            Review(verdicts=[ItemVerdict(item_id="x", verdict="revise")], coverage_ok=True),
            Review(verdicts=[ItemVerdict(item_id="x", verdict="reject", reasons=["wrong-sector"])], coverage_ok=True),
        ]
        client = QueueClient(reviews)
        merged = MultiLensReviewer(
            client=client, known_item_ids={"x"}, lenses=["relevance", "parsimony"]
        ).review(mcas, SAMPLE_PROPOSAL)
        assert merged.verdicts[0].verdict == "reject"
        assert "wrong-sector" in merged.verdicts[0].reasons
