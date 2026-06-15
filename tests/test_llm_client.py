"""LiteLLMClient — the provider-agnostic adapter behind the agents.

The agents depend only on `client.messages.parse(...) -> obj.parsed_output`
(a validated pydantic instance). This adapter translates that call onto
`litellm.completion`, so the same agents run on any LiteLLM-supported provider.
Tested with an injected fake `completion` — no network, no real provider.
"""

import json

from wizard.llm_client import LiteLLMClient
from wizard.models.plan import Proposal, Review


class FakeCompletion:
    """Stand-in for litellm.completion: returns a queued JSON payload and
    records the kwargs it was called with."""

    def __init__(self, payloads):
        self._payloads = list(payloads)
        self.calls: list[dict] = []

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        payload = self._payloads.pop(0) if len(self._payloads) > 1 else self._payloads[0]
        content = payload if isinstance(payload, str) else json.dumps(payload)
        message = type("Msg", (), {"content": content})()
        choice = type("Choice", (), {"message": message})()
        return type("Resp", (), {"choices": [choice]})()


PROPOSAL_PAYLOAD = {
    "items": [
        {
            "item_id": "ai-fairness-360",
            "item_type": "test",
            "score": 5,
            "rationale": "r",
            "evidence": [],
            "covers": ["article-10"],
        }
    ],
    "coverage_gaps": [],
}


def _client(payloads):
    fake = FakeCompletion(payloads)
    return LiteLLMClient(completion=fake), fake


class TestParse:
    def test_returns_validated_pydantic(self):
        client, _ = _client([PROPOSAL_PAYLOAD])
        result = client.messages.parse(
            model="openai/gpt-4o-mini",
            max_tokens=16000,
            system="sys",
            messages=[{"role": "user", "content": [{"type": "text", "text": "hi"}]}],
            output_format=Proposal,
        )
        assert isinstance(result.parsed_output, Proposal)
        assert result.parsed_output.items[0].item_id == "ai-fairness-360"

    def test_translates_to_chat_messages_with_system(self):
        client, fake = _client([PROPOSAL_PAYLOAD])
        client.messages.parse(
            model="gemini/gemini-1.5-pro",
            system="SYSTEM RULES",
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "block-A", "cache_control": {"type": "ephemeral"}},
                        {"type": "text", "text": "block-B"},
                    ],
                }
            ],
            output_format=Proposal,
        )
        call = fake.calls[0]
        assert call["model"] == "gemini/gemini-1.5-pro"
        assert call["messages"][0] == {"role": "system", "content": "SYSTEM RULES"}
        # content blocks flattened into one user message (cache_control dropped)
        assert call["messages"][1]["role"] == "user"
        assert "block-A" in call["messages"][1]["content"]
        assert "block-B" in call["messages"][1]["content"]
        # the pydantic schema is handed to litellm for structured output
        assert call["response_format"] is Proposal

    def test_effort_maps_to_reasoning_effort(self):
        client, fake = _client([PROPOSAL_PAYLOAD])
        client.messages.parse(
            model="openai/gpt-4o",
            system="s",
            messages=[{"role": "user", "content": [{"type": "text", "text": "x"}]}],
            output_config={"effort": "high"},
            output_format=Proposal,
        )
        assert fake.calls[0]["reasoning_effort"] == "high"

    def test_anthropic_thinking_kwarg_is_ignored(self):
        client, fake = _client([PROPOSAL_PAYLOAD])
        # the agents always pass thinking=...; the adapter must not choke
        client.messages.parse(
            model="openai/gpt-4o",
            system="s",
            messages=[{"role": "user", "content": [{"type": "text", "text": "x"}]}],
            thinking={"type": "adaptive"},
            output_format=Proposal,
        )
        assert "thinking" not in fake.calls[0]

    def test_strips_markdown_json_fences(self):
        fenced = "```json\n" + json.dumps({"verdicts": [], "coverage_ok": True}) + "\n```"
        client, _ = _client([fenced])
        result = client.messages.parse(
            model="openai/gpt-4o",
            system="s",
            messages=[{"role": "user", "content": [{"type": "text", "text": "x"}]}],
            output_format=Review,
        )
        assert result.parsed_output.coverage_ok is True


class TestAgentCompatibility:
    def test_works_as_agent_client(self, mcas_card, world):
        # the real proposer must accept this client unchanged
        from wizard.agents.llm import LLMTestProposer

        client, _ = _client([PROPOSAL_PAYLOAD])
        proposal = LLMTestProposer(client=client, model="openai/gpt-4o-mini").propose(
            mcas_card, world[0]
        )
        assert isinstance(proposal, Proposal)
        assert proposal.items[0].item_id == "ai-fairness-360"
