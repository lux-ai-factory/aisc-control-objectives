"""Which control objectives to do first.

The whole catalogue is the register. What orders it is the system's **own
risks**, the
AIRO chains its AI Card carries, rated by the assessor:

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
             (and only work a risk actually drives: a short Tier 1 is an
              honest answer, a padded one is not)

An objective no identified risk maps to is still owed; it simply is not where
this system's danger lies, so it sorts below every objective that a risk does
drive, and lands in the bottom tier. A voluntary objective sits lower still:
it binds nobody, so it cannot displace a legal duty. Which of the two the CSV
marks voluntary is the author's own note, not a judgement made here.
"""

from __future__ import annotations

from collections.abc import Mapping as MappingABC
from collections.abc import Sequence

from pydantic import BaseModel, Field, field_validator

from aisc_control_objectives.control_objectives import ControlObjectiveCatalogue
from aisc_control_objectives.models.control_objective import ControlObjective
from aisc_control_objectives.models.ontology import OntologyRisk
from aisc_control_objectives.risk_mapping import Mapping

#: What an unrated risk is worth: neutral, so tiers exist before anybody has
#: rated anything.
DEFAULT_SEVERITY = 3

#: What an objective no risk maps to scores. Below the whole 1-5 scale, because
#: "nothing the assessor identified points at this" is weaker than "a risk they
#: rated 1 points at this". Sharing the neutral value put 33 of MCAS's 50 into
#: Tier 2 and made "Next" mean almost nothing.
UNDRIVEN_SCORE = 0.0

#: How many objectives a Tier 1 may hold. Seven is what an assessor can open a
#: workstream on; beyond that the tier stops meaning anything.
TIER_ONE_BUDGET = 7

#: A directly binding duty outranks one grounded only in a draft standard.
BINDING_WEIGHT = 1.0
#: Voluntary objectives sort below every legal duty, whatever the risks say.
VOLUNTARY_FLOOR = -100.0

class Severity(BaseModel):
    """How severe each of this system's risks is: 1 (marginal) to 5 (decisive)."""

    ratings: dict[str, int] = Field(default_factory=dict)

    @field_validator("ratings")
    @classmethod
    def _within_the_scale(cls, ratings: dict[str, int]) -> dict[str, int]:
        # No pattern on the id: it is whatever the exporter named the node,
        # and `prioritise` rejects one this system's graph does not have. A
        # pattern of ours would reject a valid graph with nothing to say.
        for risk_id, rating in ratings.items():
            if not (isinstance(rating, int) and 1 <= rating <= 5):
                raise ValueError(f"{risk_id}: severity {rating!r} is not 1-5")
        return ratings

    def of(self, risk_id: str) -> int:
        return self.ratings.get(risk_id, DEFAULT_SEVERITY)


class Priority(BaseModel):
    objective_id: str
    #: The severity of the worst risk driving it, before any tiebreak. The
    #: threshold for Tier 1 reads this, not `score`: the binding bonus is a
    #: tiebreak among work, and adding it first let a risk rated 2 over the line.
    driving_severity: int = 0
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
        score = UNDRIVEN_SCORE
        reasons = ["no identified risk maps to it: owed, but not where this system's danger is"]

    if "Binding" in objective.grounding_tier_flag:
        score += BINDING_WEIGHT
        reasons.append("a directly binding duty, not only standards-grounded")
    return score, reasons


def _driving_risks(
    mappings: MappingABC[str, Mapping],
    risks: Sequence[OntologyRisk],
    severity: Severity,
) -> dict[str, list[tuple[int, OntologyRisk]]]:
    """objective id -> the risks that drive it, worst first.

    A mapping naming a risk this graph does not have is ignored rather than
    raising: it can only come from a stale run against an older graph, and one
    stale row should not cost the assessor the whole tiering."""
    risks_by_id = {risk.id: risk for risk in risks}
    driving: dict[str, list[tuple[int, OntologyRisk]]] = {}
    for risk_id, mapping in mappings.items():
        risk = risks_by_id.get(risk_id)
        if risk is None:
            continue
        for item in mapping.objectives:
            driving.setdefault(item.objective_id, []).append((severity.of(risk_id), risk))
    for entries in driving.values():
        entries.sort(key=lambda entry: (-entry[0], entry[1].position))
    return driving


def _assign_tiers(scored: list[tuple[float, tuple[int, int], Priority]], budget: int) -> None:
    """Tier 1 is the top of the driven work, and only that.

    Driven-ness is a fact about the mapping, not a threshold on the score: a
    binding objective nothing points at still scores above the undriven floor,
    and "binding" is a tiebreak among work, not a reason to schedule work
    nobody identified. An objective answering a risk the assessor called
    marginal does not belong in "start here" either, however much room is left
    in the budget: a short Tier 1 is an honest answer, a padded one is not.
    """
    scored.sort(key=lambda row: (-row[0], row[1]))
    urgent = 0
    for _score, _, priority in scored:
        if priority.non_binding or not priority.risk_ids:
            priority.tier = 3
        elif priority.driving_severity >= DEFAULT_SEVERITY and urgent < budget:
            priority.tier = 1
            urgent += 1
        else:
            # Driven by a risk the assessor named: work, just not first.
            priority.tier = 2


def prioritise(
    catalogue: ControlObjectiveCatalogue,
    severity: Severity,
    mappings: MappingABC[str, Mapping],
    risks: Sequence[OntologyRisk],
    budget: int = TIER_ONE_BUDGET,
) -> list[Priority]:
    """One Priority per objective, in catalogue order."""
    unknown = sorted(set(severity.ratings) - {risk.id for risk in risks})
    if unknown:
        raise ValueError(f"rated risk(s) not in this system's graph: {', '.join(unknown)}")

    driving = _driving_risks(mappings, risks, severity)
    scored: list[tuple[float, tuple[int, int], Priority]] = []
    priorities: dict[str, Priority] = {}

    for objective in catalogue:
        non_binding = objective.note_tag == "VOLUNTARY"
        priority = Priority(objective_id=objective.id, non_binding=non_binding)
        priorities[objective.id] = priority
        entries = driving.get(objective.id, [])
        priority.risk_ids = [risk.id for _, risk in entries]
        priority.driving_severity = entries[0][0] if entries else 0
        priority.score, priority.reasons = _score(objective, severity, entries, non_binding)
        # catalogue order breaks ties, so the same input always tiers the same
        scored.append((priority.score, objective.sort_key, priority))

    _assign_tiers(scored, budget)
    return [priorities[objective.id] for objective in catalogue]
