"""Render the pages.

Server-rendered on purpose: each page ships with everything already in it, so
the browser needs no API round-trip and the service stays the single source of
truth. Autoescaping is on — objective text and card text are data, never markup.

The pages carry the qualification app's design tokens verbatim (the Luxembourg
AI Factory palette in apps/qualification/src/app/globals.css) so the modules
read as one platform.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from aisc_control_objectives.control_objectives import ControlObjectiveCatalogue

TEMPLATES = Path(__file__).resolve().parent / "templates"
STATIC = Path(__file__).resolve().parent / "static"

#: Note tags that qualify the objective itself and are called out in colour;
#: "Paired" only explains a Control + Test row and stays neutral.
FLAG_TAGS = ("GAP", "CONDITIONAL", "VOLUNTARY")

#: The launcher on a developer's machine, when LAUNCHER_URL is not set.
DEFAULT_LAUNCHER_URL = "http://localhost:8100/"


@lru_cache(maxsize=1)
def _environment() -> Environment:
    environment = Environment(
        loader=FileSystemLoader(TEMPLATES),
        autoescape=select_autoescape(["html", "html.j2"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    environment.globals["launcher_url"] = launcher_url
    return environment


def launcher_url() -> str:
    """Where projects are chosen. Read from the environment, because it is
    outside this service."""
    return os.environ.get("LAUNCHER_URL", DEFAULT_LAUNCHER_URL).rstrip("/") + "/"


def project_base(root_path: str, project: str) -> str:
    """Everything about one project hangs off this prefix. Pages build their
    links from it, so navigating never drops the project the way a query
    string can."""
    return f"{root_path}/p/{project}" if project else ""


def project_page(project: str) -> str:
    """Where the launcher shows this project and the other five steps."""
    return f"{launcher_url()}p/{project}" if project else ""


def _navigation(root_path: str, project: str) -> dict[str, str]:
    """What every page's header and links are built from."""
    return {
        "root_path": root_path,
        "project_base": project_base(root_path, project),
        "project_page": project_page(project),
    }


def render_home_page(
    catalogue: ControlObjectiveCatalogue,
    projects_count: int | None,
    root_path: str = "",
    project: str = "",
) -> str:
    """The way in: what the two halves are, and how an assessment runs."""
    return _environment().get_template("home.html.j2").render(
        objectives_count=len(catalogue),
        macros_count=len(catalogue.macro_requirements()),
        projects_count=projects_count,
        here="home",
        **_navigation(root_path, project),
    )


def render_objectives_page(
    catalogue: ControlObjectiveCatalogue,
    source_name: str = "",
    root_path: str = "",
    project: str = "",
) -> str:
    """The full objectives page as HTML."""
    template = _environment().get_template("objectives.html.j2")
    return template.render(
        macros=catalogue.macro_requirements(),
        total=len(catalogue),
        source_name=source_name,
        flag_tags=FLAG_TAGS,
        here="objectives",
        **_navigation(root_path, project),
    )


#: A project's objective sections, in the order an assessor works through them.
TIER_SECTIONS = (
    (1, "Tier 1", "Start here"),
    (2, "Tier 2", "Next"),
    (3, "Tier 3", "Later"),
)


@dataclass
class _TierView:
    key: object
    title: str
    subtitle: str
    objectives: list[tuple] = field(default_factory=list)  # (objective, priority)


@dataclass
class _RiskView:
    """One risk, its rating, and the objectives it drives."""

    risk: object
    rating: int
    mapped: list = field(default_factory=list)   # (objective_id, rationale)
    findings: list = field(default_factory=list)


def _risk_views(record) -> list:
    """The risks worst-first, so the page reads as the assessor's ranking."""
    if not record.ontology.risks:
        return []
    run = record.mapping_run
    views = []
    for risk in record.ontology.risks:
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
    tier1: int = 0
    tier2: int = 0
    tier3: int = 0


def render_projects_page(views: list, root_path: str = "", project: str = "") -> str:
    """The systems under assessment, in one project."""
    return _environment().get_template("projects.html.j2").render(
        projects=views, here="projects", **_navigation(root_path, project)
    )


def render_project_page(
    view, catalogue: ControlObjectiveCatalogue, root_path: str = "", project: str = "",
    read_only: str | None = None,
) -> str:
    """One project: its risks, and the tiers they produce. `read_only` says why
    an older version's assessment can no longer be ranked or mapped."""
    record = view.record
    priorities = {p.objective_id: p for p in view.priorities}
    counts = _Counts()
    sections = {key: _TierView(key, title, subtitle) for key, title, subtitle in TIER_SECTIONS}

    # Ordered by tier, then by score within a tier, then by requirement order:
    # the page is a work list, and a work list reads top to bottom.
    rows = []
    for objective in catalogue:
        priority = priorities[objective.id]
        rows.append((objective, priority))
        setattr(counts, f"tier{priority.tier}", getattr(counts, f"tier{priority.tier}") + 1)

    rows.sort(key=lambda row: (-row[1].score, row[0].sort_key))
    for objective, priority in rows:
        sections[priority.tier].objectives.append((objective, priority))
    ordered = [section for section in sections.values() if section.objectives]

    template = _environment().get_template("project.html.j2")
    return template.render(
        view=view,
        record=record,
        sections=ordered,
        counts=counts,
        flag_tags=FLAG_TAGS,
        here="projects",
        risks=_risk_views(record),
        objective_labels={o.id: o.sub_requirement_label for o in catalogue},
        read_only=read_only,
        **_navigation(root_path, project),
    )
