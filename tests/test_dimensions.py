"""Trustworthiness-dimension registry (wizard/dimensions.py).

The registry is the union of the 11 dimensions the catalogue uses: the 6 shared
by tests and controls, plus the 5 control-only governance/process dimensions.
It supplies labels + ordering and normalises the test/control slug drift
("Wellbeing" vs "Well-being") onto one canonical slug. The taxonomy still lives
in the catalogue tags; this registry only adds presentation + a mapping helper.
"""

import pytest

from wizard import dimensions


class TestRegistryShape:
    def test_union_of_eleven(self):
        assert len(dimensions.all_dimensions()) == 11

    def test_ordered_and_unique(self):
        dims = dimensions.all_dimensions()
        orders = [d.order for d in dims]
        assert orders == sorted(orders)
        assert len(orders) == len(set(orders))
        slugs = [d.slug for d in dims]
        assert len(slugs) == len(set(slugs))

    @pytest.mark.parametrize(
        "slug",
        [
            "human-agency-oversight",
            "technical-robustness-safety",
            "privacy-data-governance",
            "transparency",
            "diversity-non-discrimination-fairness",
            "societal-environmental-wellbeing",
            "accountability",
            "quality-management",
            "risk-management",
            "technical-documentation",
            "record-keeping",
        ],
    )
    def test_known_slug_present(self, slug):
        assert dimensions.get(slug) is not None

    def test_shared_six_apply_to_tests_and_controls(self):
        shared = [d for d in dimensions.all_dimensions() if "tests" in d.applies_to]
        assert len(shared) == 6
        for d in shared:
            assert "controls" in d.applies_to

    def test_control_only_five_require_a_control(self):
        control_only = [
            d for d in dimensions.all_dimensions() if "tests" not in d.applies_to
        ]
        assert len(control_only) == 5
        for d in control_only:
            assert d.applies_to == frozenset({"controls"})
            assert d.requires_control is True


class TestNormalisation:
    def test_wellbeing_variant_normalises(self):
        # controls label it "Well-being", tests "Wellbeing" — one canonical slug
        assert (
            dimensions.normalize_slug("societal-environmental-well-being")
            == "societal-environmental-wellbeing"
        )

    def test_canonical_slug_unchanged(self):
        assert (
            dimensions.normalize_slug("technical-robustness-safety")
            == "technical-robustness-safety"
        )

    def test_unknown_slug_passes_through(self):
        assert dimensions.normalize_slug("not-a-dimension") == "not-a-dimension"

    def test_get_resolves_alias(self):
        d = dimensions.get("societal-environmental-well-being")
        assert d is not None
        assert d.slug == "societal-environmental-wellbeing"

    @pytest.mark.parametrize(
        ("written", "canonical"),
        [
            ("technical-robustness-and-safety", "technical-robustness-safety"),
            ("human-agency-and-oversight", "human-agency-oversight"),
            ("privacy-and-data-governance", "privacy-data-governance"),
            (
                "diversity-non-discrimination-and-fairness",
                "diversity-non-discrimination-fairness",
            ),
            (
                "societal-and-environmental-wellbeing",
                "societal-environmental-wellbeing",
            ),
        ],
    )
    def test_natural_and_form_aliases(self, written, canonical):
        # skill authors / future catalogue data may use the "and"-infixed form
        assert dimensions.normalize_slug(written) == canonical
        assert dimensions.get(written).slug == canonical


class TestLabels:
    def test_label_for_known(self):
        assert dimensions.label_for("transparency") == "Transparency"

    def test_label_for_alias(self):
        assert (
            dimensions.label_for("societal-environmental-well-being")
            == "Societal and Environmental Wellbeing"
        )

    def test_label_for_unknown_falls_back_to_slug(self):
        assert dimensions.label_for("mystery-dim") == "mystery-dim"


class TestTagExtraction:
    def test_single_dimension_from_tags(self):
        tags = {"natural-language-processing", "finance-and-insurance", "transparency"}
        assert dimensions.dimension_slugs_from_tags(tags) == ["transparency"]

    def test_multiple_dimensions_returned_in_registry_order(self):
        # lynx carries both robustness and transparency tags
        tags = {"transparency", "technical-robustness-safety", "open"}
        result = dimensions.dimension_slugs_from_tags(tags)
        assert result == ["technical-robustness-safety", "transparency"]

    def test_no_dimension_tags_returns_empty(self):
        assert dimensions.dimension_slugs_from_tags({"open", "mit", "test"}) == []

    def test_alias_tag_normalised(self):
        result = dimensions.dimension_slugs_from_tags(
            {"societal-environmental-well-being"}
        )
        assert result == ["societal-environmental-wellbeing"]

    def test_deduplicates(self):
        result = dimensions.dimension_slugs_from_tags(
            {"transparency", "societal-environmental-well-being", "societal-environmental-wellbeing"}
        )
        assert result.count("societal-environmental-wellbeing") == 1
