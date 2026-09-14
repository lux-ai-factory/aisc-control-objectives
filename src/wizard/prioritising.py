"""Of the objectives that apply, which to do first.

For a high-risk system applicability is all-or-nothing: every AI Act duty
binds, so "what do we owe" answers "all of it", which tells an assessor nothing
about where to start. Tiering is the other half, and what drives it is the
system's **own risks**, the AIRO chains the qualification captured, not an
abstract view of which requirement family matters.

    the assessor rates each risk 1-5
              |
              v
    the mapper says which objectives mitigate that risk
              |
              v
    an objective inherits the severity of the worst risk it mitigates
              |
              v
    tier 1 = the highest scoring, up to a budget an assessor can actually start

An objective no identified risk maps to is still owed; it simply is not where
this system's danger lies, so it sits at neutral. A voluntary objective never
leaves the bottom tier: it binds nobody, so it cannot displace a legal duty.
"""

from __future__ import annotations

import re
from collections.abc import Mapping as MappingABC
from collections.abc import Sequence

from pydantic import BaseModel, Field, field_validator

from wizard.applicability import Verdict
from wizard.control_objectives import ControlObjectiveCatalogue
from wizard.models.control_objective import ControlObjective
from wizard.models.ontology import OntologyRisk
from wizard.risk_mapping import Mapping

#: What an unrated risk is worth, and what an objective no risk maps to gets:
#: neutral, so tiers exist before anybody has rated anything.
DEFAULT_SEVERITY = 3

#: How many objectives a Tier 1 may hold. Seven is what an assessor can open a
#: workstream on; beyond that the tier stops meaning anything.
TIER_ONE_BUDGET = 7

#: A directly binding duty outranks one grounded only in a draft standard.
BINDING_WEIGHT = 1.0
#: Voluntary objectives sort below every legal duty, whatever the risks say.
VOLUNTARY_FLOOR = -100.0

_RISK_ID = re.compile(r"^risk\d+$")


class Severity(BaseModel):
    """How severe each of this system's risks is: 1 (marginal) to 5 (decisive)."""

    ratings: dict[str, int] = Field(default_factory=dict)

    @field_validator("ratings")
    @classmethod
    def _within_the_scale(cls, ratings: dict[str, int]) -> dict[str, int]:
        for risk_id, rating in ratings.items():
            if not _RISK_ID.match(risk_id):
                raise ValueError(f"{risk_id!r} is not a risk id")
            if not (isinstance(rating, int) and 1 <= rating <= 5):
                raise ValueError(f"{risk_id}: severity {rating!r} is not 1-5")
        return ratings

    def of(self, risk_id: str) -> int:
        return self.ratings.get(risk_id, DEFAULT_SEVERITY)


class Priority(BaseModel):
    objective_id: str
    #: 1 start here, 2 next, 3 later. None when the objective does not apply.
    tier: int | None = None
    score: float = 0.0
    #: Printable, one per signal that placed this objective.
    reasons: list[str] = Field(default_factory=list)
    #: The risks this objective mitigates, worst first.
    risk_ids: list[str] = Field(default_factory=list)
    non_binding: bool = False


def _score(
    objective: ControlObjective,
    severity: Severity,
    driving: list[tuple[int, OntologyRisk]],
    non_binding: bool,
) -> tuple[float, list[str]]:
    if non_binding:
        return VOLUNTARY_FLOOR, ["voluntary: binds nobody, so it sorts last"]

    if driving:
        rating, risk = driving[0]
        score = float(rating)
        reasons = [
            f"mitigates a risk rated {rating}/5: {risk.text[:90]}"
            + (f" (and {len(driving) - 1} more)" if len(driving) > 1 else "")
        ]
    else:
        score = float(DEFAULT_SEVERITY)
        reasons = ["no identified risk maps to it: owed, but not where this system's danger is"]

    if "Binding" in objective.grounding_tier_flag:
        score += BINDING_WEIGHT
        reasons.append("a directly binding duty, not only standards-grounded")
    return score, reasons


def prioritise(
    catalogue: ControlObjectiveCatalogue,
    verdicts: list[Verdict],
    severity: Severity,
    mappings: MappingABC[str, Mapping],
    risks: Sequence[OntologyRisk],
    budget: int = TIER_ONE_BUDGET,
) -> list[Priority]:
    """One Priority per objective, in catalogue order. Objectives that do not
    apply carry no tier: there is nothing to schedule."""
    risks_by_id = {risk.id: risk for risk in risks}
    unknown = sorted(set(severity.ratings) - set(risks_by_id))
    if unknown:
        raise ValueError(
            f"rated risk(s) not in this qualification: {', '.join(unknown)}"
        )

    # objective id -> the risks it mitigates, worst first
    driving: dict[str, list[tuple[int, OntologyRisk]]] = {}
    for risk_id, mapping in mappings.items():
        risk = risks_by_id.get(risk_id)
        if risk is None:
            continue
        for item in mapping.objectives:
            driving.setdefault(item.objective_id, []).append((severity.of(risk_id), risk))
    for entries in driving.values():
        entries.sort(key=lambda entry: (-entry[0], entry[1].position))

    by_id = {verdict.objective_id: verdict for verdict in verdicts}
    scored: list[tuple[float, tuple[int, int], Priority]] = []
    priorities: dict[str, Priority] = {}

    for objective in catalogue:
        verdict = by_id[objective.id]
        priority = Priority(objective_id=objective.id, non_binding=verdict.non_binding)
        priorities[objective.id] = priority
        if verdict.applies != "yes":
            continue
        entries = driving.get(objective.id, [])
        priority.risk_ids = [risk.id for _, risk in entries]
        priority.score, priority.reasons = _score(
            objective, severity, entries, verdict.non_binding
        )
        # catalogue order breaks ties, so the same input always tiers the same
        scored.append((priority.score, objective.sort_key, priority))

    scored.sort(key=lambda row: (-row[0], row[1]))
    for position, (score, _, priority) in enumerate(scored):
        if priority.non_binding:
            priority.tier = 3
        elif position < budget:
            priority.tier = 1
        elif score >= DEFAULT_SEVERITY:
            priority.tier = 2
        else:
            priority.tier = 3

    return [priorities[objective.id] for objective in catalogue]
