"""Which control objectives matter most (risk and control matrix, 2026-10-01).

What orders the objectives is the system's **own risks**, the AIRO chains its AI Card carries,
rated by the assessor the way risk teams rate risks:

    each risk: impact (1-5) x likelihood (1-5) = its rating, 1 to 25
              |
              v
    the matrix (the AI's suggestions or a person's) says which objectives mitigate it
              |
              v
    an objective scores the sum of the ratings of the risks it mitigates
              |
              v
    key objectives = by default the first seven driven by a High or Critical risk;
                     the assessor turns key on or off for any objective

An objective no identified risk maps to scores nothing: it is still owed, but it is not where this
system's danger lies. A voluntary objective binds nobody, so it ranks after every legal duty and is
never key by default. Which objectives are voluntary is the CSV author's own note.
"""

from __future__ import annotations

from collections.abc import Mapping as MappingABC
from collections.abc import Sequence

from pydantic import BaseModel, Field, field_validator

from aisc_control_objectives.control_objectives import ControlObjectiveCatalogue
from aisc_control_objectives.models.control_objective import ControlObjective
from aisc_control_objectives.models.ontology import OntologyRisk
from aisc_control_objectives.risk_mapping import Mapping

#: What an unrated impact or likelihood counts: the middle of the scale, so the matrix orders
#: before anybody has rated anything (an unrated risk is 3 x 3 = 9, Medium).
DEFAULT_PART = 3

#: The longest comment a rating may carry.
MAX_COMMENT = 1000

#: How many objectives are key by default. Seven is what an assessor can open a workstream on.
KEY_BUDGET = 7

#: The lowest rating that makes an objective key by default: High.
KEY_RATING = 10

#: The 5 x 5 bands (ISO 31000, COSO ERM): the highest rating in each, and its name.
BANDS = ((4, "Low"), (9, "Medium"), (16, "High"), (25, "Critical"))


def band(rating: int) -> str:
    """Low 1-4, Medium 5-9, High 10-16, Critical 17-25."""
    for top, name in BANDS:
        if rating <= top:
            return name
    raise ValueError(f"a rating is 1 to 25, not {rating}")


class Severity(BaseModel):
    """How much each of this system's risks matters: impact x likelihood, each 1-5."""

    impact: dict[str, int] = Field(default_factory=dict)
    likelihood: dict[str, int] = Field(default_factory=dict)
    #: Why a risk is rated as it is, optional; only risks with a comment are here.
    comments: dict[str, str] = Field(default_factory=dict)

    @field_validator("impact", "likelihood")
    @classmethod
    def _within_the_scale(cls, values: dict[str, int]) -> dict[str, int]:
        # No pattern on the id: it is whatever the exporter named the node, and `prioritise`
        # rejects one this system's graph does not have.
        for risk_id, value in values.items():
            if not (isinstance(value, int) and 1 <= value <= 5):
                raise ValueError(f"{risk_id}: {value!r} is not 1-5")
        return values

    @field_validator("comments")
    @classmethod
    def _short_enough(cls, comments: dict[str, str]) -> dict[str, str]:
        for risk_id, comment in comments.items():
            if len(comment) > MAX_COMMENT:
                raise ValueError(f"{risk_id}: a comment is at most {MAX_COMMENT} characters")
        return comments

    def of(self, risk_id: str) -> int:
        """The rating, 1 to 25; an unrated part counts DEFAULT_PART."""
        return self.impact.get(risk_id, DEFAULT_PART) * self.likelihood.get(risk_id, DEFAULT_PART)

    def rated(self, risk_id: str) -> bool:
        return risk_id in self.impact or risk_id in self.likelihood

    @property
    def risk_ids(self) -> set[str]:
        return set(self.impact) | set(self.likelihood) | set(self.comments)


class Priority(BaseModel):
    objective_id: str
    #: The sum of the ratings of the risks it mitigates.
    score: float = 0.0
    #: The highest single rating among them; 0 when none.
    top_rating: int = 0
    #: 1 ... n: score, then top rating, then a binding duty first, then catalogue order.
    rank: int = 0
    #: Key: the assessor's choice when they made one, else `key_default`.
    key: bool = False
    key_default: bool = False
    #: Printable, one per signal that placed this objective.
    reasons: list[str] = Field(default_factory=list)
    #: The risks this objective mitigates, highest rated first.
    risk_ids: list[str] = Field(default_factory=list)
    non_binding: bool = False


def _rated_list(ratings: list[int]) -> str:
    words = [f"{r} ({band(r)})" for r in ratings]
    return words[0] if len(words) == 1 else ", ".join(words[:-1]) + " and " + words[-1]


def _score(objective: ControlObjective, driving: list[tuple[int, OntologyRisk]],
           non_binding: bool) -> tuple[float, list[str]]:
    if driving:
        ratings = [rating for rating, _ in driving]
        n = len(driving)
        reasons = [f"mitigates {n} risk{'s' if n != 1 else ''} rated {_rated_list(ratings)}: score {sum(ratings)}"
                   f" (highest: {driving[0][1].text[:90]})"]
        score = float(sum(ratings))
    else:
        score = 0.0
        reasons = ["no identified risk maps to it: owed, but not where this system's danger is"]
    if non_binding:
        reasons.append("voluntary: binds nobody, so it ranks last")
    elif "Binding" in objective.grounding_tier_flag:
        reasons.append("a directly binding duty: ahead of an equal score that is only standards-grounded")
    return score, reasons


def _driving_risks(
    mappings: MappingABC[str, Mapping],
    risks: Sequence[OntologyRisk],
    severity: Severity,
) -> dict[str, list[tuple[int, OntologyRisk]]]:
    """objective id -> the risks that drive it, highest rated first.

    A mapping naming a risk this graph does not have is ignored rather than raising: it can only
    come from a stale run against an older graph."""
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


def prioritise(
    catalogue: ControlObjectiveCatalogue,
    severity: Severity,
    mappings: MappingABC[str, Mapping],
    risks: Sequence[OntologyRisk],
    budget: int = KEY_BUDGET,
    keys: MappingABC[str, bool] | None = None,
) -> list[Priority]:
    """One Priority per objective, in catalogue order. `keys` is the assessor's key choices."""
    unknown = sorted(severity.risk_ids - {risk.id for risk in risks})
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
        priority.top_rating = entries[0][0] if entries else 0
        priority.score, priority.reasons = _score(objective, entries, non_binding)
        binding = "Binding" in objective.grounding_tier_flag
        keyed.append(((non_binding, -priority.score, -priority.top_rating, not binding, objective.sort_key),
                      priority))

    keyed.sort(key=lambda row: row[0])
    chosen = dict(keys or {})
    defaults = 0
    for rank, (_, priority) in enumerate(keyed, 1):
        priority.rank = rank
        if (not priority.non_binding and priority.score > 0 and priority.top_rating >= KEY_RATING
                and defaults < budget):
            priority.key_default = True
            defaults += 1
        priority.key = chosen.get(priority.objective_id, priority.key_default)
    return [priorities[objective.id] for objective in catalogue]
