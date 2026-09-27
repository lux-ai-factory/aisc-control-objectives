"""The service — app factory.

Two things to look at, and one project at a time:

    /                               to the launcher, where a project is chosen
    /objectives                     the control objectives, as a reference
    /p/{project}                    the way in, inside one platform project
    /p/{project}/projects           its assessments, one per AI card version
    /p/{project}/projects/{id}      the AI Card · rank its risks · map · tiers

The JSON API mirrors the pages. Everything is persisted, so a restart loses
nothing.
"""

from __future__ import annotations

import json
import os
from typing import Literal

from fastapi import Body, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse, RedirectResponse
from sqlalchemy.exc import IntegrityError

from aisc_identity.headers import token_from_headers

from aisc_control_objectives import upstream
from aisc_control_objectives.access import (
    REFUSALS,
    ProjectAccess,
    access_for,
    decide,
    project_pid,
)
from aisc_control_objectives.config import RunConfig
from aisc_control_objectives.control_objectives import ControlObjectiveCatalogue
from aisc_control_objectives.models.control_objective import ControlObjective, MacroRequirement
from aisc_control_objectives.models.ontology import Ontology
from aisc_control_objectives.projects import ModelUnavailable, Projects
from aisc_control_objectives.rendering import (
    DEFAULT_LAUNCHER_URL,
    STATIC,
    render_home_page,
    render_objectives_page,
    render_project_page,
    render_projects_page,
)

#: Filter over the assessment mode. A paired ("Control + Test") objective
#: answers to both, so the partitions overlap rather than splitting the set.
ModeFilter = Literal["control", "test"]

NO_CARD = "No AI card for the latest version yet"

#: Where projects are chosen. One place, before any module is entered.
LAUNCHER_URL = os.environ.get("LAUNCHER_URL", DEFAULT_LAUNCHER_URL)


def _read_only(projects, view) -> str | None:
    """Why an assessment may no longer change, or None when it may."""
    if projects.is_latest(view):
        return None
    latest = view.record.latest_number
    return f"read-only: v{latest} is the latest" if latest else "read-only: its version is gone"


def _assessment_url(root_path: str, project: str, assessment_id: str) -> str:
    return f"{root_path}/p/{project}/projects/{assessment_id}"


class _Refused(Exception):
    """A start that cannot go ahead, with the plain-text answer to give."""

    def __init__(self, message: str, status_code: int):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


def _latest_version(project: str, auth: str | None) -> dict:
    """The project's latest card version, as the platform reports it."""
    try:
        latest = upstream.latest_version(project, auth)
    except upstream.UpstreamDown as exc:
        raise _Refused(f"The platform did not answer: {exc}", 502) from exc
    if latest is None:
        raise _Refused(NO_CARD, 409)
    return latest


def _card_of(latest: dict, auth: str | None) -> tuple[str, object]:
    """That version's card from qualification: the text as served, and its parse."""
    try:
        jsonld = upstream.card_jsonld(latest["project_id"], latest["pid"], auth)
    except upstream.UpstreamDown as exc:
        raise _Refused(f"Qualification did not answer: {exc}", 502) from exc
    if jsonld is None:
        raise _Refused(NO_CARD, 409)
    try:
        raw = json.loads(jsonld)
    except ValueError as exc:
        raise _Refused("Qualification served a card that is not JSON.", 502) from exc
    if not Ontology.looks_like_one(raw):
        raise _Refused("Qualification served something that is not an AI card.", 502)
    return jsonld, raw


class NoMapper:
    """Stand-in when no mapper is wired: every risk maps to nothing, and the
    page says so rather than pretending the risks were read."""

    def propose(self, risk, findings=()):
        from aisc_control_objectives.risk_mapping import Mapping

        return Mapping(risk_id=risk.id)


def create_app(
    objectives: ControlObjectiveCatalogue,
    projects: Projects,
    *,
    base_config: RunConfig | None = None,
    root_path: str = "",
    cors_origins: list[str] | None = None,
    source_name: str = "ai_act_control_objectives.csv",
    engine=None,
) -> FastAPI:
    config = base_config or RunConfig()
    app = FastAPI(title="AISC Control Objectives", root_path=root_path)

    # Who is calling, who may be in a project, and who may change it. Added
    # before CORS so that CORS stays the outermost middleware and a refusal
    # still carries its headers. Without an engine there is nothing to read
    # the membership from, and the service runs open: that is how the domain
    # tests build it, and why server.build_app always passes one (pinned in
    # tests/test_api_auth.py).
    if engine is not None:
        app.add_middleware(ProjectAccess, engine=engine)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins or ["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    # A route rather than a StaticFiles mount: Caddy strips /control-objectives,
    # and a mount under a root_path only finds files when the prefix is still
    # on the path, so behind the proxy the logo 404'd and its alt text showed.
    @app.get("/static/{name}", include_in_schema=False)
    def static(name: str) -> FileResponse:
        path = STATIC / name
        if path.parent != STATIC or not path.is_file():
            raise HTTPException(status_code=404)
        return FileResponse(path)

    def _view_or_404(project_id: str):
        view = projects.view(project_id)
        if view is None:
            raise HTTPException(status_code=404, detail=f"Unknown project {project_id}")
        return view

    def _view_of(request: Request, project_id: str):
        """An assessment addressed by id alone, decided by its OWN project.

        The JSON API has no project in its path, so the middleware only knows
        who is calling; this asks what the caller is to the project the
        assessment belongs to. A stranger gets the answer an unknown id gets.
        """
        view = _view_or_404(project_id)
        if engine is not None:
            verdict = decide(
                request.method, access_for(engine, view.record.project, request.state.caller)
            )
            if verdict == "not-found":
                raise HTTPException(status_code=404, detail=f"Unknown project {project_id}")
            if verdict != "allow":
                message, status = REFUSALS[verdict]
                raise HTTPException(status_code=status, detail=message)
        return view

    def _project_may(request: Request, project: str) -> None:
        """The caller's verdict on a platform project named in a query string."""
        if engine is None:
            return
        verdict = decide(request.method, access_for(engine, project, request.state.caller))
        if verdict != "allow":
            message, status = REFUSALS[verdict]
            raise HTTPException(status_code=status, detail=message)

    def _view_in(project: str, project_id: str):
        """An assessment opened under /p/{project}: only under its own project.

        The middleware has already decided on {project}; this refuses an
        assessment of any other project, whether {project} is the pid or the
        slug, with the answer an unknown id gets.
        """
        view = _view_or_404(project_id)
        own = view.record.project
        if own != project and (engine is None or project_pid(engine, project) != own):
            raise HTTPException(status_code=404, detail=f"Unknown project {project_id}")
        return view

    _register_pages(app, objectives, projects, _view_in, source_name, root_path)
    _register_objective_api(app, objectives, config)
    _register_project_api(app, projects, _view_of, _project_may)
    return app


def _register_pages(app, objectives, projects, _view_in, source_name, root_path):
    """The pages, and the forms that post to them.

    Anything that reads or writes an assessment lives under `/p/{project}`:
    there is one database, and a page that lists assessments has to say whose.
    The catalogue itself is the same for everyone, so it has no project in its
    path. Links therefore carry the project without a query string to lose.
    """

    @app.get("/", include_in_schema=False)
    def no_project() -> RedirectResponse:
        """Reached without a project.

        The project is chosen once, on the launcher, and every module then works
        inside it. So this does not ask again: it sends you to the one place
        that answers the question, and the launcher's card opens this service on
        the project you pick. The catalogue of objectives, which reads the same
        for everyone, stays where it is at /objectives.
        """
        return RedirectResponse(url=LAUNCHER_URL, status_code=307)

    @app.get("/p/{project}", include_in_schema=False, response_class=HTMLResponse)
    def project_home_page(project: str) -> HTMLResponse:
        return HTMLResponse(
            render_home_page(
                objectives, len(projects.list(project)), root_path=root_path, project=project
            )
        )

    @app.get("/objectives", include_in_schema=False, response_class=HTMLResponse)
    def objectives_page() -> HTMLResponse:
        return HTMLResponse(
            render_objectives_page(objectives, source_name=source_name, root_path=root_path)
        )

    @app.get("/p/{project}/objectives", include_in_schema=False, response_class=HTMLResponse)
    def project_objectives_page(project: str) -> HTMLResponse:
        return HTMLResponse(
            render_objectives_page(
                objectives, source_name=source_name, root_path=root_path, project=project
            )
        )

    @app.get("/p/{project}/projects", include_in_schema=False, response_class=HTMLResponse)
    def projects_page(project: str) -> HTMLResponse:
        return HTMLResponse(
            render_projects_page(projects.list(project), root_path=root_path, project=project)
        )

    @app.post("/p/{project}/projects", include_in_schema=False)
    async def start_assessment_form(project: str, request: Request):
        """"Start assessment": of the project's latest AI card version.

        No file: the platform says which version is the latest, qualification
        serves its card, and the assessment is stored against that version.
        Starting again on the same version opens the one it already has.
        Nothing is stored on any error.
        """
        form = await request.form()
        name = str(form.get("name") or "").strip()
        # Behind the gateway the token arrives as X-Auth-Request-Access-Token
        # and there is no Authorization header; a direct API client sends
        # Bearer. Either way it goes on as the caller's.
        token = token_from_headers(request.headers)
        auth = f"Bearer {token}" if token else None

        def opened(view):
            return RedirectResponse(
                url=_assessment_url(root_path, project, view.record.id), status_code=303
            )

        try:
            latest = _latest_version(project, auth)
            existing = projects.find_by_system(latest["pid"])
            if existing is not None:
                return opened(existing)
            jsonld, raw = _card_of(latest, auth)
        except _Refused as refused:
            return PlainTextResponse(refused.message, status_code=refused.status_code)
        try:
            view = projects.create(
                project=project, name=name, jsonld=jsonld, raw=raw, system_id=latest["pid"]
            )
        except IntegrityError:
            # Started twice at once: the other start made it.
            found = projects.find_by_system(latest["pid"])
            if found is None:
                raise
            return opened(found)
        return opened(view)

    @app.get(
        "/p/{project}/projects/{project_id}",
        include_in_schema=False,
        response_class=HTMLResponse,
    )
    def project_page(project: str, project_id: str) -> HTMLResponse:
        view = _view_in(project, project_id)
        return HTMLResponse(
            render_project_page(
                view, objectives, root_path=root_path, project=project,
                read_only=_read_only(projects, view),
            )
        )

    @app.post("/p/{project}/projects/{project_id}/map", include_in_schema=False)
    def map_form(project: str, project_id: str):
        """The one agentic step, on a button: it costs a model call per risk."""
        view = _view_in(project, project_id)
        if (why := _read_only(projects, view)) is not None:
            return PlainTextResponse(why, status_code=409)
        try:
            projects.map_risks_of(project_id)
        except ModelUnavailable as exc:
            return PlainTextResponse(str(exc), status_code=502)
        return RedirectResponse(
            url=_assessment_url(root_path, project, project_id), status_code=303
        )

    @app.post("/p/{project}/projects/{project_id}/severity", include_in_schema=False)
    async def rate_form(project: str, project_id: str, request: Request):
        view = _view_in(project, project_id)
        if (why := _read_only(projects, view)) is not None:
            return PlainTextResponse(why, status_code=409)
        form = await request.form()
        try:
            ratings = {
                risk.id: int(form[risk.id])
                for risk in view.record.ontology.risks
                if form.get(risk.id)
            }
            projects.rate(project_id, ratings)
        except ValueError as exc:
            return PlainTextResponse(f"Invalid rating: {exc}", status_code=400)
        return RedirectResponse(
            url=_assessment_url(root_path, project, project_id), status_code=303
        )


def _register_objective_api(app, objectives, config):
    """The catalogue: what the objectives are, independent of any system."""

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/config", response_model=RunConfig)
    def get_config() -> RunConfig:
        return config

    @app.get("/api/control-objectives", response_model=list[ControlObjective])
    def list_control_objectives(
        mode: ModeFilter | None = Query(
            None, description="Keep only objectives assessed by a control or by a test"
        ),
    ) -> list[ControlObjective]:
        if mode == "control":
            return objectives.requiring_control()
        if mode == "test":
            return objectives.requiring_test()
        return objectives.objectives

    @app.get("/api/control-objectives/{objective_id}", response_model=ControlObjective)
    def get_control_objective(objective_id: str) -> ControlObjective:
        objective = objectives.by_id(objective_id)
        if objective is None:
            raise HTTPException(status_code=404, detail=f"Unknown objective {objective_id}")
        return objective

    @app.get("/api/macro-requirements", response_model=list[MacroRequirement])
    def list_macro_requirements() -> list[MacroRequirement]:
        return objectives.macro_requirements()


def _register_project_api(app, projects, _view_of, _project_may):
    """One assessed system: its graph, its ranking, its mapping, its tiers."""

    def payload(view) -> dict:
        record = view.record
        return {
            "id": record.id,
            "name": record.name,
            "system_name": record.system_name,
            "qualification_id": record.qualification_id,
            "system_id": record.system_id,
            "version_number": record.version_number,
            "latest_number": record.latest_number,
            "digest": record.digest,
            "objectives_digest": record.objectives_digest,
            "created_at": record.created_at.isoformat(),
            "updated_at": record.updated_at.isoformat(),
            "risks": [risk.model_dump() for risk in record.ontology.risks],
            "severity": record.severity.model_dump(),
            "mapping_run": record.mapping_run.model_dump() if record.mapping_run else None,
            "priorities": [p.model_dump() for p in view.priorities],
            "mapped": view.mapped,
        }

    def _latest_or_409(view) -> None:
        if (why := _read_only(projects, view)) is not None:
            raise HTTPException(status_code=409, detail=why)

    @app.get("/api/projects")
    def list_projects(
        request: Request,
        project: str = Query(..., description="The platform project to list"),
    ) -> list[dict]:
        _project_may(request, project)
        return [payload(view) for view in projects.list(project)]

    @app.get("/api/projects/{project_id}")
    def get_project(project_id: str, request: Request) -> dict:
        return payload(_view_of(request, project_id))

    @app.post("/api/projects/{project_id}/map")
    def map_risks(project_id: str, request: Request) -> dict:
        _latest_or_409(_view_of(request, project_id))
        try:
            return payload(projects.map_risks_of(project_id))
        except ModelUnavailable as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @app.post("/api/projects/{project_id}/severity")
    def rate(project_id: str, request: Request, ratings: dict[str, int] = Body(...)) -> dict:
        _latest_or_409(_view_of(request, project_id))
        try:
            return payload(projects.rate(project_id, ratings))
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.delete("/api/projects/{project_id}", status_code=204)
    def delete_project(project_id: str, request: Request) -> None:
        _view_of(request, project_id)
        projects.delete(project_id)
