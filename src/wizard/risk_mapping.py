"""Which control objectives mitigate which of the system's own risks.

The qualification carries the risks an assessor actually identified for this
system, as AIRO chains. This is what connects them to the 50 objectives, so
that ranking the risks ranks the work.

Same shape as `wizard.profiling` and the qualification app's ontology filler:
the model proposes, deterministic controls check every claim against the
catalogue and against the risk's own words, failing items go back with the
findings, the rounds are bounded, and **every exit publishes** so a person has
something to correct rather than nothing.

The model is a good fit here and a poor one elsewhere: a risk chain is
specific prose ("officers rubber-stamp the recommendation", "training data is
poisoned through the bureau ingestion path") and so is an objective's text, so
the judgement is a reading, not an inference about the law.
"""

from __future__ import annotations

import re
from typing import Literal, Protocol, Sequence

from pydantic import BaseModel, Field

from wizard.control_objectives import ControlObjectiveCatalogue
from wizard.llm import Completer, parse_into
from wizard.models.ontology import OntologyRisk
from wizard.profiling import load_skill

MAX_ATTEMPTS = 3

Flag = Literal[
    "unknown-objective", "quote-not-in-risk", "quote-missing",
    "duplicate-objective", "risk-unmapped",
]


class Finding(BaseModel):
    risk_id: str
    objective_id: str = ""
    flag: Flag
    detail: str


class MappedObjective(BaseModel):
    objective_id: str
    #: A literal span of the risk chain: what in the risk this objective answers.
    quote: str = ""
    rationale: str = ""


#: Worst last: a run's stop is the worst any of its risks reached.
STOPS = ("clean", "fixpoint", "cap", "failed")
Stop = Literal["clean", "fixpoint", "cap", "failed"]


class Mapping(BaseModel):
    risk_id: str
    objectives: list[MappedObjective] = Field(default_factory=list)
    #: How this risk's own rounds ended, so a reader can see which struggled.
    stop: Stop = "clean"


class MappingRun(BaseModel):
    mappings: dict[str, Mapping] = Field(default_factory=dict)
    findings: list[Finding] = Field(default_factory=list)
    attempts: int = 0
    #: The worst stop any risk reached.
    stop: Stop = "clean"
    error: str = ""


def _normalise(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip().casefold()


def run_controls(
    mapping: Mapping, risk: OntologyRisk, catalogue: ControlObjectiveCatalogue
) -> list[Finding]:
    """Every deterministic finding against one risk's proposed mapping."""
    findings: list[Finding] = []
    if not mapping.objectives:
        return [
            Finding(
                risk_id=risk.id,
                flag="risk-unmapped",
                detail=f"nothing was proposed for {risk.text[:60]!r}",
            )
        ]

    haystack = _normalise(risk.as_text())
    seen: set[str] = set()
    for item in mapping.objectives:
        if catalogue.by_id(item.objective_id) is None:
            findings.append(Finding(
                risk_id=risk.id, objective_id=item.objective_id, flag="unknown-objective",
                detail=f"{item.objective_id} is not an objective in the catalogue"))
            continue
        if item.objective_id in seen:
            findings.append(Finding(
                risk_id=risk.id, objective_id=item.objective_id, flag="duplicate-objective",
                detail=f"{item.objective_id} was proposed twice for this risk"))
            continue
        seen.add(item.objective_id)
        if not item.quote.strip():
            findings.append(Finding(
                risk_id=risk.id, objective_id=item.objective_id, flag="quote-missing",
                detail=f"{item.objective_id} claimed with nothing quoted from the risk"))
        elif _normalise(item.quote) not in haystack:
            findings.append(Finding(
                risk_id=risk.id, objective_id=item.objective_id, flag="quote-not-in-risk",
                detail=f"the quote for {item.objective_id} is not a span of the risk: {item.quote[:60]!r}"))
    return findings


class Mapper(Protocol):
    def propose(self, risk: OntologyRisk, findings: Sequence[Finding] = ()) -> Mapping: ...


class RiskMapper:
    """The writer: asks the model which objectives mitigate one risk."""

    SKILL = "mapping-a-risk-to-control-objectives"

    def __init__(self, complete: Completer, catalogue: ControlObjectiveCatalogue,
                 skill: str | None = None):
        self._complete = complete
        self._catalogue = catalogue
        self._skill = skill if skill is not None else load_skill(self.SKILL)

    def _catalogue_text(self) -> str:
        return "\n".join(
            f"{o.id} | {o.macro_requirement} | {o.sub_requirement_label} | {o.text}"
            for o in self._catalogue
        )

    def propose(self, risk: OntologyRisk, findings: Sequence[Finding] = ()) -> Mapping:
        lines = [
            f"Risk id: {risk.id}",
            "",
            risk.as_text(),
            "",
            "The control objectives, one per line as `id | macro requirement | label | objective`:",
            self._catalogue_text(),
            "",
        ]
        if findings:
            lines += [
                "Your previous proposal failed these checks. Fix exactly these; keep the rest.",
                *(f"- {f.objective_id or f.risk_id}: {f.flag}: {f.detail}" for f in findings),
                "",
            ]
        lines.append("Now produce the mapping.")
        return parse_into(self._complete(self._skill, "\n".join(lines)), Mapping)


def _signature(findings: Sequence[Finding]) -> frozenset[tuple[str, str, str]]:
    return frozenset((f.risk_id, f.objective_id, f.flag) for f in findings)


def _map_one(
    risk: OntologyRisk,
    mapper: Mapper,
    catalogue: ControlObjectiveCatalogue,
    max_attempts: int,
) -> tuple[Mapping, list[Finding], Stop, str, int]:
    """One risk's rounds: propose, check, re-propose what failed.

    Returns what to publish whatever happened, which is the point: a mapping
    that hit its cap is worth more to a reviewer, with its findings attached,
    than nothing at all.
    """
    mapping = Mapping(risk_id=risk.id)
    findings: list[Finding] = []
    previous: frozenset[tuple[str, str, str]] | None = None
    stop: Stop = "clean"
    attempt = 0

    for attempt in range(1, max_attempts + 1):
        try:
            proposed = mapper.propose(risk, findings)
            if not isinstance(proposed, Mapping):
                raise TypeError(f"mapper returned {type(proposed).__name__}, not a Mapping")
        except Exception as exc:
            return mapping, findings, "failed", str(exc), attempt
        mapping = proposed

        findings = run_controls(mapping, risk, catalogue)
        if not findings:
            break
        signature = _signature(findings)
        if signature == previous:
            stop = "fixpoint"
            break
        previous = signature
    else:
        stop = "cap"

    # Keep only what survived the controls: an objective that does not exist,
    # or that nothing in the risk supports, is not a mapping.
    rejected = {finding.objective_id for finding in findings if finding.objective_id}
    mapping.objectives = [o for o in mapping.objectives if o.objective_id not in rejected]
    mapping.stop = stop
    return mapping, findings, stop, "", attempt


def map_risks(
    risks: Sequence[OntologyRisk],
    mapper: Mapper,
    catalogue: ControlObjectiveCatalogue,
    max_attempts: int = MAX_ATTEMPTS,
) -> MappingRun:
    """Map every risk, one at a time, bounded, always publishing."""
    run = MappingRun()
    for risk in risks:
        mapping, findings, stop, error, attempts = _map_one(risk, mapper, catalogue, max_attempts)
        run.mappings[risk.id] = mapping
        run.attempts = max(run.attempts, attempts)
        run.stop = max(run.stop, stop, key=STOPS.index)
        if stop == "failed":
            run.error = error
            return run
        run.findings.extend(findings)
    return run
