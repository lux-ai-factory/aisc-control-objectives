"""Trustworthiness-dimension registry (Phase 0).

The catalogue is the source of truth for the taxonomy: tools carry a dimension
tag in their `tag_slugs` and control checklists carry `dimension_slug`. This
registry sits on top of those slugs to add what the catalogue tags don't:
human-readable labels, a stable display order, which item types each dimension
applies to, and a deterministic floor flag for D2. It also reconciles the one
slug that drifts between the two taxonomies — tests label the wellbeing
dimension "Wellbeing", controls "Well-being" — onto a single canonical slug.

The registry never invents dimensions the catalogue doesn't use; it is the
union of the 11 dimensions observed across tests and controls:

- Shared 6 (tests + controls): the AI-HLEG trustworthiness pillars.
- Control-only 5 (governance/process): Accountability, Quality Management,
  Risk Management, Technical Documentation, Record-keeping. These have controls
  but no tests, so `requires_control` is True — a deterministic floor (D2)
  must not let them be auto-marked "covered" without an accepted control.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

TESTS = "tests"
CONTROLS = "controls"


@dataclass(frozen=True)
class Dimension:
    slug: str
    label: str
    order: int
    applies_to: frozenset[str]
    requires_control: bool = False
    sub_areas: tuple[str, ...] = field(default_factory=tuple)


def _shared(slug: str, label: str, order: int) -> Dimension:
    return Dimension(slug, label, order, frozenset({TESTS, CONTROLS}))


def _control_only(slug: str, label: str, order: int) -> Dimension:
    return Dimension(
        slug, label, order, frozenset({CONTROLS}), requires_control=True
    )


# Union of 11, in display order: the shared 6 first, then the control-only 5.
_DIMENSIONS: tuple[Dimension, ...] = (
    _shared("human-agency-oversight", "Human Agency and Oversight", 0),
    _shared("technical-robustness-safety", "Technical Robustness and Safety", 1),
    _shared("privacy-data-governance", "Privacy and Data Governance", 2),
    _shared("transparency", "Transparency", 3),
    _shared(
        "diversity-non-discrimination-fairness",
        "Diversity, Non-discrimination and Fairness",
        4,
    ),
    _shared(
        "societal-environmental-wellbeing",
        "Societal and Environmental Wellbeing",
        5,
    ),
    _control_only("accountability", "Accountability", 6),
    _control_only("quality-management", "Quality Management", 7),
    _control_only("risk-management", "Risk Management", 8),
    _control_only("technical-documentation", "Technical Documentation", 9),
    _control_only("record-keeping", "Record-keeping", 10),
)

_BY_SLUG: dict[str, Dimension] = {d.slug: d for d in _DIMENSIONS}

# Slug drift onto canonical slugs: the test/control "well-being" split, plus the
# natural "and"-infixed forms a skill author (or future catalogue data) may write
# for dimensions whose canonical slug drops the "and".
_ALIASES: dict[str, str] = {
    "societal-environmental-well-being": "societal-environmental-wellbeing",
    "societal-and-environmental-wellbeing": "societal-environmental-wellbeing",
    "societal-and-environmental-well-being": "societal-environmental-wellbeing",
    "human-agency-and-oversight": "human-agency-oversight",
    "technical-robustness-and-safety": "technical-robustness-safety",
    "privacy-and-data-governance": "privacy-data-governance",
    "diversity-non-discrimination-and-fairness": "diversity-non-discrimination-fairness",
}


def all_dimensions() -> list[Dimension]:
    """The registry in display order."""
    return list(_DIMENSIONS)


def normalize_slug(slug: str) -> str:
    """Resolve a catalogue slug onto its canonical form; unknown slugs pass
    through unchanged (the caller decides whether that is a problem)."""
    return _ALIASES.get(slug, slug)


def get(slug: str) -> Dimension | None:
    """Look up a dimension by slug (aliases resolved); None if unknown."""
    return _BY_SLUG.get(normalize_slug(slug))


def label_for(slug: str) -> str:
    """Human-readable label for a slug; falls back to the slug itself."""
    dimension = get(slug)
    return dimension.label if dimension is not None else slug


def dimension_slugs_from_tags(tags: Iterable[str]) -> list[str]:
    """Extract the canonical dimension slugs carried by a tag set, in registry
    order. Non-dimension tags (ai_type, sector, licensing, type) are ignored —
    this is how a tool's flat `tag_slugs` is reduced to its dimension(s)."""
    found = {
        normalized
        for tag in tags
        if (normalized := normalize_slug(tag)) in _BY_SLUG
    }
    return [d.slug for d in _DIMENSIONS if d.slug in found]
