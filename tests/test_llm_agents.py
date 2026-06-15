"""LLM-backed Proposer/Reviewer adapters, tested with a stubbed client.

The adapters must: select only from the candidate set given in the prompt,
carry revision notes into follow-up rounds, request structured outputs with
the right schema, and use the configured model with adaptive thinking.
No network, no real anthropic client.
"""

import json

from helpers import ScriptedClient

from wizard.agents.llm import (
    LLMChecklistProposer,
    LLMDimensionFramer,
    LLMReviewer,
    LLMTestProposer,
    MultiLensReviewer,
)
from wizard.models.plan import (
    DimensionFrame,
    DimensionFraming,
    ItemVerdict,
    Proposal,
    ProposedItem,
    Review,
)

SAMPLE_PROPOSAL = Proposal(
    items=[
        ProposedItem(
            item_id="ai-fairness-360",
            item_type="test",
            score=5,
            rationale="r",
            evidence=["e"],
            covers=["article-10"],
        )
    ]
)

SAMPLE_REVIEW = Review(verdicts=[], coverage_ok=True, notes_for_revision="")


class TestLLMTestProposer:
    def test_returns_parsed_proposal(self, mcas_card, world):
        client = ScriptedClient([SAMPLE_PROPOSAL])
        result = LLMTestProposer(client=client).propose(mcas_card, world[0])
        assert isinstance(result, Proposal)
        assert result.items[0].item_id == "ai-fairness-360"

    def test_request_uses_model_thinking_and_schema(self, mcas_card, world):
        client = ScriptedClient([SAMPLE_PROPOSAL])
        LLMTestProposer(client=client, model="claude-opus-4-8").propose(mcas_card, world[0])
        call = client.calls[0]
        assert call["model"] == "claude-opus-4-8"
        assert call["thinking"] == {"type": "adaptive"}
        assert call["output_config"] == {"effort": "high"}
        assert call["output_format"] is Proposal
        assert call["max_tokens"] >= 16000

    def test_stable_prefix_cached_and_identical_across_rounds(self, mcas_card, world):
        """SPEC §5.4: card+candidates sit in a cache_control block whose bytes
        do not change between rounds; per-round material goes in the tail."""
        client = ScriptedClient([SAMPLE_PROPOSAL, SAMPLE_PROPOSAL])
        proposer = LLMTestProposer(client=client)
        proposer.propose(mcas_card, world[0])
        proposer.propose(
            mcas_card, world[0], revision_notes="Cover Article 14.", prior=SAMPLE_PROPOSAL
        )

        def blocks(call):
            return call["messages"][0]["content"]

        round1, round2 = blocks(client.calls[0]), blocks(client.calls[1])
        assert round1[0]["cache_control"] == {"type": "ephemeral"}
        # stable block byte-identical across rounds → cache hit on round 2
        assert round1[0]["text"] == round2[0]["text"]
        # volatile tail carries the revision material
        assert "Cover Article 14." in round2[-1]["text"]

    def test_prompt_contains_candidates_and_open_issues(self, mcas_card, world):
        client = ScriptedClient([SAMPLE_PROPOSAL])
        LLMTestProposer(client=client).propose(mcas_card, world[0])
        prompt = json.dumps(client.calls[0]["messages"])
        # every candidate id must be offered to the model
        for cand in world[0]:
            assert cand.item.slug in prompt
        # open issues are the primary matching signal (SPEC §2.1)
        assert "kill-switch" in prompt  # distinctive fragment of MCAS open issue 2

    def test_revision_notes_and_prior_included_when_given(self, mcas_card, world):
        client = ScriptedClient([SAMPLE_PROPOSAL])
        LLMTestProposer(client=client).propose(
            mcas_card,
            world[0],
            revision_notes="Cover Article 14 explicitly.",
            prior=SAMPLE_PROPOSAL,
        )
        prompt = json.dumps(client.calls[0]["messages"])
        assert "Cover Article 14 explicitly." in prompt
        assert "ai-fairness-360" in prompt  # prior proposal echoed

    def test_system_prompt_pins_selection_rules(self, mcas_card, world):
        client = ScriptedClient([SAMPLE_PROPOSAL])
        LLMTestProposer(client=client).propose(mcas_card, world[0])
        system = client.calls[0]["system"]
        assert "only" in system.lower()  # only candidate ids may be cited

    def test_prompt_enumerates_valid_coverage_keys(self, mcas_card, world):
        # the proposer must be given the exact valid coverage keys (so it can't
        # invent open-issue-N keys the coverage guard would strip)
        client = ScriptedClient([SAMPLE_PROPOSAL])
        LLMTestProposer(client=client).propose(mcas_card, world[0])
        stable = client.calls[0]["messages"][0]["content"][0]["text"]
        assert "coverage_keys" in stable
        for key in mcas_card.article_keys():
            assert key in stable
        # and the system prompt points the model at that list
        assert "coverage_keys" in client.calls[0]["system"]


SAMPLE_FRAMING = DimensionFraming(
    frames=[
        DimensionFrame(
            dimension_slug="technical-robustness-safety",
            in_scope=True,
            relevance_reason="agentic system exposed to untrusted tool output",
            residual_gaps=["no prompt-injection benchmark"],
        )
    ]
)


class TestLLMDimensionFramer:
    def test_returns_parsed_framing(self, mcas_card):
        client = ScriptedClient([SAMPLE_FRAMING])
        framer = LLMDimensionFramer(client=client)
        result = framer.frame(
            mcas_card, ["technical-robustness-safety", "transparency"]
        )
        assert isinstance(result, DimensionFraming)
        assert result.frames[0].dimension_slug == "technical-robustness-safety"

    def test_request_schema_model_and_thinking(self, mcas_card):
        client = ScriptedClient([SAMPLE_FRAMING])
        LLMDimensionFramer(client=client, model="claude-opus-4-8").frame(
            mcas_card, ["transparency"]
        )
        call = client.calls[0]
        assert call["model"] == "claude-opus-4-8"
        assert call["output_format"] is DimensionFraming
        assert call["thinking"] == {"type": "adaptive"}

    def test_prompt_carries_dimensions_with_labels_and_technology(self, mcas_card):
        client = ScriptedClient([SAMPLE_FRAMING])
        LLMDimensionFramer(client=client).frame(
            mcas_card, ["technical-robustness-safety", "transparency"]
        )
        prompt = json.dumps(client.calls[0]["messages"])
        # slug + human label both offered so the model reasons over named dims
        assert "technical-robustness-safety" in prompt
        assert "Technical Robustness and Safety" in prompt
        # technology conditioning: the card's target systems are in the prompt
        assert "target_systems" in prompt
        # system prompt instructs not to recommend for already-covered dims
        system = client.calls[0]["system"].lower()
        assert "scope" in system

    def test_stable_block_cached(self, mcas_card):
        client = ScriptedClient([SAMPLE_FRAMING])
        LLMDimensionFramer(client=client).frame(mcas_card, ["transparency"])
        content = client.calls[0]["messages"][0]["content"]
        assert content[0]["cache_control"] == {"type": "ephemeral"}

    def test_no_skills_key_when_loader_absent(self, mcas_card):
        client = ScriptedClient([SAMPLE_FRAMING])
        LLMDimensionFramer(client=client).frame(mcas_card, ["transparency"])
        assert "skills" not in client.calls[0]["messages"][0]["content"][0]["text"]

    def test_matching_skill_injected_into_stable_block(self, mcas_card):
        from pathlib import Path

        from wizard.skills import SkillsLoader

        loader = SkillsLoader.from_dir(
            Path(__file__).parent / "fixtures" / "skills"
        )
        client = ScriptedClient([SAMPLE_FRAMING])
        # MCAS is NLP + finance → robustness skill matches
        LLMDimensionFramer(client=client, skills=loader).frame(
            mcas_card, ["technical-robustness-safety", "transparency"]
        )
        stable = client.calls[0]["messages"][0]["content"][0]
        assert "prompt-injection" in stable["text"]
        # injected into the CACHED stable block, not the volatile tail
        assert stable["cache_control"] == {"type": "ephemeral"}


class TestLLMChecklistProposer:
    def test_offers_checklist_candidates(self, mcas_card, world):
        client = ScriptedClient([SAMPLE_PROPOSAL])
        LLMChecklistProposer(client=client).propose(mcas_card, world[1])
        prompt = json.dumps(client.calls[0]["messages"])
        assert "Transparency_Checklist" in prompt


class TestLLMReviewer:
    def test_returns_parsed_review(self, mcas_card):
        client = ScriptedClient([SAMPLE_REVIEW])
        reviewer = LLMReviewer(client=client, known_item_ids={"ai-fairness-360"})
        result = reviewer.review(mcas_card, SAMPLE_PROPOSAL)
        assert isinstance(result, Review)
        assert result.coverage_ok

    def test_review_request_schema_and_known_ids(self, mcas_card):
        client = ScriptedClient([SAMPLE_REVIEW])
        LLMReviewer(client=client, known_item_ids={"id-a", "id-b"}).review(
            mcas_card, SAMPLE_PROPOSAL
        )
        call = client.calls[0]
        assert call["output_format"] is Review
        prompt = json.dumps(call["messages"])
        assert "id-a" in prompt and "id-b" in prompt


class TestMultiLensReviewer:
    def _reviews(self):
        relevance = Review(
            verdicts=[ItemVerdict(item_id="x", verdict="accept")],
            coverage_ok=True,
        )
        coverage = Review(
            verdicts=[ItemVerdict(item_id="x", verdict="revise", reasons=["weak-coverage"])],
            coverage_ok=False,
            notes_for_revision="cover article 14",
        )
        parsimony = Review(verdicts=[], coverage_ok=True)  # omits item x → accept
        return relevance, coverage, parsimony

    def test_one_call_per_lens_sharing_one_cached_prefix(self, mcas_card):
        """The lens instruction lives in the volatile user tail, NOT the system
        prompt — so all three lens calls share one cached prefix (system and
        stable block identical) instead of each paying full price."""
        client = ScriptedClient(list(self._reviews()))
        MultiLensReviewer(
            client=client,
            known_item_ids={"x"},
            lenses=["relevance", "coverage", "parsimony"],
        ).review(mcas_card, SAMPLE_PROPOSAL)
        assert len(client.calls) == 3
        systems = [c["system"] for c in client.calls]
        assert len(set(systems)) == 1  # byte-identical → shared cache prefix
        stable_blocks = [c["messages"][0]["content"][0]["text"] for c in client.calls]
        assert len(set(stable_blocks)) == 1
        tails = [c["messages"][0]["content"][-1]["text"] for c in client.calls]
        assert "RELEVANCE" in tails[0]
        assert "COVERAGE" in tails[1]
        assert "PARSIMONY" in tails[2]

    def test_merge_worst_verdict_and_anded_coverage(self, mcas_card):
        client = ScriptedClient(list(self._reviews()))
        merged = MultiLensReviewer(
            client=client,
            known_item_ids={"x"},
            lenses=["relevance", "coverage", "parsimony"],
        ).review(mcas_card, SAMPLE_PROPOSAL)

        [verdict] = merged.verdicts
        assert verdict.item_id == "x"
        assert verdict.verdict == "revise"  # worst across accept/revise/(omitted)
        assert not merged.coverage_ok  # AND of True/False/True
        assert "[coverage]" in merged.notes_for_revision
        assert "cover article 14" in merged.notes_for_revision

    def test_reject_beats_revise(self, mcas_card):
        reviews = [
            Review(verdicts=[ItemVerdict(item_id="x", verdict="revise")], coverage_ok=True),
            Review(
                verdicts=[ItemVerdict(item_id="x", verdict="reject", reasons=["wrong-sector"])],
                coverage_ok=True,
            ),
        ]
        client = ScriptedClient(reviews)
        merged = MultiLensReviewer(
            client=client, known_item_ids={"x"}, lenses=["relevance", "parsimony"]
        ).review(mcas_card, SAMPLE_PROPOSAL)
        assert merged.verdicts[0].verdict == "reject"
        assert "wrong-sector" in merged.verdicts[0].reasons
