"""LLM-backed Proposer/Reviewer adapters (SPEC §5.2–§5.3).

The client is duck-typed (anything exposing messages.parse) so tests inject a
stub. In production it is the LiteLLM-backed client, so any provider works:

    from wizard.llm_client import LiteLLMClient
    from wizard.agents.llm import LLMTestProposer
    proposer = LLMTestProposer(client=LiteLLMClient(), model="openai/gpt-4o-mini")

v1 design note: candidates are computed deterministically (SPEC §5.1) and
embedded in the prompt, so proposers select rather than search — MCP tool
access from inside the agent loop is a post-v1 extension. The deterministic
ID guard in the orchestrator backstops both adapters.
"""

from __future__ import annotations

import json
from typing import Any

from wizard.config import DEFAULT_MODEL, Lens
from wizard.dimensions import label_for
from wizard.llm_client import LLMClient
from wizard.matching.prefilter import ScoredCandidate
from wizard.matching.tag_map import map_system_card_tags
from wizard.models.catalogue import ChecklistDoc
from wizard.models.plan import DimensionFraming, Proposal, Review, merge_verdicts
from wizard.models.system_card import SystemCard
from wizard.skills import SkillsLoader

MAX_TOKENS = 16000

_PROPOSER_SYSTEM = """You select items for an AI-system assessment plan under the EU AI Act.

Rules:
- You may ONLY cite item ids from the candidate list in the user message. Never invent ids.
- Never propose the same item id more than once; merge what it covers into a single entry.
- Be inclusive: propose EVERY candidate that is genuinely relevant to this system, \
each with a recommendation `score` from 1 to 5 (5 = strongly recommended / clearly \
warranted, 3 = useful, 1 = weakly relevant / nice-to-have). Simply OMIT candidates \
that are not relevant (an omitted item scores 0). Do not withhold a relevant item \
just to keep the list short — rank with the score instead.
- Every open issue of the system card must be covered by at least one selected item, \
or listed in coverage_gaps with a one-line reason.
- Each item needs a rationale tied to verbatim system-card evidence, its `score`, and \
the coverage keys it covers. For `covers` you MUST use ONLY keys from \
`system_card.coverage_keys` (the exact valid keys for THIS card) — do not invent keys \
and do not guess "open-issue-N" forms; if unsure, cover fewer keys.
- Datasets may only be proposed alongside a test that consumes them: every \
dataset item MUST set paired_test_id to the id of that proposed test."""

_FRAMER_SYSTEM = """You frame an EU-AI-Act assessment by trustworthiness dimension.

You are given a system card and the candidate trustworthiness dimensions. For \
each dimension decide, conditioned on the system's TECHNOLOGY and CONTEXT:
- in_scope: whether the dimension is relevant to THIS system at all. A dimension \
is out of scope only when the technology/use-case genuinely cannot raise it.
- relevance_reason: one line tying scope to the system's technology/sector.
- already_addressed: what the card already does for this dimension, each backed \
by verbatim card evidence. Do not invent mitigations the card does not describe.
- residual_gaps: what still needs verifying or controlling AFTER crediting what \
is already addressed. Leave empty when the card already covers the dimension \
adequately — do not manufacture a gap just to recommend something.

Condition on technology: NLP/LLM systems lean prompt-injection/jailbreak/toxicity \
under robustness; tabular/predictive systems lean drift/calibration; agentic \
systems raise excessive-agency and oversight. A high-risk sector tightens \
oversight, transparency, and record-keeping expectations."""

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
        # the exact set of keys an item's `covers` may use for THIS card —
        # article/annex keys plus the synthetic open-issue-N keys for any
        # article-less open issue. Enumerated so the proposer can't invent keys.
        "coverage_keys": sorted(card.article_keys()),
    }


def _card_ai_types_sectors(card: SystemCard) -> tuple[set[str], set[str]]:
    mapped = map_system_card_tags(
        sorted(card.target_system_slugs), sorted(card.sector_slugs)
    )
    return mapped.ai_type_slugs, mapped.sector_slugs


class _LLMAgent:
    def __init__(
        self,
        client: LLMClient,
        model: str = DEFAULT_MODEL,
        skills: SkillsLoader | None = None,
    ):
        self.client = client
        self.model = model
        self.skills = skills

    def _skill_bodies(self, card: SystemCard, dimensions: list[str]) -> list[str]:
        """Guidance from skills matching this system's dimensions + technology.
        Each entry is the skill body, prefixed with its (advisory) `applies_when`
        so the model knows when the guidance bites. Empty — and omitted from the
        prompt — when no loader is configured, so the unskilled path is
        byte-identical and the prompt cache is preserved."""
        if not self.skills:
            return []
        ai_types, sectors = _card_ai_types_sectors(card)
        bodies = []
        for s in self.skills.select(dimensions, ai_types, sectors):
            if s.applies_when:
                bodies.append(f"Applies when: {s.applies_when}\n\n{s.body}")
            else:
                bodies.append(s.body)
        return bodies

    def _parse(
        self,
        system: str,
        stable_payload: dict,
        volatile_payload: dict | None,
        schema: type,
    ) -> Any:
        """Two-block prompt for cache reuse (SPEC §5.4): the stable block
        (card + candidates / known ids) carries the cache breakpoint and is
        byte-identical across review rounds and lenses; per-round material
        (prior proposal, revision notes, lens instruction) goes in the tail,
        after the breakpoint."""
        content: list[dict] = [
            {
                "type": "text",
                "text": json.dumps(stable_payload, ensure_ascii=False),
                "cache_control": {"type": "ephemeral"},
            }
        ]
        if volatile_payload:
            content.append(
                {"type": "text", "text": json.dumps(volatile_payload, ensure_ascii=False)}
            )
        response = self.client.messages.parse(
            model=self.model,
            max_tokens=MAX_TOKENS,
            thinking={"type": "adaptive"},
            output_config={"effort": "high"},
            system=system,
            messages=[{"role": "user", "content": content}],
            output_format=schema,
        )
        return response.parsed_output


class LLMDimensionFramer(_LLMAgent):
    """Framing stage (Phase 1): one LLM call that decides, per candidate
    dimension, whether it is in scope, what the card already addresses, and
    what residual gap remains — conditioned on technology + context. Wraps the
    propose→review loop; it does not select items itself."""

    def frame(
        self, card: SystemCard, dimension_slugs: list[str]
    ) -> DimensionFraming:
        stable: dict[str, Any] = {
            "task": (
                "Frame this system's assessment by trustworthiness dimension. "
                "Assess only the dimensions listed; do not invent others."
            ),
            "system_card": _card_digest(card),
            "dimensions": [
                {"slug": slug, "label": label_for(slug)} for slug in dimension_slugs
            ],
        }
        skills = self._skill_bodies(card, dimension_slugs)
        if skills:
            stable["skills"] = skills
        return self._parse(_FRAMER_SYSTEM, stable, None, DimensionFraming)


class _LLMProposerBase(_LLMAgent):
    track: str = ""

    def propose(
        self,
        card: SystemCard,
        candidates: list[ScoredCandidate],
        revision_notes: str | None = None,
        prior: Proposal | None = None,
    ) -> Proposal:
        stable: dict[str, Any] = {
            "task": f"Propose {self.track} for the assessment plan of this system.",
            "system_card": _card_digest(card),
            "candidates": _candidate_digest(candidates),
        }
        cand_dims = sorted(
            {dim for c in candidates for dim in c.item.dimension_slugs()}
        )
        skills = self._skill_bodies(card, cand_dims)
        if skills:
            stable["skills"] = skills
        volatile: dict[str, Any] = {}
        if prior is not None:
            volatile["prior_proposal"] = prior.model_dump()
        if revision_notes:
            volatile["revision_notes"] = revision_notes
        return self._parse(_PROPOSER_SYSTEM, stable, volatile or None, Proposal)


class LLMTestProposer(_LLMProposerBase):
    track = "tests and datasets"


class LLMChecklistProposer(_LLMProposerBase):
    track = "control checklists"


class LLMReviewer(_LLMAgent):
    def __init__(
        self,
        client: LLMClient,
        known_item_ids: set[str],
        model: str = DEFAULT_MODEL,
        lens_instruction: str = "",
    ):
        super().__init__(client, model)
        self.known_item_ids = known_item_ids
        # goes in the volatile tail, NOT the system prompt: the system and
        # stable block stay byte-identical across lenses, so multi-lens
        # reviews share one cached prefix instead of three full-price reads
        self.lens_instruction = lens_instruction

    def review(self, card: SystemCard, proposal: Proposal) -> Review:
        stable = {
            "task": "Review this proposal for quality and coverage.",
            "system_card": _card_digest(card),
            "known_ids": sorted(self.known_item_ids),
        }
        volatile: dict[str, Any] = {"proposal": proposal.model_dump()}
        if self.lens_instruction:
            volatile["lens_instruction"] = self.lens_instruction
        return self._parse(_REVIEWER_SYSTEM, stable, volatile, Review)


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
        client: LLMClient,
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
                lens_instruction=_LENS_PROMPTS[lens],
            )
            for lens in self.lenses
        ]

    def review(self, card: SystemCard, proposal: Proposal) -> Review:
        reviews = [r.review(card, proposal) for r in self._reviewers]
        merged_verdicts = merge_verdicts(
            [v for review in reviews for v in review.verdicts]
        )
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
