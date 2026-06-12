"""Article/Annex reference extraction from system-card and checklist text.

The matching signal is overlap at the *article* level: "Article 13.2" in a
system-card open issue must match a checklist question tagged "Article 13".
"""

from wizard.matching.articles import article_keys


class TestArticleKeys:
    def test_plain_article(self):
        assert article_keys("Article 12") == {"article-12"}

    def test_dotted_subref_collapses_to_article(self):
        assert article_keys("Article 13.2") == {"article-13"}

    def test_letter_subref(self):
        # the MCAS card contains refs like "Article 13.b.iii"
        assert article_keys("Article 13.b.iii") == {"article-13"}

    def test_slashed_alternatives(self):
        # open issue: "Article 14.4.a/d: No explicit stop/kill-switch ..."
        assert article_keys("Article 14.4.a/d: No explicit mechanism") == {"article-14"}

    def test_plural_enumeration(self):
        # overview: "... across Articles 10, 12, 13, and 14"
        assert article_keys("compliance across Articles 10, 12, 13, and 14") == {
            "article-10",
            "article-12",
            "article-13",
            "article-14",
        }

    def test_annex_roman(self):
        assert article_keys("Annex IV.1.a") == {"annex-iv"}
        assert article_keys("within the high-risk scope of Annex III") == {"annex-iii"}

    def test_mixed_text(self):
        text = "Supports Article 10.2.b and Annex IV.1.a traceability."
        assert article_keys(text) == {"article-10", "annex-iv"}

    def test_case_insensitive(self):
        assert article_keys("ARTICLE 15 and annex iii") == {"article-15", "annex-iii"}

    def test_no_refs(self):
        assert article_keys("Supports fairness compliance aligned with EU AI Act") == set()

    def test_empty_and_none_safe(self):
        assert article_keys("") == set()
        assert article_keys(None) == set()

    def test_does_not_match_article_inside_words(self):
        assert article_keys("This particle 12 is not a legal ref") == set()

    def test_iterable_of_texts(self):
        # convenience: accept a list (e.g. all `references` of a finding)
        assert article_keys(["Article 12", "Article 12.3", "Annex IV.1.a"]) == {
            "article-12",
            "annex-iv",
        }
