"""Qualification tag slugs → catalogue tag slugs.

Qualification target-system tags are `category:subcategory` slugs
(e.g. "tabular-structured-data:tabular-classification-regression"); the
catalogue tags them at category level with slightly different slugs
(e.g. "tabular-and-structured-data"). Sector slugs are already identical.
"""

import json
from pathlib import Path

import pytest

from wizard.matching.tag_map import map_ai_type, map_system_card_tags

FIXTURES = Path(__file__).parent / "fixtures"


class TestMapAiType:
    @pytest.mark.parametrize(
        ("qual_tag", "expected"),
        [
            # identical prefix
            ("natural-language-processing:question-answering", "natural-language-processing"),
            # renamed prefixes (the "and" variants)
            ("tabular-structured-data:tabular-classification-regression", "tabular-and-structured-data"),
            ("predictive-analytical-ai:risk-scoring-assessment", "predictive-and-analytical-ai"),
            ("knowledge-retrieval:retrieval-augmented-generation-rag", "knowledge-and-retrieval"),
        ],
    )
    def test_known_mappings(self, qual_tag, expected):
        assert map_ai_type(qual_tag) == expected

    def test_unknown_category_returns_none(self):
        assert map_ai_type("quantum-computing:qubit-stuff") is None

    def test_category_only_tag_without_subcategory(self):
        assert map_ai_type("natural-language-processing") == "natural-language-processing"


class TestMapSystemCardTags:
    def test_sector_slugs_pass_through_verbatim(self):
        result = map_system_card_tags([], ["finance-and-insurance", "health"])
        assert result.sector_slugs == {"finance-and-insurance", "health"}

    def test_mcas_card_maps_fully(self):
        """Every MCAS tag must resolve to a slug present in the seed vocabulary."""
        tools = json.loads((FIXTURES / "tools_seed.json").read_text())
        vocabulary = {slug for tool in tools for slug in tool["tag_slugs"]}

        mcas_target_tags = [
            "tabular-structured-data:tabular-classification-regression",
            "predictive-analytical-ai:risk-scoring-assessment",
            "predictive-analytical-ai:predictive-analytics",
            "natural-language-processing:text-generation-summarization",
            "natural-language-processing:question-answering",
            "knowledge-retrieval:retrieval-augmented-generation-rag",
        ]
        result = map_system_card_tags(mcas_target_tags, ["finance-and-insurance"])

        assert result.ai_type_slugs == {
            "tabular-and-structured-data",
            "predictive-and-analytical-ai",
            "natural-language-processing",
            "knowledge-and-retrieval",
        }
        assert result.sector_slugs == {"finance-and-insurance"}
        assert result.unmapped == []
        # everything we map must exist in the actual catalogue vocabulary
        assert result.ai_type_slugs <= vocabulary
        assert result.sector_slugs <= vocabulary

    def test_unmapped_tags_are_reported_not_dropped_silently(self):
        result = map_system_card_tags(["quantum-computing:qubits"], [])
        assert result.ai_type_slugs == set()
        assert result.unmapped == ["quantum-computing:qubits"]
