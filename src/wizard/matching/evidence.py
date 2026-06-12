"""G1 — verify that evidence quotes actually occur in the system card.

Verbatim evidence is a hard requirement of the proposer prompt; this module
makes it checkable. Matching: normalized substring first, then a fuzzy
best-window ratio >= 0.90 (absorbs punctuation/elision drift, rejects
paraphrase).
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher

from wizard.models.plan import ProposedItem
from wizard.models.system_card import SystemCard

FUZZY_THRESHOLD = 0.90
_MIN_QUOTE_CHARS = 8


def _normalize(text: str) -> str:
    text = text.lower()
    text = re.sub(r"\s+", " ", text)
    return text.strip(" \t\n.,;:!?\"'")


def card_corpus(card: SystemCard) -> str:
    """All card text an evidence quote may legitimately come from."""
    parts: list[str] = [
        card.overview,
        card.description,
        card.target_use_case,
        card.target_users,
    ]
    for finding in card.findings:
        parts.append(finding.summary)
        parts.extend(finding.points)
    parts.extend(card.open_issues)
    # WP2-M3: qualification answers join the corpus once SystemCard carries them
    parts.extend(getattr(card, "answer_texts", lambda: [])())
    return "\n".join(p for p in parts if p)


def find_quote(quote: str, corpus: str) -> bool:
    norm_quote = _normalize(quote)
    if len(norm_quote) < _MIN_QUOTE_CHARS:
        return False
    norm_corpus = _normalize(corpus)
    if norm_quote in norm_corpus:
        return True

    # fuzzy: same-length sliding windows, so an aligned perfect match scores
    # 1.0 and the 0.90 threshold has real slack for punctuation/elision drift
    # (longer windows cap the achievable ratio below ~0.91 — see tests)
    window = len(norm_quote)
    step = max(1, window // 10)
    matcher = SequenceMatcher(autojunk=False)
    matcher.set_seq2(norm_quote)
    for start in range(0, max(1, len(norm_corpus) - window + 1), step):
        matcher.set_seq1(norm_corpus[start : start + window])
        if (
            matcher.real_quick_ratio() >= FUZZY_THRESHOLD
            and matcher.quick_ratio() >= FUZZY_THRESHOLD
            and matcher.ratio() >= FUZZY_THRESHOLD
        ):
            return True
    return False


def verify_evidence(item: ProposedItem, card: SystemCard) -> list[str]:
    """Return the subset of the item's evidence quotes found in the card."""
    corpus = card_corpus(card)
    return [quote for quote in item.evidence if find_quote(quote, corpus)]
