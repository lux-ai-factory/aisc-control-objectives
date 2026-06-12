"""Propose → review loop (SPEC §5.2–§5.3).

The orchestrator is deterministic plain Python; proposers and the reviewer are
injected behind small protocols so the loop is testable with fakes and the
LLM-backed adapters stay thin. Two deterministic guards run regardless of what
the agents say:

- items citing an item_id outside the candidate set are dropped *before*
  review (hallucinated-ID defense, in addition to the reviewer's own check);
- open issues not covered by any accepted item are recorded as gaps.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Protocol

from wizard.config import GuardsConfig
from wizard.matching.evidence import card_corpus, find_quote
from wizard.matching.prefilter import ScoredCandidate
from wizard.models.catalogue import ChecklistDoc
from wizard.models.plan import (
    VERDICT_SEVERITY,
    AssessmentPlan,
    ItemVerdict,
    Proposal,
    ProposedItem,
    Review,
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


class Orchestrator:
    def __init__(
        self,
        test_proposer: Proposer,
        checklist_proposer: Proposer,
        reviewer: Reviewer,
        max_rounds: int = 3,
        guards: GuardsConfig | None = None,
    ):
        self.test_proposer = test_proposer
        self.checklist_proposer = checklist_proposer
        self.reviewer = reviewer
        self.max_rounds = max_rounds
        self.guards = guards or GuardsConfig()

    def _apply_guards(
        self,
        card: SystemCard,
        proposal: Proposal,
        known_ids: set[str],
        checklist_lookup: dict[str, ChecklistDoc],
    ) -> Proposal:
        """Deterministic guards (WP1): run after proposers, before the reviewer.

        Order matters: ID guard → evidence → coverage claims → dataset
        pairing (last, so a dataset cascades out with its dropped test).
        Findings land in guard_report, which the reviewer sees.
        """
        report: list[str] = []

        items = []
        for item in proposal.items:
            if item.item_id in known_ids:
                items.append(item)
            else:
                report.append(
                    f"id-not-found: proposed item '{item.item_id}' is not in the candidate set; dropped"
                )

        if self.guards.evidence != "off":
            corpus = card_corpus(card)  # built once; identical for every item
            checked = []
            for item in items:
                verified = [q for q in item.evidence if find_quote(q, corpus)]
                for quote in item.evidence:
                    if quote not in verified:
                        report.append(
                            f"evidence-not-found: {item.item_id}: \"{quote[:80]}\""
                        )
                if verified:
                    checked.append(item.model_copy(update={"evidence": verified}))
                elif self.guards.evidence == "demote":
                    report.append(f"evidence-empty(demoted): {item.item_id}")
                    checked.append(
                        item.model_copy(update={"evidence": [], "priority": "optional"})
                    )
                else:  # drop
                    report.append(f"evidence-empty: {item.item_id}")
            items = checked

        if self.guards.coverage_claims != "off":
            known_keys = card.article_keys()
            claimed = []
            for item in items:
                keys = []
                for key in item.covers:
                    if key not in known_keys:
                        report.append(
                            f"coverage-claim-unknown-key: {item.item_id}: {key}"
                        )
                        continue
                    checklist = checklist_lookup.get(item.item_id)
                    if (
                        item.item_type == "checklist"
                        and checklist is not None
                        and key not in checklist.article_keys()
                    ):
                        report.append(
                            f"coverage-claim-unsupported: {item.item_id}: {key}"
                        )
                        continue
                    keys.append(key)
                claimed.append(item.model_copy(update={"covers": keys}))
            items = claimed

        if self.guards.dataset_pairing != "off":
            present_tests = {i.item_id for i in items if i.item_type == "test"}
            paired = []
            for item in items:
                if item.item_type == "dataset" and item.paired_test_id not in present_tests:
                    report.append(
                        f"dataset-unpaired: {item.item_id} (paired test "
                        f"'{item.paired_test_id}' not in the proposal)"
                    )
                else:
                    paired.append(item)
            items = paired

        return Proposal(
            items=items,
            coverage_gaps=proposal.coverage_gaps,
            guard_report=report,
        )

    def run(
        self,
        card: SystemCard,
        test_candidates: list[ScoredCandidate],
        checklist_candidates: list[ScoredCandidate],
    ) -> AssessmentPlan:
        known_ids = {c.item.slug for c in test_candidates} | {
            c.item.slug for c in checklist_candidates
        }
        checklist_lookup = {
            c.item.slug: c.item
            for c in checklist_candidates
            if isinstance(c.item, ChecklistDoc)
        }
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
            verdict_by_id: dict[str, ItemVerdict] = {}
            for verdict in review.verdicts:
                current = verdict_by_id.get(verdict.item_id)
                if (
                    current is None
                    or VERDICT_SEVERITY[verdict.verdict] > VERDICT_SEVERITY[current.verdict]
                ):
                    verdict_by_id[verdict.item_id] = verdict
            accepted = [
                i
                for i in merged.items
                if verdict_by_id.get(i.item_id) is None
                or verdict_by_id[i.item_id].verdict == "accept"
            ]
            # re-check pairing post-review: a dataset whose test the reviewer
            # rejected must cascade out (the pre-review guard couldn't see this)
            if self.guards.dataset_pairing != "off":
                accepted_tests = {i.item_id for i in accepted if i.item_type == "test"}
                kept = []
                for item in accepted:
                    if (
                        item.item_type == "dataset"
                        and item.paired_test_id not in accepted_tests
                    ):
                        warnings.append(
                            f"dataset-unpaired: {item.item_id} dropped after review "
                            f"(paired test '{item.paired_test_id}' was not accepted)"
                        )
                    else:
                        kept.append(item)
                accepted = kept
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

        coverage: dict[str, list[str]] = {}
        for item in accepted:
            for key in item.covers:
                coverage.setdefault(key, []).append(item.item_id)

        # open issues with no accepted coverage become gaps
        gaps = list(declared_gaps)
        covered_keys = set(coverage)
        for issue, keys in zip(card.open_issues, card.open_issue_keys()):
            missing = keys - covered_keys
            if missing:
                gaps.append(
                    f"open issue not covered ({', '.join(sorted(missing))}): {issue[:120]}"
                )

        return AssessmentPlan(
            plan_id=str(uuid.uuid4()),
            qualification_id=card.qualification_id,
            system_name=card.system_name,
            created_at=datetime.now(timezone.utc),
            status="reviewed" if reviewed else "draft",
            tests=[i for i in accepted if i.item_type == "test"],
            datasets=[i for i in accepted if i.item_type == "dataset"],
            checklists=[i for i in accepted if i.item_type == "checklist"],
            coverage=coverage,
            gaps=gaps,
            warnings=warnings,
            review_rounds=rounds,
        )
