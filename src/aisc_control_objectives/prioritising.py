"""Which control objectives to do first.

The whole catalogue is the register. What orders it is the system's **own
risks**, the
AIRO chains its AI Card carries, rated by the assessor:

    the assessor rates each risk 1-5
              |
              v
    the mapping (the AI's or a person's) says which objectives mitigate that risk
              |
              v
    an objective scores the sum of the severities of the risks it mitigates:
    S = how many risks point at it x how severe they are on average
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

#: What an objective no risk maps to scores: an empty sum. Below any risk the
#: assessor rated, because "nothing the assessor identified points at this" is
#: weaker than "a risk they rated 1 points at this".
UNDRIVEN_SCORE = 0.0

#: The longest comment a severity may carry.
MAX_COMMENT = 1000

#: How many objectives a Tier 1 may hold. Seven is what an assessor can open a
#: workstream on; beyond that the tier stops meaning anything.
TIER_ONE_BUDGET = 7


class Severity(BaseModel):
    """How severe each of this system's risks is: 1 (marginal) to 5 (decisive)."""

    ratings: dict[str, int] = Field(default_factory=dict)
    #: Why a risk is rated as it is, optional; only risks with a comment are here.
    comments: dict[str, str] = Field(default_factory=dict)

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

    @field_validator("comments")
    @classmethod
    def _short_enough(cls, comments: dict[str, str]) -> dict[str, str]:
        for risk_id, comment in comments.items():
            if len(comment) > MAX_COMMENT:
                raise ValueError(f"{risk_id}: a comment is at most {MAX_COMMENT} characters")
        return comments

    def of(self, risk_id: str) -> int:
        return self.ratings.get(risk_id, DEFAULT_SEVERITY)


class Priority(BaseModel):
    objective_id: str
    #: The severity of the worst risk driving it. The threshold for Tier 1 reads
    #: this, not `score`: many marginal risks are still marginal work.
    driving_severity: int = 0
    #: 1 start here, 2 next, 3 later. None when the objective does not apply.
    tier: int | None = None
    #: The sum of the severities of the risks it mitigates.
    score: float = 0.0
    #: 1 ... 50: score, then the worst single risk, then a binding duty first,
    #: then catalogue order. A voluntary objective ranks after every legal duty.
    rank: int = 0
    #: Printable, one per signal that placed this objective.
    reasons: list[str] = Field(default_factory=list)
    #: The risks this objective mitigates, worst first.
    risk_ids: list[str] = Field(default_factory=list)
    non_binding: bool = False


def _and(values: list[int]) -> str:
    words = [str(v) for v in values]
    return words[0] if len(words) == 1 else ", ".join(words[:-1]) + " and " + words[-1]


def _score(
    objective: ControlObjective,
    driving: list[tuple[int, OntologyRisk]],
    non_binding: bool,
) -> tuple[float, list[str]]:
    if driving:
        ratings = [rating for rating, _ in driving]
        score = float(sum(ratings))
        n = len(driving)
        reasons = [f"mitigates {n} risk{'s' if n != 1 else ''} rated {_and(ratings)}: score {sum(ratings)}"
                   f" (worst: {driving[0][1].text[:90]})"]
    else:
        score = UNDRIVEN_SCORE
        reasons = ["no identified risk maps to it: owed, but not where this system's danger is"]
    if non_binding:
        reasons.append("voluntary: binds nobody, so it sorts last")
    elif "Binding" in objective.grounding_tier_flag:
        reasons.append("a directly binding duty: ahead of an equal score that is only standards-grounded")
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


def _assign_tiers(ordered: list[Priority], budget: int) -> None:
    """Tier 1 is the top of the driven work, and only that.

    An objective answering only risks the assessor called marginal does not
    belong in "start here", however much room is left in the budget: a short
    Tier 1 is an honest answer, a padded one is not.
    """
    urgent = 0
    for priority in ordered:
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
    keyed: list[tuple[tuple, Priority]] = []
    priorities: dict[str, Priority] = {}

    for objective in catalogue:
        non_binding = objective.note_tag == "VOLUNTARY"
        priority = Priority(objective_id=objective.id, non_binding=non_binding)
        priorities[objective.id] = priority
        entries = driving.get(objective.id, [])
        priority.risk_ids = [risk.id for _, risk in entries]
        priority.driving_severity = entries[0][0] if entries else 0
        priority.score, priority.reasons = _score(objective, entries, non_binding)
        binding = "Binding" in objective.grounding_tier_flag
        # catalogue order breaks the last tie, so the same input always ranks the same
        keyed.append(((non_binding, -priority.score, -priority.driving_severity, not binding,
                       objective.sort_key), priority))

    keyed.sort(key=lambda row: row[0])
    ordered = [priority for _, priority in keyed]
    for rank, priority in enumerate(ordered, 1):
        priority.rank = rank
    _assign_tiers(ordered, budget)
    return [priorities[objective.id] for objective in catalogue]
