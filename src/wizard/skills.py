"""Skills ingestion (Phase 2): human-authored domain knowledge that tells the
agents what to test per dimension/technology and when coverage is adequate.

A skill is a Markdown file with a YAML frontmatter header:

    ---
    dimension: technical-robustness-and-safety
    ai_types: [natural-language-processing, agents-and-agentic-systems]
    sectors: [finance-and-insurance]      # optional; empty = all sectors
    applies_when: "system exposes an LLM endpoint"   # optional, human-readable
    ---
    Guidance body: what to test, what counts as adequate coverage, …

Skills are routed by `(dimension, ai_type, sector)` and their bodies are
injected into the framer/proposer **stable cached block** (so they don't break
the SPEC §5.4 prompt-cache design). The loader ships **disabled**: with no
skills directory it behaves exactly like the unskilled path.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from wizard.dimensions import normalize_slug
from wizard.frontmatter import split_frontmatter


@dataclass(frozen=True)
class Skill:
    dimension: str
    ai_types: tuple[str, ...]
    sectors: tuple[str, ...]
    applies_when: str
    body: str
    source: str


def _as_tuple(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    return tuple(str(v) for v in value)


def _parse_skill(path: Path) -> Skill | None:
    meta, body = split_frontmatter(path.read_text(encoding="utf-8"))
    dimension = meta.get("dimension")
    if not dimension:
        # a skill with no dimension can't be routed — skip it rather than guess
        return None
    return Skill(
        dimension=normalize_slug(str(dimension)),
        ai_types=_as_tuple(meta.get("ai_types")),
        sectors=_as_tuple(meta.get("sectors")),
        applies_when=str(meta.get("applies_when") or ""),
        body=body,
        source=path.name,
    )


class SkillsLoader:
    def __init__(self, skills: list[Skill]):
        self.skills = skills

    def __bool__(self) -> bool:
        return bool(self.skills)

    @classmethod
    def disabled(cls) -> SkillsLoader:
        """An empty loader — the default, used until real skills files exist."""
        return cls([])

    @classmethod
    def from_dir(cls, path: Path | str) -> SkillsLoader:
        """Load every `*.md` skill in a directory (sorted by filename for a
        stable prompt order). A missing directory yields an empty loader."""
        directory = Path(path)
        if not directory.is_dir():
            return cls([])
        skills = [
            skill
            for file in sorted(directory.glob("*.md"))
            if (skill := _parse_skill(file)) is not None
        ]
        return cls(skills)

    def select(
        self,
        dimensions: Iterable[str],
        ai_types: set[str],
        sectors: set[str],
    ) -> list[Skill]:
        """Skills whose dimension is among `dimensions` and whose ai_type /
        sector constraints (if any) intersect the system's. An empty constraint
        means 'applies to all'. Order follows load order (stable for caching)."""
        wanted = {normalize_slug(d) for d in dimensions}
        selected = []
        for skill in self.skills:
            if skill.dimension not in wanted:
                continue
            if skill.ai_types and not (set(skill.ai_types) & ai_types):
                continue
            if skill.sectors and not (set(skill.sectors) & sectors):
                continue
            selected.append(skill)
        return selected
