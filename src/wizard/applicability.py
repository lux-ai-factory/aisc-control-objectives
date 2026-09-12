"""Which control objectives bind a given system: a rules table, not a model.

Each legal basis of an objective sits in a regime (see `ControlObjective.regimes`)
and each regime is governed by one fact of the profile:

    ai_act       the AI Act's Chapter III duties        <- high_risk
    gdpr         GDPR-only bases                        <- personal_data
    conditional  the row's own trigger (Art. 50)        <- the note's trigger fact
    voluntary    Art. 95 codes of conduct               <- nothing: always in, non-binding

An objective with several bases applies as soon as one of them applies, and the
reason names the basis that carried it. The role is always the provider.
"""

from __future__ import annotations

from pydantic import BaseModel

from wizard.control_objectives import ControlObjectiveCatalogue
from wizard.models.control_objective import ControlObjective
from wizard.models.profile import Answer, Profile

#: The fact that governs a regime; `conditional` takes the row's trigger fact.
FACT_FOR_REGIME = {"ai_act": "high_risk", "gdpr": "personal_data"}

#: Reason clauses per fact, for a basis that applies / does not / is unsettled.
CLAUSES: dict[str, dict[Answer, str]] = {
    "high_risk": {
        "yes": "High-risk AI system: {basis} binds the provider.",
        "no": "Not a high-risk AI system: {basis} does not apply.",
        "undetermined": "High-risk status not yet determined; {basis} pending.",
    },
    "personal_data": {
        "yes": "Personal data is processed: {basis} applies.",
        "no": "No personal data is processed: {basis} does not apply.",
        "undetermined": "Personal-data processing not yet determined; {basis} pending.",
    },
    "interacts_with_natural_persons": {
        "yes": "The system interacts with natural persons: {basis} applies.",
        "no": "The system does not interact with natural persons: {basis} does not apply.",
        "undetermined": "Interaction with natural persons not yet determined; {basis} pending.",
    },
}


class Verdict(BaseModel):
    objective_id: str
    applies: Answer
    #: Printable sentences a reviewer can check against the legal basis.
    reason: str
    #: Voluntary objectives are in the register but bind nobody.
    non_binding: bool = False
    #: True while the governing facts are the model's proposal, not a person's.
    provisional: bool = False


def _verdict_for(objective: ControlObjective, profile: Profile, confirmed: bool) -> Verdict:
    if "voluntary" in objective.regimes:
        return Verdict(
            objective_id=objective.id,
            applies="yes",
            reason=f"Voluntary: {objective.legal_basis} (codes of conduct). In the register as non-binding.",
            non_binding=True,
            provisional=False,
        )

    # One (fact, basis, answer) per legal basis; the strongest answer wins.
    answered: list[tuple[str, str, Answer]] = []
    for basis, regime in zip(objective.legal_bases, objective.regimes):
        fact = FACT_FOR_REGIME.get(regime) or objective.trigger_fact
        answered.append((fact, basis, profile.fact(fact).value))

    for outcome in ("yes", "undetermined", "no"):
        carrying = [(fact, basis) for fact, basis, answer in answered if answer == outcome]
        if carrying:
            break
    return Verdict(
        objective_id=objective.id,
        applies=outcome,
        reason=" ".join(CLAUSES[fact][outcome].format(basis=basis) for fact, basis in carrying),
        provisional=not confirmed,
    )


def decide(
    profile: Profile, catalogue: ControlObjectiveCatalogue, confirmed: bool = False
) -> list[Verdict]:
    """One verdict per objective, in catalogue order."""
    return [_verdict_for(objective, profile, confirmed) for objective in catalogue]
