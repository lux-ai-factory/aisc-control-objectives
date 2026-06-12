"""Agent I/O schemas and the assessment plan (SPEC §5.2, §5.3, §6)."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator


class ProposedItem(BaseModel):
    item_id: str
    item_type: Literal["test", "dataset", "checklist"]
    priority: Literal["must", "should", "optional"]
    rationale: str
    evidence: list[str] = Field(default_factory=list)
    covers: list[str] = Field(default_factory=list)
    paired_test_id: str | None = None

    @model_validator(mode="after")
    def _dataset_must_be_paired(self) -> "ProposedItem":
        if self.item_type == "dataset" and not self.paired_test_id:
            raise ValueError(
                "dataset items require paired_test_id (the test that consumes the dataset)"
            )
        return self


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


def build_coverage(items: list["ProposedItem"]) -> dict[str, list[str]]:
    """Derive the coverage matrix (key -> covering item ids) from items.
    The single definition of 'coverage' — used at plan assembly and finalize."""
    coverage: dict[str, list[str]] = {}
    for item in items:
        for key in item.covers:
            coverage.setdefault(key, []).append(item.item_id)
    return coverage


def drop_unpaired_datasets(
    items: list["ProposedItem"], warning_template: str
) -> tuple[list["ProposedItem"], list[str]]:
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
    warnings: list[str] = Field(default_factory=list)
    review_rounds: int = 0
    transcript_ref: str = ""
    run_config: dict = Field(default_factory=dict)  # effective RunConfig echo (WP0)

    def finalized_without(self, deselect: set[str]) -> "AssessmentPlan":
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
