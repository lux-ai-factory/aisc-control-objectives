"""LLM-backed Proposer/Reviewer adapters (SPEC §5.2–§5.3).

The client is duck-typed (anything exposing messages.parse) so tests inject a
stub and the `anthropic` package is only required at runtime:

    import anthropic
    from wizard.agents.llm import LLMTestProposer
    proposer = LLMTestProposer(client=anthropic.Anthropic())

v1 design note: candidates are computed deterministically (SPEC §5.1) and
embedded in the prompt, so proposers select rather than search — MCP tool
access from inside the agent loop is a post-v1 extension. The deterministic
ID guard in the orchestrator backstops both adapters.
"""

from __future__ import annotations

import json
from typing import Any

from wizard.config import DEFAULT_MODEL, Lens
from wizard.matching.prefilter import ScoredCandidate
from wizard.models.catalogue import ChecklistDoc
from wizard.models.plan import VERDICT_SEVERITY, ItemVerdict, Proposal, Review
from wizard.models.system_card import SystemCard

MAX_TOKENS = 16000

_PROPOSER_SYSTEM = """You select items for an AI-system assessment plan under the EU AI Act.

Rules:
- You may ONLY cite item ids from the candidate list in the user message. Never invent ids.
- Every open issue of the system card must be covered by at least one selected item, \
or listed in coverage_gaps with a one-line reason.
- Each item needs a rationale tied to verbatim system-card evidence, a priority \
(must/should/optional), and the coverage keys it covers — article keys like \
"article-13", or "open-issue-N" for the card's Nth open issue when that issue \
cites no article.
- Datasets may only be proposed alongside a test that consumes them: every \
dataset item MUST set paired_test_id to the id of that proposed test."""

_REVIEWER_SYSTEM = """You are a quality reviewer for an AI-assessment plan proposal.

For every proposed item return a verdict: accept, revise, or reject.
- reject items whose id is not in the known-ids list (reason: id-not-found)
- reject or revise items whose rationale does not follow from the system card \
(reasons: weak-rationale, wrong-sector, duplicate-coverage, missing-dataset-pairing)
- set coverage_ok=true only if every open issue is covered or explicitly gapped.
Be specific in notes_for_revision; the proposer will act on them verbatim."""


def _candidate_digest(candidates: list[ScoredCandidate]) -> list[dict]:
    digest = []
    for cand in candidates:
        entry: dict[str, Any] = {
            "id": cand.item.slug,
            "name": cand.item.name,
            "description": (cand.item.description or "")[:400],
            "score": cand.score,
            "article_overlap": sorted(cand.article_overlap),
        }
        if isinstance(cand.item, ChecklistDoc):
            entry["control_topic"] = cand.item.control_topic
        else:
            entry["tags"] = sorted(cand.item.tag_slugs)
            entry["ai_type_overlap"] = sorted(cand.ai_type_overlap)
            entry["sector_overlap"] = sorted(cand.sector_overlap)
        digest.append(entry)
    return digest


def _card_digest(card: SystemCard) -> dict:
    return {
        "system_name": card.system_name,
        "system_version": card.system_version,
        "overview": card.overview,
        "target_use_case": card.target_use_case,
        "sectors": sorted(card.sector_slugs),
        "target_systems": sorted(card.target_system_slugs),
        "findings": [f.model_dump() for f in card.findings],
        "open_issues": card.open_issues,
    }


class _LLMAgent:
    def __init__(self, client: Any, model: str = DEFAULT_MODEL):
        self.client = client
        self.model = model

    def _parse(self, system: str, user_payload: dict, schema: type) -> Any:
        response = self.client.messages.parse(
            model=self.model,
            max_tokens=MAX_TOKENS,
            thinking={"type": "adaptive"},
            system=system,
            messages=[{"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)}],
            output_format=schema,
        )
        return response.parsed_output


class _LLMProposerBase(_LLMAgent):
    track: str = ""

    def propose(
        self,
        card: SystemCard,
        candidates: list[ScoredCandidate],
        revision_notes: str | None = None,
        prior: Proposal | None = None,
    ) -> Proposal:
        payload: dict[str, Any] = {
            "task": f"Propose {self.track} for the assessment plan of this system.",
            "system_card": _card_digest(card),
            "candidates": _candidate_digest(candidates),
        }
        if prior is not None:
            payload["prior_proposal"] = prior.model_dump()
        if revision_notes:
            payload["revision_notes"] = revision_notes
        return self._parse(_PROPOSER_SYSTEM, payload, Proposal)


class LLMTestProposer(_LLMProposerBase):
    track = "tests and datasets"


class LLMChecklistProposer(_LLMProposerBase):
    track = "control checklists"


class LLMReviewer(_LLMAgent):
    def __init__(
        self,
        client: Any,
        known_item_ids: set[str],
        model: str = DEFAULT_MODEL,
        system_suffix: str = "",
    ):
        super().__init__(client, model)
        self.known_item_ids = known_item_ids
        self.system_suffix = system_suffix

    def review(self, card: SystemCard, proposal: Proposal) -> Review:
        payload = {
            "task": "Review this proposal for quality and coverage.",
            "system_card": _card_digest(card),
            "known_ids": sorted(self.known_item_ids),
            "proposal": proposal.model_dump(),
        }
        system = _REVIEWER_SYSTEM + ("\n\n" + self.system_suffix if self.system_suffix else "")
        return self._parse(system, payload, Review)


_LENS_PROMPTS: dict[str, str] = {
    "relevance": (
        "Your lens: RELEVANCE. Judge above all whether each item is genuinely "
        "warranted by THIS system card — its sector, its AI types, its actual "
        "risk profile. Flag items that merely sound plausible."
    ),
    "coverage": (
        "Your lens: COVERAGE. Judge above all whether the proposal addresses "
        "every open issue and every finding article. Hunt for what is missing, "
        "not what is wrong."
    ),
    "parsimony": (
        "Your lens: PARSIMONY. Judge above all whether the plan is "
        "proportionate: flag redundant items, overlapping coverage, and "
        "anything disproportionate to the system's risk profile."
    ),
}

class MultiLensReviewer:
    """Runs one reviewer per lens (separate contexts) and merges deterministically:
    per item the WORST verdict wins, coverage_ok is the AND of all lenses, and
    notes are concatenated with a [lens] label (SPEC_HARDENING A2)."""

    def __init__(
        self,
        client: Any,
        known_item_ids: set[str],
        lenses: list[Lens],
        model: str = DEFAULT_MODEL,
    ):
        self.lenses = list(lenses)
        self._reviewers = [
            LLMReviewer(
                client=client,
                known_item_ids=known_item_ids,
                model=model,
                system_suffix=_LENS_PROMPTS[lens],
            )
            for lens in self.lenses
        ]

    def review(self, card: SystemCard, proposal: Proposal) -> Review:
        reviews = [r.review(card, proposal) for r in self._reviewers]

        merged_verdicts: dict[str, ItemVerdict] = {}
        for review in reviews:
            for verdict in review.verdicts:
                current = merged_verdicts.get(verdict.item_id)
                if current is None or VERDICT_SEVERITY[verdict.verdict] > VERDICT_SEVERITY[current.verdict]:
                    merged_verdicts[verdict.item_id] = verdict.model_copy()
                elif VERDICT_SEVERITY[verdict.verdict] == VERDICT_SEVERITY[current.verdict]:
                    current.reasons = list(dict.fromkeys(current.reasons + verdict.reasons))

        notes = "\n".join(
            f"[{lens}] {review.notes_for_revision}"
            for lens, review in zip(self.lenses, reviews)
            if review.notes_for_revision
        )
        return Review(
            verdicts=list(merged_verdicts.values()),
            coverage_ok=all(r.coverage_ok for r in reviews),
            notes_for_revision=notes,
        )
