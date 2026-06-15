"""Propose → review loop (SPEC §5.2–§5.3).

The orchestrator is deterministic plain Python; proposers and the reviewer are
injected behind small protocols so the loop is testable with fakes and the
LLM-backed adapters stay thin. Four deterministic guards run after the
proposers and before the reviewer — the ID guard unconditionally, the rest
per GuardsConfig policy:

- _drop_unknown_ids: hallucinated item ids never reach the reviewer
- _check_evidence (G1): evidence quotes must occur in the card
- _check_coverage_claims (G2): cover claims must be card-known and, for
  checklists, supported by the checklist's own questions
- _drop_unpaired_datasets (G3): datasets cascade out with their tests

Independently of the reviewer, open issues not covered by any accepted item
are recorded as gaps.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Literal, Protocol

from wizard.config import GuardsConfig
from wizard.dimensions import all_dimensions
from wizard.matching.evidence import card_corpus, verify_evidence
from wizard.matching.prefilter import ScoredCandidate, known_candidate_ids
from wizard.models.catalogue import ChecklistDoc
from wizard.models.plan import (
    AssessmentPlan,
    DimensionFrame,
    DimensionFraming,
    Proposal,
    ProposedItem,
    Review,
    build_coverage,
    build_dimension_assessments,
    dedupe_gaps,
    dedupe_items,
    drop_unpaired_datasets,
    merge_verdicts,
)
from wizard.models.system_card import SystemCard


class Proposer(Protocol):
    def propose(
        self,
        card: SystemCard,
        candidates: list[ScoredCandidate],
        revision_notes: str | None = None,
        prior: Proposal | None = None,
    ) -> Proposal: ...


class Reviewer(Protocol):
    def review(self, card: SystemCard, proposal: Proposal) -> Review: ...


class Framer(Protocol):
    def frame(
        self, card: SystemCard, dimension_slugs: list[str]
    ) -> DimensionFraming: ...


def _drop_unknown_ids(
    items: list[ProposedItem], known_ids: set[str], report: list[str]
) -> list[ProposedItem]:
    """Hallucination guard: only candidate-set ids may be proposed."""
    kept = []
    for item in items:
        if item.item_id in known_ids:
            kept.append(item)
        else:
            report.append(
                f"id-not-found: proposed item '{item.item_id}' is not in the candidate set; dropped"
            )
    return kept


def _drop_zero_score(
    items: list[ProposedItem], report: list[str]
) -> list[ProposedItem]:
    """A score of 0 means 'not recommended' — exclude it (the proposer should
    simply omit such items; this is the backstop)."""
    kept = []
    for item in items:
        if item.score <= 0:
            report.append(f"excluded-zero-score: {item.item_id}")
        else:
            kept.append(item)
    return kept


def _check_evidence(
    items: list[ProposedItem],
    card: SystemCard,
    policy: Literal["drop", "demote"],  # caller skips when "off"
    report: list[str],
) -> list[ProposedItem]:
    """G1: evidence quotes must occur in the card; unverifiable ones are
    stripped, and items left with none are dropped or demoted per policy."""
    corpus = card_corpus(card)  # built once; identical for every item
    kept = []
    for item in items:
        verified = verify_evidence(item, corpus)
        for quote in item.evidence:
            if quote not in verified:
                report.append(f'evidence-not-found: {item.item_id}: "{quote[:80]}"')
        if verified:
            kept.append(item.model_copy(update={"evidence": verified}))
        elif policy == "demote":
            report.append(f"evidence-empty(demoted): {item.item_id}")
            kept.append(item.model_copy(update={"evidence": [], "score": 1}))
        else:  # drop
            report.append(f"evidence-empty: {item.item_id}")
    return kept


def _check_coverage_claims(
    items: list[ProposedItem],
    card: SystemCard,
    checklist_lookup: dict[str, ChecklistDoc],
    report: list[str],
) -> list[ProposedItem]:
    """G2: cover claims must reference card-known keys, and a checklist can
    only claim articles its own questions cite."""
    known_keys = card.article_keys()
    kept = []
    for item in items:
        checklist = checklist_lookup.get(item.item_id)
        supported = checklist.article_keys() if checklist is not None else None
        keys = []
        for key in item.covers:
            if key not in known_keys:
                report.append(f"coverage-claim-unknown-key: {item.item_id}: {key}")
            elif (
                item.item_type == "checklist"
                and supported is not None
                and key not in supported
            ):
                report.append(f"coverage-claim-unsupported: {item.item_id}: {key}")
            else:
                keys.append(key)
        kept.append(item.model_copy(update={"covers": keys}))
    return kept


def _enrich_dimensions(
    items: list[ProposedItem], dimension_lookup: dict[str, list[str]]
) -> list[ProposedItem]:
    """Stamp each accepted item with the trustworthiness dimension(s) of its
    catalogue candidate (Phase 0). The proposer emits only an item_id; the
    dimension is a property of the candidate, so it is resolved here, after the
    item has survived the guards and review."""
    return [
        item.model_copy(
            update={"dimension_slugs": dimension_lookup.get(item.item_id, [])}
        )
        for item in items
    ]


def _drop_unpaired_datasets(
    items: list[ProposedItem], report: list[str]
) -> list[ProposedItem]:
    """G3: a dataset must be paired with a test present in the same proposal."""
    kept, warnings = drop_unpaired_datasets(
        items, "dataset-unpaired: {item_id} (paired test '{paired}' not in the proposal)"
    )
    report.extend(warnings)
    return kept


def _candidate_dimensions(dimension_lookup: dict[str, list[str]]) -> list[str]:
    """The trustworthiness dimensions present across the candidate set, in
    registry order — the dimensions the framer is asked to assess."""
    present = {slug for slugs in dimension_lookup.values() for slug in slugs}
    return [d.slug for d in all_dimensions() if d.slug in present]


def _open_issue_gaps(card: SystemCard, covered_keys: set[str]) -> list[str]:
    """One gap line per open issue whose coverage keys are not all covered."""
    gaps = []
    for issue, keys in zip(card.open_issues, card.open_issue_keys()):
        missing = keys - covered_keys
        if missing:
            gaps.append(
                f"open issue not covered ({', '.join(sorted(missing))}): {issue[:120]}"
            )
    return gaps


class Orchestrator:
    def __init__(
        self,
        test_proposer: Proposer,
        checklist_proposer: Proposer,
        reviewer: Reviewer,
        max_rounds: int = 3,
        guards: GuardsConfig | None = None,
        framer: Framer | None = None,
        high_risk: bool = False,
    ):
        self.test_proposer = test_proposer
        self.checklist_proposer = checklist_proposer
        self.reviewer = reviewer
        self.max_rounds = max_rounds
        self.guards = guards or GuardsConfig()
        # framer is optional: without it the loop behaves exactly as before and
        # dimensions are assembled from the accepted items' tags alone (every
        # candidate dimension in scope, no narrative). high_risk drives the D2
        # floor and is supplied by the runner from the card's sector + config.
        self.framer = framer
        self.high_risk = high_risk

    def _apply_guards(
        self,
        card: SystemCard,
        proposal: Proposal,
        known_ids: set[str],
        checklist_lookup: dict[str, ChecklistDoc],
    ) -> Proposal:
        """Deterministic guards (WP1): run after proposers, before the reviewer.

        Order matters: dedup → ID guard → evidence → coverage claims → dataset
        pairing (last, so a dataset cascades out with its dropped test). Dedup
        runs first so the reviewer never sees (and rejects) duplicate rows.
        Findings land in guard_report, which the reviewer sees.
        """
        report: list[str] = []
        items, dup_warnings = dedupe_items(
            proposal.items, "duplicate-item: {item_id} ({n} rows merged into one)"
        )
        report.extend(dup_warnings)
        items = _drop_zero_score(items, report)
        items = _drop_unknown_ids(items, known_ids, report)
        if self.guards.evidence != "off":
            items = _check_evidence(items, card, self.guards.evidence, report)
        if self.guards.coverage_claims != "off":
            items = _check_coverage_claims(items, card, checklist_lookup, report)
        if self.guards.dataset_pairing != "off":
            items = _drop_unpaired_datasets(items, report)
        return Proposal(
            items=items,
            coverage_gaps=proposal.coverage_gaps,
            guard_report=report,
        )

    def _frame_dimensions(
        self, card: SystemCard, dimension_lookup: dict[str, list[str]]
    ) -> DimensionFraming:
        """Frame the candidate dimensions up front (Phase 1): scope + what the
        card already addresses, consumed at assembly. Without a framer, every
        candidate dimension is framed in-scope with no narrative."""
        ordered_dims = _candidate_dimensions(dimension_lookup)
        if self.framer is not None:
            return self.framer.frame(card, ordered_dims)
        return DimensionFraming(
            frames=[DimensionFrame(dimension_slug=slug) for slug in ordered_dims]
        )

    def run(
        self,
        card: SystemCard,
        test_candidates: list[ScoredCandidate],
        checklist_candidates: list[ScoredCandidate],
    ) -> AssessmentPlan:
        known_ids = known_candidate_ids(test_candidates, checklist_candidates)
        checklist_lookup = {
            c.item.slug: c.item
            for c in checklist_candidates
            if isinstance(c.item, ChecklistDoc)
        }
        dimension_lookup = {
            c.item.slug: c.item.dimension_slugs()
            for c in test_candidates + checklist_candidates
        }
        framing = self._frame_dimensions(card, dimension_lookup)

        warnings: list[str] = []
        accepted: list[ProposedItem] = []
        declared_gaps: list[str] = []
        notes: str | None = None
        prior: Proposal | None = None
        reviewed = False
        rounds = 0

        for rounds in range(1, self.max_rounds + 1):
            tests = self.test_proposer.propose(
                card, test_candidates, revision_notes=notes, prior=prior
            )
            checks = self.checklist_proposer.propose(
                card, checklist_candidates, revision_notes=notes, prior=prior
            )
            merged = Proposal(
                items=tests.items + checks.items,
                coverage_gaps=tests.coverage_gaps + checks.coverage_gaps,
            )

            merged = self._apply_guards(card, merged, known_ids, checklist_lookup)
            warnings.extend(merged.guard_report)

            review = self.reviewer.review(card, merged)
            # worst verdict wins on duplicate item_ids — an LLM emitting
            # reject-then-accept rows for one item must not flip it to accepted
            verdict_by_id = merge_verdicts(review.verdicts)
            accepted = [
                i
                for i in merged.items
                if verdict_by_id.get(i.item_id) is None
                or verdict_by_id[i.item_id].verdict == "accept"
            ]
            # re-check pairing post-review: a dataset whose test the reviewer
            # rejected must cascade out (the pre-review guard couldn't see this)
            if self.guards.dataset_pairing != "off":
                accepted, pairing_warnings = drop_unpaired_datasets(
                    accepted,
                    "dataset-unpaired: {item_id} dropped after review "
                    "(paired test '{paired}' was not accepted)",
                )
                warnings.extend(pairing_warnings)
            declared_gaps = list(merged.coverage_gaps)

            outstanding = [
                v for v in verdict_by_id.values() if v.verdict != "accept"
            ]
            if not outstanding and review.coverage_ok:
                reviewed = True
                break
            notes = (
                review.notes_for_revision
                or "; ".join(
                    f"{v.item_id}: {', '.join(v.reasons)}" for v in outstanding
                )
                # a reviewer can signal incomplete coverage without notes —
                # synthesize a nudge so the next round's prompt actually changes
                or "Reviewer marked coverage incomplete without specifics: ensure every "
                "open issue is covered by an item or declared in coverage_gaps with a reason."
            )
            prior = merged

        if not reviewed:
            warnings.append(
                f"review not converged after {rounds} round(s); last reviewer notes: {notes or 'n/a'}"
            )

        accepted = _enrich_dimensions(accepted, dimension_lookup)
        # surface the strongest recommendations first
        accepted.sort(key=lambda i: -i.score)
        coverage = build_coverage(accepted)

        # Dimension-first view (Phase 1): group accepted items under the framed
        # in-scope dimensions and apply the D2 floor.
        dimensions = build_dimension_assessments(
            framing.frames, accepted, high_risk=self.high_risk
        )

        # open issues with no accepted coverage become gaps
        gaps = dedupe_gaps(declared_gaps + _open_issue_gaps(card, set(coverage)))

        return AssessmentPlan(
            plan_id=str(uuid.uuid4()),
            qualification_id=card.qualification_id,
            system_name=card.system_name,
            created_at=datetime.now(UTC),
            status="reviewed" if reviewed else "draft",
            tests=[i for i in accepted if i.item_type == "test"],
            datasets=[i for i in accepted if i.item_type == "dataset"],
            checklists=[i for i in accepted if i.item_type == "checklist"],
            coverage=coverage,
            gaps=gaps,
            dimensions=dimensions,
            warnings=warnings,
            review_rounds=rounds,
        )
