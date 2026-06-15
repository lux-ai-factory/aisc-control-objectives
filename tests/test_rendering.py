"""Plan → HTML/PDF rendering (Phase 3).

The report is organised by trustworthiness dimension: per dimension its status,
what the card already addresses, the residual gaps, and the recommended items
with their rationale. HTML is asserted on directly; the PDF path is smoke-tested
for valid, non-empty bytes.
"""

from datetime import UTC, datetime

from wizard.models.plan import (
    AssessmentPlan,
    DimensionAssessment,
    ProposedItem,
)
from wizard.rendering import render_plan_html, render_plan_pdf


def _plan() -> AssessmentPlan:
    return AssessmentPlan(
        plan_id="plan-123",
        qualification_id="qual-1",
        system_name="MicroCredit Assist Score",
        created_at=datetime(2026, 6, 13, tzinfo=UTC),
        status="reviewed",
        tests=[
            ProposedItem(
                item_id="ai-fairness-360",
                item_type="test",
                score=5,
                rationale="Verify the claimed quarterly fairness audits independently.",
                dimension_slugs=["diversity-non-discrimination-fairness"],
            )
        ],
        checklists=[
            ProposedItem(
                item_id="transparency-checklist",
                item_type="checklist",
                score=3,
                rationale="Article 13 machine-readable IFU gap.",
                dimension_slugs=["transparency"],
            )
        ],
        dimensions=[
            DimensionAssessment(
                dimension_slug="diversity-non-discrimination-fairness",
                label="Diversity, Non-discrimination and Fairness",
                in_scope=True,
                relevance_reason="Credit scoring affects protected groups.",
                already_addressed=["quarterly fairness audits are described"],
                residual_gaps=["no independent verification evidenced"],
                recommended_item_ids=["ai-fairness-360"],
                status="partial",
            ),
            DimensionAssessment(
                dimension_slug="transparency",
                label="Transparency",
                in_scope=True,
                recommended_item_ids=["transparency-checklist"],
                status="gap",
            ),
        ],
    )


class TestHtml:
    def test_contains_system_and_dimensions(self):
        html = render_plan_html(_plan())
        assert "MicroCredit Assist Score" in html
        assert "Diversity, Non-discrimination and Fairness" in html
        assert "Transparency" in html

    def test_renders_status_and_narrative(self):
        html = render_plan_html(_plan())
        assert "partial" in html.lower()
        assert "quarterly fairness audits are described" in html
        assert "no independent verification evidenced" in html

    def test_renders_recommended_item_rationale(self):
        html = render_plan_html(_plan())
        # the recommendation rationale is resolved from the plan's item list
        assert "Verify the claimed quarterly fairness audits independently." in html
        assert "5/5" in html  # recommendation score badge

    def test_empty_plan_renders(self):
        plan = AssessmentPlan(
            plan_id="p",
            qualification_id="q",
            system_name="Empty System",
            created_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
        html = render_plan_html(plan)
        assert "Empty System" in html


class TestPdf:
    def test_produces_nonempty_pdf_bytes(self):
        pdf = render_plan_pdf(_plan())
        assert isinstance(pdf, bytes)
        assert pdf.startswith(b"%PDF")
        assert len(pdf) > 1000
