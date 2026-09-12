"""LiteLLMClient — the provider-agnostic adapter.

Callers depend only on `client.messages.parse(...) -> obj.parsed_output`
(a validated pydantic instance). This adapter translates that call onto
`litellm.completion`, so the same caller runs on any LiteLLM-supported
provider. Tested with an injected fake `completion` — no network, no real
provider.

The schemas below are local test doubles: the adapter is schema-agnostic, so
it is tested against shapes it does not own.
"""

import json

from pydantic import BaseModel, Field

from wizard.llm_client import LiteLLMClient


class Item(BaseModel):
    item_id: str
    item_type: str = ""
    score: int = 0
    rationale: str = ""
    evidence: list[str] = Field(default_factory=list)
    covers: list[str] = Field(default_factory=list)


class Proposal(BaseModel):
    items: list[Item] = Field(default_factory=list)
    coverage_gaps: list[str] = Field(default_factory=list)


class Frame(BaseModel):
    dimension_slug: str


class DimensionFraming(BaseModel):
    """Single-field schema — the shape a provider tends to stringify whole."""

    frames: list[Frame] = Field(default_factory=list)


class Review(BaseModel):
    verdicts: list[str] = Field(default_factory=list)
    coverage_ok: bool = False


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

    def test_every_provider_kwarg_reaches_completion(self):
        """No allowlist: a param a caller passes is forwarded, and LiteLLM's
        drop_params decides what a given provider cannot take. An allowlist is
        how `temperature` came to be silently swallowed."""
        client, fake = _client([PROPOSAL_PAYLOAD])
        client.messages.parse(
            model="openai/gpt-4o",
            system="s",
            messages=[{"role": "user", "content": [{"type": "text", "text": "x"}]}],
            output_format=Proposal,
            max_tokens=16000,
            seed=7,
        )
        assert fake.calls[0]["max_tokens"] == 16000
        assert fake.calls[0]["seed"] == 7

    def test_temperature_reaches_the_provider(self):
        client, fake = _client([PROPOSAL_PAYLOAD])
        client.messages.parse(
            model="openai/gpt-4o",
            system="s",
            messages=[{"role": "user", "content": [{"type": "text", "text": "x"}]}],
            output_format=Proposal,
            temperature=0,
        )
        assert fake.calls[0]["temperature"] == 0

    def test_empty_content_is_a_named_error_not_a_json_stack(self):
        class Empty:
            def __call__(self, **kwargs):
                message = type("Msg", (), {"content": None})()
                choice = type("Choice", (), {"message": message})()
                return type("Resp", (), {"choices": [choice]})()

        client = LiteLLMClient(completion=Empty())
        import pytest

        with pytest.raises(ValueError, match="no content"):
            client.messages.parse(
                model="m", system="s",
                messages=[{"role": "user", "content": [{"type": "text", "text": "x"}]}],
                output_format=Proposal,
            )

    def test_recovers_when_provider_wraps_whole_payload_as_string(self):
        # Observed with Claude via LiteLLM tool-use: the model returns the whole
        # object stringified under the schema's own single field name, i.e.
        # {"frames": "<json of the entire DimensionFraming>"}. Naive validation
        # sees frames as a string and rejects it; the adapter must recover.
        inner = json.dumps({"frames": [{"dimension_slug": "fairness"}]})
        content = json.dumps({"frames": inner})
        client, _ = _client([content])
        result = client.messages.parse(
            model="anthropic/claude-opus-4-8",
            system="s",
            messages=[{"role": "user", "content": [{"type": "text", "text": "x"}]}],
            output_format=DimensionFraming,
        )
        assert isinstance(result.parsed_output, DimensionFraming)
        assert result.parsed_output.frames[0].dimension_slug == "fairness"

    def test_recovers_when_list_field_arrives_as_json_string(self):
        # Related malformation: a list-valued field comes back as a JSON string
        # ({"frames": "[...]"}) rather than a real array.
        content = json.dumps({"frames": json.dumps([{"dimension_slug": "privacy"}])})
        client, _ = _client([content])
        result = client.messages.parse(
            model="anthropic/claude-opus-4-8",
            system="s",
            messages=[{"role": "user", "content": [{"type": "text", "text": "x"}]}],
            output_format=DimensionFraming,
        )
        assert isinstance(result.parsed_output, DimensionFraming)
        assert result.parsed_output.frames[0].dimension_slug == "privacy"

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
