"""Pure assembly of per-dimension assessments + the D2 deterministic floor.

`build_dimension_assessments` groups accepted items under the framer's
in-scope dimensions, derives a status, and applies the floor: in a high-risk
deployment, or for a `requires_control` dimension, a dimension cannot end
"covered" without an accepted control checklist.
"""

from wizard.models.plan import (
    DimensionFrame,
    ProposedItem,
    build_dimension_assessments,
)


def _item(item_id, item_type, dims):
    return ProposedItem(
        item_id=item_id,
        item_type=item_type,
        score=5,
        rationale="r",
        dimension_slugs=dims,
    )


class TestStatusDerivation:
    def test_no_gap_no_reco_is_covered(self):
        frame = DimensionFrame(dimension_slug="transparency", residual_gaps=[])
        [da] = build_dimension_assessments([frame], [], high_risk=False)
        assert da.status == "covered"
        assert da.label == "Transparency"

    def test_gap_with_recommendation_is_partial(self):
        frame = DimensionFrame(
            dimension_slug="transparency", residual_gaps=["no model card"]
        )
        items = [_item("vader", "test", ["transparency"])]
        [da] = build_dimension_assessments([frame], items, high_risk=False)
        assert da.status == "partial"
        assert da.recommended_item_ids == ["vader"]

    def test_gap_with_no_recommendation_is_gap(self):
        frame = DimensionFrame(
            dimension_slug="transparency", residual_gaps=["no model card"]
        )
        [da] = build_dimension_assessments([frame], [], high_risk=False)
        assert da.status == "gap"

    def test_out_of_scope_dimensions_excluded(self):
        frames = [
            DimensionFrame(dimension_slug="transparency", in_scope=True),
            DimensionFrame(dimension_slug="accountability", in_scope=False),
        ]
        result = build_dimension_assessments(frames, [], high_risk=False)
        assert [d.dimension_slug for d in result] == ["transparency"]

    def test_recommended_items_filtered_by_dimension(self):
        frame = DimensionFrame(dimension_slug="transparency")
        items = [
            _item("vader", "test", ["transparency"]),
            _item("ai-fairness-360", "test", ["diversity-non-discrimination-fairness"]),
        ]
        [da] = build_dimension_assessments([frame], items, high_risk=False)
        assert da.recommended_item_ids == ["vader"]


class TestDeterministicFloor:
    def test_requires_control_dim_cannot_be_covered_without_control(self):
        # risk-management is control-only → requires_control=True
        frame = DimensionFrame(dimension_slug="risk-management", residual_gaps=[])
        [da] = build_dimension_assessments([frame], [], high_risk=False)
        assert da.status == "partial"
        assert any("floor" in g.lower() for g in da.residual_gaps)

    def test_requires_control_dim_covered_when_control_accepted(self):
        frame = DimensionFrame(dimension_slug="risk-management", residual_gaps=[])
        items = [_item("risk-checklist", "checklist", ["risk-management"])]
        [da] = build_dimension_assessments([frame], items, high_risk=False)
        assert da.status == "covered"

    def test_high_risk_sector_floors_a_shared_dimension(self):
        # transparency is not requires_control, but a high-risk deployment
        # still demands a control to auto-cover it
        frame = DimensionFrame(dimension_slug="transparency", residual_gaps=[])
        [da] = build_dimension_assessments([frame], [], high_risk=True)
        assert da.status == "partial"

    def test_high_risk_sector_covered_with_control(self):
        frame = DimensionFrame(dimension_slug="transparency", residual_gaps=[])
        items = [_item("transparency-checklist", "checklist", ["transparency"])]
        [da] = build_dimension_assessments([frame], items, high_risk=True)
        assert da.status == "covered"

    def test_low_risk_shared_dimension_not_floored(self):
        frame = DimensionFrame(dimension_slug="transparency", residual_gaps=[])
        items = [_item("vader", "test", ["transparency"])]
        [da] = build_dimension_assessments([frame], items, high_risk=False)
        assert da.status == "covered"
