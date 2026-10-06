"""Render the pages.

Server-rendered on purpose: each page ships with everything already in it, so
the browser needs no API round-trip and the service stays the single source of
truth. Autoescaping is on: objective text and card text are data, never markup.

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
from aisc_control_objectives.prioritising import band

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


@dataclass
class _RiskView:
    """One row of the risk register: a risk, its rating, and the objectives it drives."""

    risk: object
    rating: int
    impact: int | None = None
    likelihood: int | None = None
    mapped: list = field(default_factory=list)   # (objective_id, rationale, source)
    findings: list = field(default_factory=list)
    #: Why the assessor rated it so; "" when they said nothing.
    comment: str = ""

    @property
    def band(self) -> str:
        """Low ... Critical; "Not rated" until both parts are."""
        return band(self.rating) if self.rating else "Not rated"

    @property
    def rated(self) -> bool:
        return self.impact is not None or self.likelihood is not None


def _risk_views(record) -> list:
    """The risks by rating, highest first: the register reads as the assessor's ranking."""
    if not record.ontology.risks:
        return []
    run = record.mapping_run
    severity = record.severity
    views = []
    for risk in record.ontology.risks:
        mapping = run.mappings.get(risk.id) if run else None
        views.append(
            _RiskView(
                risk=risk,
                rating=severity.of(risk.id),
                impact=severity.impact.get(risk.id),
                likelihood=severity.likelihood.get(risk.id),
                mapped=[(item.objective_id, item.rationale, item.source)
                        for item in (mapping.objectives if mapping else [])],
                findings=[f for f in (run.findings if run else []) if f.risk_id == risk.id],
                comment=severity.comments.get(risk.id, ""),
            )
        )
    return sorted(views, key=lambda view: (-view.rating, view.risk.position))


@dataclass
class _Chip:
    """One objective in a risk's row of the matrix."""

    objective: object
    priority: object
    #: "ai" or "person"
    source: str
    rationale: str
    quote: str


def _chips(record, risks: list, catalogue: ControlObjectiveCatalogue, priorities: dict) -> dict:
    """risk id -> its objectives as chips, by rank; one outside the profile (left from an older
    mapping) is not shown."""
    run = record.mapping_run
    out = {}
    for view in risks:
        mapping = run.mappings.get(view.risk.id) if run else None
        chips = []
        for item in (mapping.objectives if mapping else []):
            objective = catalogue.by_id(item.objective_id)
            if objective is not None:
                chips.append(_Chip(objective=objective, priority=priorities[objective.id], source=item.source,
                                   rationale=item.rationale, quote=item.quote))
        chips.sort(key=lambda chip: chip.priority.rank)
        out[view.risk.id] = chips
    return out


def render_projects_page(views: list, root_path: str = "", project: str = "") -> str:
    """The systems under assessment, in one project."""
    return _environment().get_template("projects.html.j2").render(
        projects=views, here="projects", **_navigation(root_path, project)
    )


def render_project_page(
    view, catalogue: ControlObjectiveCatalogue, root_path: str = "", project: str = "",
    read_only: str | None = None, profiles: list | None = None,
) -> str:
    """One assessment as a risk and control matrix: the profile line, the risk register and the
    matrix. `read_only` says why an older version's assessment can no longer be changed. The
    objectives are the assessment's profile's (`view.catalogue`); `profiles` are the ones it may
    switch to."""
    catalogue = view.catalogue or catalogue
    record = view.record
    priorities = {p.objective_id: p for p in view.priorities}
    risks = _risk_views(record)
    template = _environment().get_template("project.html.j2")
    return template.render(
        view=view,
        record=record,
        flag_tags=FLAG_TAGS,
        here="projects",
        risks=risks,
        chips=_chips(record, risks, catalogue, priorities),
        keys=[p for p in sorted(view.priorities, key=lambda p: p.rank) if p.key],
        objective_labels={o.id: o.sub_requirement_label for o in catalogue},
        macros=catalogue.macro_requirements(),
        read_only=read_only,
        profile=view.profile or {},
        profiles=profiles or [],
        **_navigation(root_path, project),
    )


# Objective sets and profiles


def _library_context(library) -> dict:
    from aisc_control_objectives.library import MODES, NO_BASIS
    return {"dimensions": library.dimensions, "modes": MODES, "no_basis": NO_BASIS}


def render_sets_page(library, root_path: str = "", project: str = "") -> str:
    return _environment().get_template("sets.html.j2").render(
        sets=library.sets(), here="sets", **_navigation(root_path, project))


def render_set_page(library, set_id: str, root_path: str = "", project: str = "") -> str:
    view = library.get_set(set_id)
    numbers = [o.number for o, _ in view.draft_rows]
    return _environment().get_template("set.html.j2").render(
        view=view, next_number=(max(numbers) + 1) if numbers else 1, here="sets",
        **_library_context(library), **_navigation(root_path, project))


def render_profiles_page(library, root_path: str = "", project: str = "") -> str:
    return _environment().get_template("profiles.html.j2").render(
        profiles=library.profiles(), here="profiles", **_navigation(root_path, project))


def render_profile_page(library, profile_id: str | None, root_path: str = "", project: str = "") -> str:
    """One profile (None: a new one). The picker groups what can be picked by set, then dimension."""
    view = library.get_profile(profile_id) if profile_id else None
    available = library.available()
    groups = []
    for summary in library.sets():
        if summary.latest is None:
            continue
        mine = ControlObjectiveCatalogue([o for o in available if o.set_code == summary.code])
        if len(mine):
            groups.append((summary, [(m, m.objectives) for m in mine.macro_requirements()]))
    return _environment().get_template("profile.html.j2").render(
        view=view, groups=groups, builtin=[o for o in available if o.set_code == "O"],
        here="profiles", **_navigation(root_path, project))
