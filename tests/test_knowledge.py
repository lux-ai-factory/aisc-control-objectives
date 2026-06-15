"""Knowledge document base — the high-risk-sector classification (D2 floor).

The Annex III high-risk sectors are domain knowledge, not operator config, so
they live in the document base (`knowledge/high-risk-sectors.md`) and are loaded
from there, never from wizard.toml.
"""

import textwrap
from pathlib import Path

from wizard.knowledge import HIGH_RISK_SECTORS_DOC, load_high_risk_sectors

KNOWLEDGE_DIR = Path(__file__).parent.parent / "knowledge"


class TestLoader:
    def test_loads_sectors_from_doc(self, tmp_path):
        (tmp_path / HIGH_RISK_SECTORS_DOC).write_text(
            textwrap.dedent(
                """
                ---
                sectors:
                  - finance-and-insurance
                  - health
                ---
                Some prose explaining the Annex III basis.
                """
            )
        )
        assert load_high_risk_sectors(tmp_path) == {"finance-and-insurance", "health"}

    def test_missing_doc_is_empty_set(self, tmp_path):
        assert load_high_risk_sectors(tmp_path / "nope") == set()

    def test_doc_without_sectors_key_is_empty(self, tmp_path):
        (tmp_path / HIGH_RISK_SECTORS_DOC).write_text("---\nnote: hi\n---\nbody")
        assert load_high_risk_sectors(tmp_path) == set()


class TestShippedDocument:
    def test_ships_and_parses(self):
        sectors = load_high_risk_sectors(KNOWLEDGE_DIR)
        assert "finance-and-insurance" in sectors
        assert "health" in sectors

    def test_sectors_are_real_catalogue_slugs(self, seed_tools_raw):
        """The shipped sectors must be slugs the catalogue actually emits, or
        the floor silently never triggers."""
        vocabulary = {slug for tool in seed_tools_raw for slug in tool["tag_slugs"]}
        assert load_high_risk_sectors(KNOWLEDGE_DIR) <= vocabulary

    def test_horizontal_sectors_excluded(self):
        sectors = load_high_risk_sectors(KNOWLEDGE_DIR)
        assert "digital-economy" not in sectors
        assert "science-&-technology" not in sectors
