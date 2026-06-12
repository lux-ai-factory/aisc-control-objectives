"""Map qualification tag slugs onto the catalogue tag vocabulary.

Qualification target-system tags are `category:subcategory`; the catalogue
tags tools at category level (`ai_type` section). Most category prefixes match
the catalogue slug verbatim; the exceptions differ only by an "and" infix and
live in _CATEGORY_EXCEPTIONS. Sector slugs are shared verbatim between the
two taxonomies.

Unknown categories are *reported*, never silently dropped — the caller decides
whether to warn or fall through to the LLM matcher (SPEC §2.4).
"""

from __future__ import annotations

from dataclasses import dataclass, field

_CATEGORY_EXCEPTIONS = {
    "tabular-structured-data": "tabular-and-structured-data",
    "predictive-analytical-ai": "predictive-and-analytical-ai",
    "knowledge-retrieval": "knowledge-and-retrieval",
    # label "Agents & Agentic Systems" slugifies with the "&" dropped, but the
    # catalogue slug keeps an "and"
    "agents-agentic-systems": "agents-and-agentic-systems",
}

# catalogue ai_type slugs that map from an identical qualification prefix
_IDENTITY_CATEGORIES = {
    "natural-language-processing",
    "computer-vision",
    "agents-and-agentic-systems",
    "anomaly-detection",
}


def map_ai_type(qual_tag: str) -> str | None:
    """Map one qualification target-system tag to a catalogue ai_type slug."""
    category = qual_tag.split(":", 1)[0]
    if category in _CATEGORY_EXCEPTIONS:
        return _CATEGORY_EXCEPTIONS[category]
    if category in _IDENTITY_CATEGORIES:
        return category
    return None


def map_sector(sector_tag: str) -> str:
    """Sector slugs are shared verbatim between qualification and catalogue."""
    return sector_tag


@dataclass
class MappedTags:
    ai_type_slugs: set[str] = field(default_factory=set)
    sector_slugs: set[str] = field(default_factory=set)
    unmapped: list[str] = field(default_factory=list)


def map_system_card_tags(
    target_system_tags: list[str], sector_tags: list[str]
) -> MappedTags:
    """Map all tags of a system card; collect unmappable ones for reporting."""
    result = MappedTags()
    for tag in target_system_tags:
        slug = map_ai_type(tag)
        if slug is None:
            result.unmapped.append(tag)
        else:
            result.ai_type_slugs.add(slug)
    result.sector_slugs = {map_sector(t) for t in sector_tags}
    return result
