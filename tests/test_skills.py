"""SkillsLoader (Phase 2): markdown + YAML frontmatter, routed by
(dimension, ai_type, sector).

The loader is the channel for domain knowledge that tells the agents what to
test per dimension/technology. It ships disabled (an empty loader behaves like
the unskilled path); when files are present, matching skill bodies are selected
for injection into the framer/proposer prompt.
"""

from pathlib import Path

import pytest

from wizard import dimensions
from wizard.skills import Skill, SkillsLoader

SKILLS_DIR = Path(__file__).parent / "fixtures" / "skills"
SHIPPED_SKILLS_DIR = Path(__file__).parent.parent / "skills"


@pytest.fixture(scope="module")
def loader() -> SkillsLoader:
    return SkillsLoader.from_dir(SKILLS_DIR)


class TestLoading:
    def test_loads_all_fixture_skills(self, loader):
        assert len(loader.skills) == 2

    def test_frontmatter_parsed(self, loader):
        robustness = next(
            s for s in loader.skills if "robustness" in s.dimension
        )
        assert robustness.ai_types == (
            "natural-language-processing",
            "agents-and-agentic-systems",
        )
        assert robustness.sectors == ("finance-and-insurance",)
        assert "prompt-injection" in robustness.body

    def test_dimension_normalised_on_load(self, loader):
        # file writes the "and"-infixed form; loader canonicalises it
        robustness = next(s for s in loader.skills if "robustness" in s.dimension)
        assert robustness.dimension == "technical-robustness-safety"

    def test_missing_dir_is_empty_not_error(self, tmp_path):
        loader = SkillsLoader.from_dir(tmp_path / "does-not-exist")
        assert loader.skills == []
        assert not loader  # falsy when empty

    def test_disabled_loader_is_empty(self):
        assert SkillsLoader.disabled().skills == []


class TestSelection:
    def test_dimension_required(self, loader):
        # privacy has no skill → nothing selected
        selected = loader.select(
            dimensions=["privacy-data-governance"],
            ai_types={"natural-language-processing"},
            sectors={"finance-and-insurance"},
        )
        assert selected == []

    def test_matches_on_dimension_and_ai_type_and_sector(self, loader):
        selected = loader.select(
            dimensions=["technical-robustness-safety"],
            ai_types={"natural-language-processing"},
            sectors={"finance-and-insurance"},
        )
        assert [s.dimension for s in selected] == ["technical-robustness-safety"]

    def test_ai_type_mismatch_excludes_skill(self, loader):
        # robustness skill is gated to NLP/agentic; a vision-only system misses it
        selected = loader.select(
            dimensions=["technical-robustness-safety"],
            ai_types={"computer-vision"},
            sectors={"finance-and-insurance"},
        )
        assert selected == []

    def test_sector_mismatch_excludes_skill(self, loader):
        selected = loader.select(
            dimensions=["technical-robustness-safety"],
            ai_types={"natural-language-processing"},
            sectors={"health"},
        )
        assert selected == []

    def test_unconstrained_skill_matches_any_technology(self, loader):
        # transparency skill declares no ai_types/sectors → matches all
        selected = loader.select(
            dimensions=["transparency"],
            ai_types={"computer-vision"},
            sectors={"health"},
        )
        assert [s.dimension for s in selected] == ["transparency"]

    def test_accepts_and_infixed_dimension_query(self, loader):
        selected = loader.select(
            dimensions=["technical-robustness-and-safety"],
            ai_types={"agents-and-agentic-systems"},
            sectors={"finance-and-insurance"},
        )
        assert len(selected) == 1


class TestShippedSkills:
    """The real starter library under skills/ must parse and route correctly,
    and the README (no frontmatter) must be ignored, not crash the loader."""

    @pytest.fixture(scope="class")
    def shipped(self) -> SkillsLoader:
        return SkillsLoader.from_dir(SHIPPED_SKILLS_DIR)

    def test_loads_and_skips_readme(self, shipped):
        md_files = list(SHIPPED_SKILLS_DIR.glob("*.md"))
        # every .md except the README (which has no `dimension`) becomes a skill
        assert len(shipped.skills) == len(md_files) - 1
        assert len(shipped.skills) >= 6

    def test_every_skill_dimension_is_a_known_slug(self, shipped):
        for skill in shipped.skills:
            assert dimensions.get(skill.dimension) is not None
            # stored canonical
            assert skill.dimension == dimensions.normalize_slug(skill.dimension)

    def test_routes_for_nlp_finance_system(self, shipped):
        # an MCAS-like system: NLP + predictive, finance sector
        selected = shipped.select(
            dimensions=[
                "technical-robustness-safety",
                "privacy-data-governance",
                "transparency",
                "diversity-non-discrimination-fairness",
            ],
            ai_types={"natural-language-processing", "predictive-and-analytical-ai"},
            sectors={"finance-and-insurance"},
        )
        dims = {s.dimension for s in selected}
        assert "technical-robustness-safety" in dims
        assert "diversity-non-discrimination-fairness" in dims
        assert "privacy-data-governance" in dims  # unconstrained → matches
        assert "transparency" in dims

    def test_governance_dimension_skill_present(self, shipped):
        risk = [s for s in shipped.skills if s.dimension == "risk-management"]
        assert risk and "Article 9" in risk[0].body


class TestSkillModel:
    def test_skill_is_hashable_frozen(self):
        skill = Skill(
            dimension="transparency",
            ai_types=(),
            sectors=(),
            applies_when="",
            body="b",
            source="x.md",
        )
        with pytest.raises(Exception):
            skill.body = "y"  # frozen
