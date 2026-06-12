"""Deterministic candidate scoring (SPEC §5.1).

score = w_ai * |ai_type overlap| + w_sector * |sector overlap|
      + w_article * |article-key overlap|

Tools below score 0 are dropped; checklists are *all* kept (the set is small
and horizontal checklists with no article overlap can still be mandatory —
that judgment belongs to the proposer agent, not the prefilter), ranked so
agents see the signal.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from wizard.matching.tag_map import map_system_card_tags
from wizard.models.catalogue import CatalogueTool, ChecklistDoc
from wizard.models.system_card import SystemCard


@dataclass(frozen=True)
class Weights:
    ai_type: float = 3.0
    sector: float = 2.0
    article: float = 2.0


@dataclass
class ScoredCandidate:
    item: CatalogueTool | ChecklistDoc
    score: float
    ai_type_overlap: set[str] = field(default_factory=set)
    sector_overlap: set[str] = field(default_factory=set)
    article_overlap: set[str] = field(default_factory=set)


def known_candidate_ids(
    test_candidates: list["ScoredCandidate"],
    checklist_candidates: list["ScoredCandidate"],
) -> set[str]:
    """The single definition of candidate identity — used by the orchestrator's
    hallucination guard and the reviewer's known-ids list, which must agree."""
    return {c.item.slug for c in test_candidates} | {
        c.item.slug for c in checklist_candidates
    }


def _card_signals(card: SystemCard) -> tuple[set[str], set[str], set[str]]:
    mapped = map_system_card_tags(
        sorted(card.target_system_slugs), sorted(card.sector_slugs)
    )
    return mapped.ai_type_slugs, mapped.sector_slugs, card.article_keys()


def prefilter_tools(
    card: SystemCard, tools: list[CatalogueTool], weights: Weights = Weights()
) -> list[ScoredCandidate]:
    ai_types, sectors, articles = _card_signals(card)
    candidates = []
    for tool in tools:
        scored = ScoredCandidate(
            item=tool,
            score=0.0,
            ai_type_overlap=tool.tag_slugs & ai_types,
            sector_overlap=tool.tag_slugs & sectors,
            article_overlap=tool.article_keys() & articles,
        )
        scored.score = (
            weights.ai_type * len(scored.ai_type_overlap)
            + weights.sector * len(scored.sector_overlap)
            + weights.article * len(scored.article_overlap)
        )
        if scored.score > 0:
            candidates.append(scored)
    return sorted(candidates, key=lambda c: (-c.score, c.item.slug))


def prefilter_checklists(
    card: SystemCard, checklists: list[ChecklistDoc], weights: Weights = Weights()
) -> list[ScoredCandidate]:
    _, _, articles = _card_signals(card)
    candidates = []
    for checklist in checklists:
        overlap = checklist.article_keys() & articles
        candidates.append(
            ScoredCandidate(
                item=checklist,
                score=weights.article * len(overlap),
                article_overlap=overlap,
            )
        )
    return sorted(candidates, key=lambda c: (-c.score, c.item.slug))
