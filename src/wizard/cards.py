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
from wizard.profiling import Extractor, ProfileRun, extract_profile


class CardRecord(BaseModel):
    id: str
    created_at: str
    card: SystemCard
    #: What the model did: the proposal, its open findings, how it stopped.
    run: ProfileRun
    #: The profile in force: the proposal until a person confirms or overrides.
    profile: Profile
    confirmed: bool = False
    verdicts: list[Verdict] = []


class CardStore:
    def __init__(self) -> None:
        self._records: dict[str, CardRecord] = {}

    def save(self, record: CardRecord) -> None:
        self._records[record.id] = record

    def get(self, record_id: str) -> CardRecord | None:
        return self._records.get(record_id)

    def list(self) -> list[CardRecord]:
        return sorted(self._records.values(), key=lambda r: r.created_at, reverse=True)


def assess_card(
    card: SystemCard, extractor: Extractor, catalogue: ControlObjectiveCatalogue
) -> CardRecord:
    """Propose the profile, then let the rules decide. Never raises on a dead
    model: the run records the failure and the verdicts come out undetermined."""
    run = extract_profile(card, extractor)
    return CardRecord(
        id=uuid.uuid4().hex[:12],
        created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        card=card,
        run=run,
        profile=run.profile,
        confirmed=False,
        verdicts=decide(run.profile, catalogue, confirmed=False),
    )


def confirm_profile(
    record: CardRecord, answers: Mapping[str, Answer], catalogue: ControlObjectiveCatalogue
) -> CardRecord:
    """A person's answers override the model's values. The model's quotes stay
    on the record, so it is still visible what the proposal rested on."""
    profile = record.profile.model_copy(deep=True)
    for name, value in answers.items():
        if name in Profile.FACTS and value is not None:
            profile.fact(name).value = value
    return record.model_copy(
        update={
            "profile": profile,
            "confirmed": True,
            "verdicts": decide(profile, catalogue, confirmed=True),
        }
    )
