"""Agent I/O schemas and the assessment plan (SPEC §5.2, §5.3, §6)."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from wizard.dimensions import get as get_dimension
from wizard.dimensions import label_for


class ProposedItem(BaseModel):
    item_id: str
    item_type: Literal["test", "dataset", "checklist"]
    # recommendation strength 1 (weakly relevant) … 5 (strongly recommended);
    # 0 means "not recommended" → excluded by a guard. Clamped, never raises, so
    # a weaker model emitting an out-of-range value can't crash the parse.
    score: int = 3
    rationale: str
    evidence: list[str] = Field(default_factory=list)
    covers: list[str] = Field(default_factory=list)
    # a dataset must be consumed by a test; the pairing is enforced
    # deterministically by the dataset-pairing guard (drop_unpaired_datasets),
    # not as a parse-time validator — a weaker LLM that omits the pairing should
    # have the item dropped with a warning, like any other bad output, never
    # crash the whole proposal parse.
    paired_test_id: str | None = None
    # canonical trustworthiness dimension(s) the catalogue item belongs to;
    # populated by the orchestrator from the candidate set (Phase 0). Labels
    # are resolved on demand from wizard.dimensions, not denormalised here.
    dimension_slugs: list[str] = Field(default_factory=list)

    @field_validator("score")
    @classmethod
    def _clamp_score(cls, value: int) -> int:
        return max(0, min(5, value))


class Proposal(BaseModel):
    items: list[ProposedItem] = Field(default_factory=list)
    coverage_gaps: list[str] = Field(default_factory=list)
    guard_report: list[str] = Field(default_factory=list)


class ItemVerdict(BaseModel):
    item_id: str
    verdict: Literal["accept", "reject", "revise"]
    reasons: list[str] = Field(default_factory=list)


# shared severity order for merging verdicts (worst wins)
VERDICT_SEVERITY = {"accept": 0, "revise": 1, "reject": 2}

def dedupe_items(
    items: list[ProposedItem], warning_template: str
) -> tuple[list[ProposedItem], list[str]]:
    """Collapse duplicate item ids into one row, preserving first-seen order.
    An LLM that proposes the same item twice would otherwise have it flagged as
    a duplicate by the reviewer and excluded — so a *good* item proposed twice
    gets dropped. Merging up front (union covers + evidence, highest score
    wins, keep any pairing) prevents that. `warning_template` is formatted with
    {item_id} and {n} (the row count). Returns (deduped items, warnings)."""
    # key on (id, type): a tool proposed both as a test and as its dataset
    # legitimately shares a slug across types and must NOT be collapsed.
    merged: dict[tuple[str, str], ProposedItem] = {}
    counts: dict[tuple[str, str], int] = {}
    order: list[tuple[str, str]] = []
    for item in items:
        key = (item.item_id, item.item_type)
        if key not in merged:
            merged[key] = item.model_copy()
            order.append(key)
            counts[key] = 1
            continue
        counts[key] += 1
        current = merged[key]
        merged[key] = current.model_copy(
            update={
                "covers": list(dict.fromkeys(current.covers + item.covers)),
                "evidence": list(dict.fromkeys(current.evidence + item.evidence)),
                "score": max(current.score, item.score),
                "paired_test_id": current.paired_test_id or item.paired_test_id,
            }
        )
    warnings = [
        warning_template.format(item_id=key[0], n=counts[key])
        for key in order
        if counts[key] > 1
    ]
    return [merged[key] for key in order], warnings


def dedupe_gaps(gaps: list[str]) -> list[str]:
    """Collapse duplicate coverage-gap lines, preserving first-seen order. Lines
    that share the same leading "Article …:" / "Annex …:" label are treated as
    one gap — the two proposer tracks often restate the same gap in different
    words, which reads as noise. Other lines dedupe only on exact match."""
    seen: set[str] = set()
    out: list[str] = []
    for gap in gaps:
        label = gap.split(":", 1)[0].strip().lower()
        key = label if label.startswith(("article", "annex")) else gap
        if key in seen:
            continue
        seen.add(key)
        out.append(gap)
    return out


def merge_verdicts(verdicts: list[ItemVerdict]) -> dict[str, ItemVerdict]:
    """Merge verdicts by item id: the worst verdict wins, reasons are unioned
    on equal severity. The single definition of this policy — used both for
    duplicate rows within one review and across multi-lens reviews."""
    merged: dict[str, ItemVerdict] = {}
    for verdict in verdicts:
        current = merged.get(verdict.item_id)
        if current is None or VERDICT_SEVERITY[verdict.verdict] > VERDICT_SEVERITY[current.verdict]:
            merged[verdict.item_id] = verdict.model_copy()
        elif VERDICT_SEVERITY[verdict.verdict] == VERDICT_SEVERITY[current.verdict]:
            current.reasons = list(dict.fromkeys(current.reasons + verdict.reasons))
    return merged


def build_coverage(items: list[ProposedItem]) -> dict[str, list[str]]:
    """Derive the coverage matrix (key -> covering item ids) from items.
    The single definition of 'coverage' — used at plan assembly and finalize."""
    coverage: dict[str, list[str]] = {}
    for item in items:
        for key in item.covers:
            coverage.setdefault(key, []).append(item.item_id)
    return coverage


def drop_unpaired_datasets(
    items: list[ProposedItem], warning_template: str
) -> tuple[list[ProposedItem], list[str]]:
    """Enforce the pairing invariant: a dataset survives only if its paired
    test is among the items. `warning_template` is formatted with
    {item_id} and {paired}. Returns (kept items, warnings)."""
    present_tests = {i.item_id for i in items if i.item_type == "test"}
    kept, warnings = [], []
    for item in items:
        if item.item_type == "dataset" and item.paired_test_id not in present_tests:
            warnings.append(
                warning_template.format(item_id=item.item_id, paired=item.paired_test_id)
            )
        else:
            kept.append(item)
    return kept, warnings


class Review(BaseModel):
    verdicts: list[ItemVerdict] = Field(default_factory=list)
    coverage_ok: bool = False
    notes_for_revision: str = ""


# --- Dimension-first mechanism (Phase 1) -----------------------------------

DimensionStatus = Literal["covered", "partial", "gap"]


class DimensionFrame(BaseModel):
    """Framer output for one trustworthiness dimension: whether it is in scope
    for this system, why, what the card already does about it, and what gap
    remains. The framer conditions this on the system's technology + context."""

    dimension_slug: str
    in_scope: bool = True
    relevance_reason: str = ""
    already_addressed: list[str] = Field(default_factory=list)
    residual_gaps: list[str] = Field(default_factory=list)


class DimensionFraming(BaseModel):
    frames: list[DimensionFrame] = Field(default_factory=list)


class DimensionAssessment(BaseModel):
    """A dimension as it appears in the assembled plan: the framer's reasoning
    plus the items selected to close its residual gap and a derived status."""

    dimension_slug: str
    label: str
    in_scope: bool
    relevance_reason: str = ""
    already_addressed: list[str] = Field(default_factory=list)
    residual_gaps: list[str] = Field(default_factory=list)
    recommended_item_ids: list[str] = Field(default_factory=list)
    status: DimensionStatus = "gap"


_FLOOR_NOTE = (
    "deterministic floor: this dimension cannot be auto-marked covered without "
    "an accepted control checklist (high-risk deployment or governance dimension)"
)


def build_dimension_assessments(
    frames: list[DimensionFrame],
    accepted: list[ProposedItem],
    *,
    high_risk: bool,
) -> list[DimensionAssessment]:
    """Group accepted items under the framer's in-scope dimensions, derive a
    status, and apply the D2 floor. Out-of-scope dimensions are dropped.

    Status: no residual gap → covered; gap with recommended items → partial;
    gap with nothing recommended → gap. The floor then vetoes a "covered"
    verdict for `requires_control` dimensions (always) and every dimension in
    a high-risk deployment, unless an accepted control checklist backs it —
    downgrading to "partial" and recording why."""
    assessments: list[DimensionAssessment] = []
    for frame in frames:
        if not frame.in_scope:
            continue
        dim = get_dimension(frame.dimension_slug)
        slug = dim.slug if dim is not None else frame.dimension_slug

        # highest-scored recommendations first
        in_dim = sorted(
            (i for i in accepted if slug in i.dimension_slugs),
            key=lambda i: -i.score,
        )
        recommended_ids = [i.item_id for i in in_dim]
        has_control = any(i.item_type == "checklist" for i in in_dim)
        residual_gaps = list(frame.residual_gaps)

        if residual_gaps:
            status: DimensionStatus = "partial" if recommended_ids else "gap"
        else:
            status = "covered"

        requires_control = dim.requires_control if dim is not None else False
        if status == "covered" and (high_risk or requires_control) and not has_control:
            status = "partial"
            residual_gaps = residual_gaps + [_FLOOR_NOTE]

        assessments.append(
            DimensionAssessment(
                dimension_slug=slug,
                label=label_for(slug),
                in_scope=True,
                relevance_reason=frame.relevance_reason,
                already_addressed=frame.already_addressed,
                residual_gaps=residual_gaps,
                recommended_item_ids=recommended_ids,
                status=status,
            )
        )
    return assessments


class AssessmentPlan(BaseModel):
    plan_id: str
    qualification_id: str
    system_name: str
    created_at: datetime
    status: Literal["draft", "reviewed", "finalized", "exported", "failed"] = "draft"
    tests: list[ProposedItem] = Field(default_factory=list)
    datasets: list[ProposedItem] = Field(default_factory=list)
    checklists: list[ProposedItem] = Field(default_factory=list)
    coverage: dict[str, list[str]] = Field(default_factory=dict)
    gaps: list[str] = Field(default_factory=list)
    # dimension-first view (Phase 1); additive — legacy fields above remain the
    # flat item lists and article-keyed coverage that existing consumers use
    dimensions: list[DimensionAssessment] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    review_rounds: int = 0
    run_config: dict = Field(default_factory=dict)  # effective RunConfig echo (WP0)

    def finalized_without(self, deselect: set[str]) -> AssessmentPlan:
        """Finalize with optional deselection, keeping the plan consistent:
        paired datasets cascade out with their deselected tests, the coverage
        matrix is rebuilt from the remaining items, and coverage keys that
        lose their last covering item are recorded as gaps."""
        tests = [i for i in self.tests if i.item_id not in deselect]
        checklists = [i for i in self.checklists if i.item_id not in deselect]
        remaining_datasets = [i for i in self.datasets if i.item_id not in deselect]
        datasets, pairing_warnings = drop_unpaired_datasets(
            tests + remaining_datasets,
            "dataset-unpaired: {item_id} removed at finalize "
            "(paired test '{paired}' was deselected)",
        )
        datasets = [i for i in datasets if i.item_type == "dataset"]
        warnings = self.warnings + pairing_warnings

        coverage = build_coverage(tests + datasets + checklists)

        gaps = list(self.gaps)
        for key in self.coverage:
            if key not in coverage:
                gaps.append(
                    f"coverage lost at finalize ({key}): all covering items deselected"
                )

        return self.model_copy(
            update={
                "status": "finalized",
                "tests": tests,
                "datasets": datasets,
                "checklists": checklists,
                "coverage": coverage,
                "gaps": gaps,
                "warnings": warnings,
            }
        )
