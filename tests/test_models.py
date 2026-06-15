"""Domain models: system card, catalogue entries, checklists, agent I/O."""

import pytest
from pydantic import ValidationError

from wizard.models.catalogue import CatalogueTool, ChecklistDoc
from wizard.models.plan import (
    DimensionAssessment,
    DimensionFrame,
    DimensionFraming,
    ItemVerdict,
    Proposal,
    ProposedItem,
    Review,
    dedupe_gaps,
    dedupe_items,
)


class TestSystemCard:
    def test_identity(self, mcas_card):
        assert mcas_card.system_name == "MicroCredit Assist Score (MCAS)"
        assert mcas_card.system_version == "v1.2.0"
        assert mcas_card.qualification_id == "cmpeno6uw0001h9ig8l1d5b27"

    def test_sector_slugs_derived_from_labels(self, mcas_card):
        assert mcas_card.sector_slugs == {"finance-and-insurance"}

    def test_target_system_slugs_derived_from_labels(self, mcas_card):
        # label "Tabular & Structured Data" / "Tabular Classification & Regression"
        # must slugify to the qualification slug form (category:subcategory)
        assert (
            "tabular-structured-data:tabular-classification-regression"
            in mcas_card.target_system_slugs
        )
        # "Retrieval-Augmented Generation (RAG)" — parens dropped
        assert (
            "knowledge-retrieval:retrieval-augmented-generation-rag"
            in mcas_card.target_system_slugs
        )
        assert len(mcas_card.target_system_slugs) == 6

    def test_findings_parsed(self, mcas_card):
        assert len(mcas_card.findings) == 4
        articles = {f.article for f in mcas_card.findings}
        assert articles == {"Article 10", "Article 12", "Article 13", "Article 14"}

    def test_open_issues(self, mcas_card):
        assert len(mcas_card.open_issues) == 4

    def test_article_keys_aggregate_findings_and_open_issues(self, mcas_card):
        keys = mcas_card.article_keys()
        # findings reference Articles 10/12/13/14/16/19 + Annex IV
        for expected in ("article-10", "article-12", "article-13", "article-14", "annex-iv"):
            assert expected in keys

    def test_open_issue_keys_indexed_per_issue(self, mcas_card):
        per_issue = mcas_card.open_issue_keys()
        assert len(per_issue) == 4
        # first open issue is "Article 13.2: The IFU ..."
        assert per_issue[0] == {"article-13"}


class TestCatalogueTool:
    def test_from_seed_entry(self, seed_tools_raw):
        fairness = next(t for t in seed_tools_raw if t["name"] == "AI Fairness 360")
        tool = CatalogueTool.from_seed(fairness)
        assert tool.slug
        assert "finance-and-insurance" in tool.tag_slugs
        assert "natural-language-processing" in tool.tag_slugs
        # article keys extracted from metadata.target_legal_requirements
        assert isinstance(tool.article_keys(), set)

    def test_all_seed_entries_parse(self, seed_tools_raw):
        parsed = [CatalogueTool.from_seed(t) for t in seed_tools_raw]
        assert len(parsed) == len(seed_tools_raw)

    def test_dimension_slugs_extracted_from_tags(self, seed_tools):
        # AgentDojo is tagged with the robustness dimension only
        agentdojo = next(t for t in seed_tools if t.slug == "agentdojo")
        assert agentdojo.dimension_slugs() == ["technical-robustness-safety"]

    def test_multiple_dimensions_in_registry_order(self, seed_tools):
        # lynx carries both robustness and transparency dimension tags
        lynx = next(t for t in seed_tools if t.slug == "lynx")
        assert lynx.dimension_slugs() == [
            "technical-robustness-safety",
            "transparency",
        ]

    def test_tool_without_dimension_tag_returns_empty(self):
        bare = CatalogueTool(slug="x", name="x", tag_slugs={"open", "test"})
        assert bare.dimension_slugs() == []


class TestChecklistDoc:
    def test_from_seed_entry(self, seed_checklists_raw):
        accuracy = next(c for c in seed_checklists_raw if c["name"] == "Accuracy_Checklist")
        doc = ChecklistDoc.from_seed(accuracy)
        assert doc.control_topic == "Accuracy"
        # question articles ("Article 15") aggregate into article keys
        assert "article-15" in doc.article_keys()

    def test_all_seed_entries_parse(self, seed_checklists_raw):
        parsed = [ChecklistDoc.from_seed(c) for c in seed_checklists_raw]
        assert len(parsed) == len(seed_checklists_raw)

    def test_dimension_slugs_from_dimension_slug(self, seed_checklists):
        accuracy = next(c for c in seed_checklists if c.name == "Accuracy_Checklist")
        assert accuracy.dimension_slugs() == ["technical-robustness-safety"]

    def test_dimension_slug_normalised(self):
        doc = ChecklistDoc(
            slug="wb", name="wb", dimension_slug="societal-environmental-well-being"
        )
        assert doc.dimension_slugs() == ["societal-environmental-wellbeing"]

    def test_checklist_without_dimension_returns_empty(self):
        doc = ChecklistDoc(slug="x", name="x")
        assert doc.dimension_slugs() == []


class TestAgentSchemas:
    def test_proposal_roundtrip(self):
        payload = {
            "items": [
                {
                    "item_id": "ai-fairness-360",
                    "item_type": "test",
                    "score": 5,
                    "rationale": "Quarterly fairness audits are claimed; verify with a standard toolkit.",
                    "evidence": ["quarterly fairness audits compare approval, default, and override rates"],
                    "covers": ["article-10"],
                }
            ],
            "coverage_gaps": ["article-12: no live-inference logging test available"],
        }
        proposal = Proposal.model_validate(payload)
        assert proposal.items[0].score == 5

    def test_score_clamped_not_rejected(self):
        # out-of-range scores are clamped to [0, 5], never raised — a weak model
        # can't crash the parse with a stray value
        hi = ProposedItem.model_validate(
            {"item_id": "x", "item_type": "test", "score": 9, "rationale": "r"}
        )
        lo = ProposedItem.model_validate(
            {"item_id": "y", "item_type": "test", "score": -2, "rationale": "r"}
        )
        assert hi.score == 5
        assert lo.score == 0

    def test_score_defaults_when_omitted(self):
        item = ProposedItem.model_validate(
            {"item_id": "z", "item_type": "test", "rationale": "r"}
        )
        assert item.score == 3

    def test_review_verdicts(self):
        review = Review.model_validate(
            {
                "verdicts": [
                    {"item_id": "x", "verdict": "revise", "reasons": ["weak-rationale"]}
                ],
                "coverage_ok": False,
                "notes_for_revision": "Cover Article 14 explicitly.",
            }
        )
        assert review.verdicts[0].verdict == "revise"
        assert not review.coverage_ok

    def test_verdict_literal_enforced(self):
        with pytest.raises(ValidationError):
            ItemVerdict.model_validate({"item_id": "x", "verdict": "maybe", "reasons": []})

    def test_unpaired_dataset_parses_and_is_left_to_the_guard(self):
        # G3 is enforced by the dataset-pairing guard, NOT a parse-time
        # validator: an unpaired dataset must parse cleanly (so a weaker LLM
        # can't crash the whole proposal) and be dropped later by the guard.
        item = ProposedItem.model_validate(
            {
                "item_id": "some-dataset",
                "item_type": "dataset",
                "score": 3,
                "rationale": "r",
                "evidence": [],
                "covers": [],
            }
        )
        assert item.item_type == "dataset"
        assert item.paired_test_id is None

    def test_dataset_with_pairing_valid(self):
        item = ProposedItem.model_validate(
            {
                "item_id": "some-dataset",
                "item_type": "dataset",
                "score": 3,
                "rationale": "r",
                "evidence": [],
                "covers": [],
                "paired_test_id": "some-test",
            }
        )
        assert item.paired_test_id == "some-test"

    def test_non_dataset_needs_no_pairing(self):
        item = ProposedItem.model_validate(
            {
                "item_id": "t",
                "item_type": "test",
                "score": 5,
                "rationale": "r",
                "evidence": [],
                "covers": [],
            }
        )
        assert item.paired_test_id is None

    def test_dimension_slugs_default_empty(self):
        item = ProposedItem.model_validate(
            {
                "item_id": "t",
                "item_type": "test",
                "score": 5,
                "rationale": "r",
            }
        )
        assert item.dimension_slugs == []

    def test_dimension_slugs_roundtrip(self):
        item = ProposedItem.model_validate(
            {
                "item_id": "lynx",
                "item_type": "test",
                "score": 5,
                "rationale": "r",
                "dimension_slugs": ["technical-robustness-safety", "transparency"],
            }
        )
        assert item.dimension_slugs == [
            "technical-robustness-safety",
            "transparency",
        ]

    def test_proposal_carries_guard_report(self):
        proposal = Proposal.model_validate(
            {"items": [], "coverage_gaps": [], "guard_report": ["evidence-empty: x"]}
        )
        assert proposal.guard_report == ["evidence-empty: x"]


class TestDedupeItems:
    def _item(self, item_id, covers, score=3, evidence=None):
        return ProposedItem(
            item_id=item_id,
            item_type="test",
            score=score,
            rationale="r",
            evidence=evidence or [],
            covers=covers,
        )

    def test_no_duplicates_unchanged(self):
        items = [self._item("a", ["article-10"]), self._item("b", ["article-12"])]
        out, warnings = dedupe_items(items, "duplicate-item: {item_id} ({n})")
        assert [i.item_id for i in out] == ["a", "b"]
        assert warnings == []

    def test_merges_covers_evidence_and_highest_score(self):
        items = [
            self._item("a", ["article-10"], score=1, evidence=["e1"]),
            self._item("a", ["article-12"], score=5, evidence=["e2"]),
        ]
        out, warnings = dedupe_items(items, "duplicate-item: {item_id} ({n})")
        assert len(out) == 1
        assert out[0].covers == ["article-10", "article-12"]
        assert out[0].evidence == ["e1", "e2"]
        assert out[0].score == 5  # highest wins
        assert warnings == ["duplicate-item: a (2)"]

    def test_preserves_first_seen_order(self):
        items = [
            self._item("b", ["article-12"]),
            self._item("a", ["article-10"]),
            self._item("b", ["article-13"]),
        ]
        out, _ = dedupe_items(items, "{item_id} {n}")
        assert [i.item_id for i in out] == ["b", "a"]


class TestDedupeGaps:
    def test_same_article_label_collapsed(self):
        gaps = [
            "Article 13.2: machine-readable IFU absent; unresolved by candidates.",
            "Article 13.2: the open issue on machine-readable IFU remains unresolved.",
        ]
        assert dedupe_gaps(gaps) == [gaps[0]]

    def test_distinct_articles_kept(self):
        gaps = ["Article 13: x", "Article 14: y"]
        assert dedupe_gaps(gaps) == gaps

    def test_non_article_lines_exact_dedupe_only(self):
        gaps = [
            "open issue not covered (article-13): foo",
            "open issue not covered (article-13): foo",
            "open issue not covered (article-13): bar",
        ]
        assert dedupe_gaps(gaps) == [
            "open issue not covered (article-13): foo",
            "open issue not covered (article-13): bar",
        ]


class TestDimensionFraming:
    def test_frame_defaults(self):
        frame = DimensionFrame.model_validate({"dimension_slug": "transparency"})
        assert frame.in_scope is True
        assert frame.already_addressed == []
        assert frame.residual_gaps == []

    def test_framing_roundtrip(self):
        framing = DimensionFraming.model_validate(
            {
                "frames": [
                    {
                        "dimension_slug": "technical-robustness-safety",
                        "in_scope": True,
                        "relevance_reason": "LLM endpoint exposed to untrusted input.",
                        "already_addressed": ["adversarial testing is performed quarterly"],
                        "residual_gaps": ["no prompt-injection benchmark cited"],
                    }
                ]
            }
        )
        assert framing.frames[0].residual_gaps == ["no prompt-injection benchmark cited"]


class TestDimensionAssessment:
    def test_status_literal_enforced(self):
        with pytest.raises(ValidationError):
            DimensionAssessment.model_validate(
                {
                    "dimension_slug": "transparency",
                    "label": "Transparency",
                    "in_scope": True,
                    "status": "unknown",
                }
            )

    def test_defaults(self):
        da = DimensionAssessment.model_validate(
            {
                "dimension_slug": "transparency",
                "label": "Transparency",
                "in_scope": True,
                "status": "gap",
            }
        )
        assert da.recommended_item_ids == []
        assert da.already_addressed == []
