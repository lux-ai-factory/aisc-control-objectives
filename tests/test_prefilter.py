"""Deterministic prefilter (SPEC §5.1) — golden tests against the MCAS card.

MCAS is a credit-scoring system: tabular + predictive + NLP + RAG, sector
finance-and-insurance, with findings/open issues on Articles 10/12/13/14
(plus 16/19 references and Annex IV). The prefilter must surface fairness,
robustness, and PII tools and the article-matching checklists — and must not
let an irrelevant (e.g. computer-vision-only) tool through.
"""

from wizard.matching.articles import article_keys
from wizard.matching.prefilter import prefilter_checklists, prefilter_tools
from wizard.models.catalogue import CatalogueTool


class TestParenthesizedArticleRefs:
    def test_checklist_paren_form_collapses_to_article(self):
        # controls_seed uses forms like "Article 13(3)(b)(ii)"
        assert article_keys("Article 13(3)(b)(ii), 13(3)(b)(v)") == {"article-13"}


class TestPrefilterTools:
    def test_results_sorted_by_score_desc(self, mcas_card, seed_tools):
        ranked = prefilter_tools(mcas_card, seed_tools)
        scores = [c.score for c in ranked]
        assert scores == sorted(scores, reverse=True)

    def test_relevant_tools_rank_top(self, mcas_card, seed_tools):
        ranked = prefilter_tools(mcas_card, seed_tools)
        top5 = {c.item.name for c in ranked[:5]}
        # 3 ai-type overlaps + finance sector
        assert "Adversarial Robustness Toolbox (ART)" in top5
        # fairness toolkit, finance sector — the canonical pick for credit scoring
        assert "AI Fairness 360" in top5
        # PII detection, finance sector
        assert "Microsoft Presidio" in top5

    def test_zero_score_tools_excluded(self, mcas_card, seed_tools):
        cv_only = CatalogueTool(
            slug="cv-only",
            name="CV Only Tool",
            tag_slugs={"computer-vision", "open", "test"},
        )
        ranked = prefilter_tools(mcas_card, seed_tools + [cv_only])
        assert "CV Only Tool" not in {c.item.name for c in ranked}

    def test_candidates_carry_overlap_explanations(self, mcas_card, seed_tools):
        ranked = prefilter_tools(mcas_card, seed_tools)
        fairness = next(c for c in ranked if c.item.name == "AI Fairness 360")
        assert "finance-and-insurance" in fairness.sector_overlap
        assert {"natural-language-processing", "predictive-and-analytical-ai"} <= (
            fairness.ai_type_overlap
        )


class TestPrefilterChecklists:
    def test_article_matching_checklists_score_positive(self, mcas_card, seed_checklists):
        ranked = prefilter_checklists(mcas_card, seed_checklists)
        by_name = {c.item.name: c for c in ranked}
        for name in (
            "Transparency_Checklist",  # Article 13
            "Human Oversight Evaluation Tool - Article 14 AI Act",
            "Logging Evaluation Tool - Article 12 AI Act",
            "Data and Data Governance Evaluation Tool - Article 10 AI Act",
        ):
            assert by_name[name].score > 0, name
            assert by_name[name].article_overlap, name

    def test_all_checklists_remain_candidates(self, mcas_card, seed_checklists):
        # horizontal checklists (e.g. Risk Management, Article 9) have no
        # article overlap with the MCAS card but stay in the candidate set —
        # the proposer agent decides, not the prefilter (SPEC §5.1)
        ranked = prefilter_checklists(mcas_card, seed_checklists)
        assert len(ranked) == len(seed_checklists)

    def test_zero_overlap_checklist_ranks_below_matching_ones(self, mcas_card, seed_checklists):
        ranked = prefilter_checklists(mcas_card, seed_checklists)
        names = [c.item.name for c in ranked]
        assert names.index("Transparency_Checklist") < names.index(
            "Risk Management_Checklist"
        )


class TestWeights:
    def test_ai_type_weighs_more_than_sector(self, mcas_card):
        two_ai = CatalogueTool(
            slug="a", name="a", tag_slugs={"natural-language-processing", "tabular-and-structured-data"}
        )
        one_sector = CatalogueTool(slug="b", name="b", tag_slugs={"finance-and-insurance"})
        ranked = prefilter_tools(mcas_card, [two_ai, one_sector])
        assert ranked[0].item.slug == "a"
