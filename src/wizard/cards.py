"""An uploaded card, its proposed profile, the person's confirmation, and the
verdicts that follow. Kept in memory: a restart loses the uploads, which is
the old wizard's contract too, and persistence is the post-demo step.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Mapping

from pydantic import BaseModel

from wizard.applicability import Verdict, decide
from wizard.control_objectives import ControlObjectiveCatalogue
from wizard.models.profile import Answer, Profile
from wizard.models.system_card import SystemCard
from wizard.models.ontology import Ontology
from wizard.prioritising import Priority, Severity, prioritise
from wizard.risk_mapping import Mapper, MappingRun, map_risks
from wizard.profiling import Extractor, ProfileRun, extract_profile


class CardRecord(BaseModel):
    """One assessed card.

    `run.profile` is the proposal of record: what the model actually said,
    kept whatever a person does afterwards. `profile` is what is in force,
    and starts as a copy of it. They must never be the same object, or an
    in-place write to the one in force would rewrite the proposal too.
    """

    id: str
    created_at: str
    card: SystemCard
    #: What the model did: the proposal, its open findings, how it stopped.
    run: ProfileRun
    #: The profile in force: the proposal until a person confirms or overrides.
    profile: Profile
    confirmed: bool = False
    verdicts: list[Verdict] = []
    #: The assessor's view of what matters for THIS system, and the tiers that
    #: follow from it. Neutral until somebody rates it.
    severity: Severity = Severity()
    priorities: list[Priority] = []
    #: The system's filled AIRO graph, once one is added, and what the mapper
    #: made of its risks. Absent until somebody uploads it.
    ontology: Ontology | None = None
    mapping_run: MappingRun | None = None


class CardStore:
    def __init__(self) -> None:
        self._records: dict[str, CardRecord] = {}

    def save(self, record: CardRecord) -> None:
        self._records[record.id] = record

    def get(self, record_id: str) -> CardRecord | None:
        return self._records.get(record_id)

    def list(self) -> list[CardRecord]:
        return sorted(self._records.values(), key=lambda r: r.created_at, reverse=True)


def _retiered(record: CardRecord, catalogue: ControlObjectiveCatalogue) -> list[Priority]:
    """Tiers follow from the verdicts and the risks, so they are recomputed
    whenever either changes: confirming the profile can take an objective out
    of scope, and rating a risk changes where the work starts."""
    return prioritise(
        catalogue,
        record.verdicts,
        record.severity,
        record.mapping_run.mappings if record.mapping_run else {},
        record.ontology.risks if record.ontology else [],
    )


def assess_card(
    card: SystemCard, extractor: Extractor, catalogue: ControlObjectiveCatalogue
) -> CardRecord:
    """Propose the profile, then let the rules decide. Never raises on a dead
    model: the run records the failure and the verdicts come out undetermined."""
    run = extract_profile(card, extractor)
    record = CardRecord(
        id=uuid.uuid4().hex[:12],
        created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        card=card,
        run=run,
        # a copy: see CardRecord — the proposal of record must not be shared
        profile=run.profile.model_copy(deep=True),
        confirmed=False,
        verdicts=decide(run.profile, catalogue, confirmed=False),
    )
    record.priorities = _retiered(record, catalogue)
    return record


def confirm_profile(
    record: CardRecord, answers: Mapping[str, Answer], catalogue: ControlObjectiveCatalogue
) -> CardRecord:
    """A person's answers override the model's values. The model's quotes stay
    on the record, so it is still visible what the proposal rested on."""
    profile = record.profile.model_copy(deep=True)
    for name, value in answers.items():
        if name in Profile.FACTS and value is not None:
            profile.fact(name).value = value
    updated = record.model_copy(
        update={
            "profile": profile,
            "confirmed": True,
            "verdicts": decide(profile, catalogue, confirmed=True),
        }
    )
    updated.priorities = _retiered(updated, catalogue)
    return updated


def add_ontology(
    record: CardRecord,
    ontology: Ontology,
    mapper: Mapper,
    catalogue: ControlObjectiveCatalogue,
) -> CardRecord:
    """Attach the system's filled AIRO graph and map its risks onto the
    objectives. The tiers follow; what applies does not change."""
    run = map_risks(ontology.risks, mapper, catalogue)
    updated = record.model_copy(update={"ontology": ontology, "mapping_run": run})
    updated.priorities = _retiered(updated, catalogue)
    return updated


def rate_severity(
    record: CardRecord, ratings: dict[str, int], catalogue: ControlObjectiveCatalogue
) -> CardRecord:
    """The assessor says how severe each of this system's risks is; the tiers
    follow. Applicability is untouched: rating changes the order of the work,
    never what is owed."""
    updated = record.model_copy(update={"severity": Severity(ratings=dict(ratings))})
    updated.priorities = _retiered(updated, catalogue)
    return updated
