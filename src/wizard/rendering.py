"""Render the wizard's pages.

Server-rendered on purpose: each page ships with everything already in it, so
the browser needs no API round-trip and the service stays the single source of
truth. Autoescaping is on — objective text and card text are data, never markup.

The pages carry the qualification app's design tokens verbatim (the Luxembourg
AI Factory palette in apps/qualification/src/app/globals.css) so the modules
read as one platform.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape
from wizard.cards import CardRecord
from wizard.control_objectives import ControlObjectiveCatalogue

TEMPLATES = Path(__file__).resolve().parent / "templates"
STATIC = Path(__file__).resolve().parent / "static"

#: Note tags that qualify the objective itself and are called out in colour;
#: "Paired" only explains a Control + Test row and stays neutral.
FLAG_TAGS = ("GAP", "CONDITIONAL", "VOLUNTARY")

#: The profile's facts as questions a person can answer.
FACT_QUESTIONS = (
    ("high_risk", "Is the system high-risk under AI Act Annex III?"),
    ("personal_data", "Does the system process personal data?"),
    ("interacts_with_natural_persons", "Does the system interact directly with natural persons?"),
)


@lru_cache(maxsize=1)
def _environment() -> Environment:
    return Environment(
        loader=FileSystemLoader(TEMPLATES),
        autoescape=select_autoescape(["html", "html.j2"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )


def render_objectives_page(
    catalogue: ControlObjectiveCatalogue, source_name: str = "", root_path: str = ""
) -> str:
    """The full objectives page as HTML."""
    template = _environment().get_template("objectives.html.j2")
    return template.render(
        macros=catalogue.macro_requirements(),
        total=len(catalogue),
        source_name=source_name,
        flag_tags=FLAG_TAGS,
        root_path=root_path,
    )


@dataclass
class _MacroView:
    id: str
    title: str
    objectives: list[tuple] = field(default_factory=list)  # (objective, verdict, priority)
    in_scope: int = 0


@dataclass
class _RiskView:
    """One risk, its rating, and the objectives it drives."""

    risk: object
    rating: int
    mapped: list = field(default_factory=list)   # (objective_id, rationale)
    findings: list = field(default_factory=list)


def _risk_views(record) -> list:
    """The risks worst-first, so the page reads as the assessor's ranking."""
    if record.qualification is None:
        return []
    run = record.mapping_run
    views = []
    for risk in record.qualification.risks:
        mapping = run.mappings.get(risk.id) if run else None
        views.append(
            _RiskView(
                risk=risk,
                rating=record.severity.of(risk.id),
                mapped=[(item.objective_id, item.rationale) for item in (mapping.objectives if mapping else [])],
                findings=[f for f in (run.findings if run else []) if f.risk_id == risk.id],
            )
        )
    return sorted(views, key=lambda view: (-view.rating, view.risk.position))


@dataclass
class _Counts:
    to_achieve: int = 0
    non_binding: int = 0
    not_applicable: int = 0
    pending: int = 0
    tier1: int = 0
    tier2: int = 0
    tier3: int = 0


def render_card_page(
    record: CardRecord, catalogue: ControlObjectiveCatalogue, root_path: str = ""
) -> str:
    """One assessed card: its profile to confirm, and the verdict per objective."""
    verdicts = {v.objective_id: v for v in record.verdicts}
    priorities = {p.objective_id: p for p in record.priorities}
    counts = _Counts()
    macros: list[_MacroView] = []
    for macro in catalogue.macro_requirements():
        rows = []
        in_scope = 0
        for objective in macro.objectives:
            verdict = verdicts[objective.id]
            priority = priorities.get(objective.id)
            rows.append((objective, verdict, priority))
            if priority and priority.tier:
                setattr(counts, f"tier{priority.tier}", getattr(counts, f"tier{priority.tier}") + 1)
            if verdict.non_binding:
                counts.non_binding += 1
            elif verdict.applies == "yes":
                counts.to_achieve += 1
                in_scope += 1
            elif verdict.applies == "no":
                counts.not_applicable += 1
            else:
                counts.pending += 1
        macros.append(_MacroView(id=macro.id, title=macro.title, objectives=rows, in_scope=in_scope))

    template = _environment().get_template("card.html.j2")
    return template.render(
        record=record,
        macros=macros,
        counts=counts,
        facts=FACT_QUESTIONS,
        flag_tags=FLAG_TAGS,
        severity=record.severity,
        risks=_risk_views(record),
        objective_labels={o.id: o.sub_requirement_label for o in catalogue},
        root_path=root_path,
    )
