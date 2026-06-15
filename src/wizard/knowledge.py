"""The knowledge document base: regulatory/domain reference documents that are
maintained by a compliance reviewer rather than tuned by an operator.

Currently holds the EU AI Act Annex III high-risk-sector classification that
drives the D2 deterministic floor. Operational policy (whether the floor is
enabled at all) stays in `wizard.toml`; the *content* of the classification
lives here, alongside the skills, as a citable document.
"""

from __future__ import annotations

from pathlib import Path

from wizard.frontmatter import split_frontmatter

HIGH_RISK_SECTORS_DOC = "high-risk-sectors.md"


def load_high_risk_sectors(knowledge_dir: Path | str) -> set[str]:
    """The catalogue sector slugs classified high-risk (EU AI Act Annex III),
    read from `<knowledge_dir>/high-risk-sectors.md`. A missing document yields
    an empty set — the floor then never classifies a system high-risk."""
    path = Path(knowledge_dir) / HIGH_RISK_SECTORS_DOC
    if not path.is_file():
        return set()
    meta, _ = split_frontmatter(path.read_text(encoding="utf-8"))
    return {str(s) for s in (meta.get("sectors") or [])}
