"""The service — app factory.

Two things to look at, and one project at a time:

    /                               to the launcher, where a project is chosen
    /objectives                     the control objectives, as a reference
    /p/{project}                    the way in, inside one platform project
    /p/{project}/projects           its assessments, one per AI card version
    /p/{project}/projects/{id}      the AI Card · rank its risks · map · tiers

    /p/{project}/api/projects[/{id}[/map|/ratings|/key]]  the JSON API, inside the project too

Each project's assessments are in that project's own database (isolation
2026-09-25): the gate opens the database of the project in the path, after
deciding the caller may be there, and every handler works in that database
only. An assessment of another project is not there, so it is a 404.
Everything is persisted, so a restart loses nothing.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Literal

from aisc_identity.headers import token_from_headers
from fastapi import Body, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    PlainTextResponse,
    RedirectResponse,
)
from sqlalchemy.exc import DBAPIError, IntegrityError

from aisc_control_objectives import ledger
from aisc_control_objectives.api import ledger_events
from aisc_control_objectives import projectdb, upstream
from aisc_control_objectives.access import REFUSALS, ProjectAccess
from aisc_control_objectives.api.library_routes import register_library
from aisc_control_objectives.config import RunConfig
from aisc_control_objectives.control_objectives import ControlObjectiveCatalogue
from aisc_control_objectives.db.repository import ProjectRepository
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

#: A start whose version the project's own database does not have.
NOT_IN_PROJECT = "The latest version is not in this project's database."

logger = logging.getLogger(__name__)

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
    databases: projectdb.ProjectDatabases | None = None,
) -> FastAPI:
    """The app. `engine` reads membership (the gate); `databases`, as deployed,
    is the door to each project's own database. Without `databases` every
    assessment is in `projects`' one repository (the single-database domain
    tests); without `engine` there is no gate at all."""
    if databases is not None and engine is None:
        raise ValueError("databases needs the platform engine for its gate")
    config = base_config or RunConfig()
    app = FastAPI(title="AISC Control Objectives", root_path=root_path)

    # Who is calling, who may be in a project, and who may change it. Added
    # before CORS so that CORS stays the outermost middleware and a refusal
    # still carries its headers. Without an engine there is nothing to read
    # the membership from, and the service runs open: that is how the domain
    # tests build it, and why server.build_app always passes one (pinned in
    # tests/test_api_auth.py).
    if engine is not None:
        app.add_middleware(ProjectAccess, engine=engine, databases=databases)

    # The witnessed request (X-AISC-Request-Id) this request's ledger events cite (phase 6).
    app.add_middleware(ledger.RequestId)

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

    if databases is not None:
        @app.exception_handler(DBAPIError)
        async def database_error(request: Request, exc: DBAPIError):
            """A project database dropped under a running service (I2.5): forget
            its engine, and the project is gone, 404. Anything else is a 500."""
            opened = getattr(request.state, "opened", None)
            if opened is not None and projectdb.is_missing_database(exc):
                databases.evict(opened.pid)
                message, status = REFUSALS["not-found"]
            else:
                logger.error("database error on %s: %s", request.url.path, type(exc).__name__)
                message, status = "Internal Server Error", 500
            if "/api/" in request.url.path:
                return JSONResponse({"detail": message}, status_code=status)
            return PlainTextResponse(message, status_code=status)

    def projects_of(request: Request) -> Projects:
        """The service on the database the gate opened for this request."""
        opened = getattr(request.state, "opened", None)
        if opened is None:
            return projects
        return projects.bound(
            ProjectRepository(opened.engine, objectives_digest=objectives.digest, pid=opened.pid)
        )

    def view_in(request: Request, project_id: str):
        """An assessment in this project's database, or the 404 an unknown id gets.

        The gate has decided on the project in the path and opened its
        database; an assessment of any other project is simply not in it.
        """
        view = projects_of(request).view(project_id)
        if view is None:
            raise HTTPException(status_code=404, detail=f"Unknown project {project_id}")
        return view

    _register_pages(app, objectives, projects_of, view_in, source_name, root_path)
    _register_objective_api(app, objectives, config)
    _register_project_api(app, projects_of, view_in)
    register_library(app, projects_of, root_path)
    return app


def _register_pages(app, objectives, projects_of, view_in, source_name, root_path):
    """The pages, and the forms that post to them.

    Anything that reads or writes an assessment lives under `/p/{project}`:
    the project's own database is where it is, and the path says which.
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
    def project_home_page(project: str, request: Request) -> HTMLResponse:
        return HTMLResponse(
            render_home_page(
                objectives, len(projects_of(request).list()), root_path=root_path, project=project
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
    def projects_page(project: str, request: Request) -> HTMLResponse:
        return HTMLResponse(
            render_projects_page(projects_of(request).list(), root_path=root_path, project=project)
        )

    @app.post("/p/{project}/projects", include_in_schema=False)
    async def start_assessment_form(project: str, request: Request):
        """"Start assessment": of the project's latest AI card version.

        No file: the platform says which version is the latest, qualification
        serves its card, and the assessment is stored against that version.
        Starting again on the same version opens the one it already has.
        Nothing is stored on any error, and a version this project's database
        does not have is refused (409), never stored against another's.
        """
        projects = projects_of(request)
        opened = getattr(request.state, "opened", None)
        pid = opened.pid if opened is not None else project
        form = await request.form()
        name = str(form.get("name") or "").strip()
        # Behind the gateway the token arrives as X-Auth-Request-Access-Token
        # and there is no Authorization header; a direct API client sends
        # Bearer. Either way it goes on as the caller's.
        token = token_from_headers(request.headers)
        auth = f"Bearer {token}" if token else None

        def redirect_to(view):
            return RedirectResponse(
                url=_assessment_url(root_path, project, view.record.id), status_code=303
            )

        try:
            latest = _latest_version(pid, auth)
            existing = projects.find_by_system(latest["pid"])
            if existing is not None:
                return redirect_to(existing)
            jsonld, raw = _card_of(latest, auth)
            if not projects.has_version(latest["pid"]):
                raise _Refused(NOT_IN_PROJECT, 409)
        except _Refused as refused:
            return PlainTextResponse(refused.message, status_code=refused.status_code)
        try:
            view = projects.create(
                project=pid, name=name, jsonld=jsonld, raw=raw, system_id=latest["pid"],
                record=lambda s, c: ledger.emit(s, "assessment.started", item_type="assessment", item_id=c["id"],
                                                card_version=latest["pid"],
                                                details={"card_version": latest["pid"], "risks": c["risks"],
                                                         "profile_version": c["profile_version"]}),
            )
        except IntegrityError as exc:
            if getattr(exc.orig, "sqlstate", None) == "23503":
                # the version went from project.system between the check and the write
                return PlainTextResponse(NOT_IN_PROJECT, status_code=409)
            # Started twice at once: the other start made it.
            found = projects.find_by_system(latest["pid"])
            if found is None:
                raise
            return redirect_to(found)
        return redirect_to(view)

    @app.get(
        "/p/{project}/projects/{project_id}",
        include_in_schema=False,
        response_class=HTMLResponse,
    )
    def project_page(project: str, project_id: str, request: Request) -> HTMLResponse:
        view = view_in(request, project_id)
        return HTMLResponse(
            render_project_page(
                view, objectives, root_path=root_path, project=project,
                read_only=_read_only(projects_of(request), view),
                profiles=projects_of(request).library.profiles(),
            )
        )

    @app.post("/p/{project}/projects/{project_id}/map", include_in_schema=False)
    def map_form(project: str, project_id: str, request: Request):
        """The one agentic step, on a button: it costs a model call per risk."""
        projects = projects_of(request)
        view = view_in(request, project_id)
        if (why := _read_only(projects, view)) is not None:
            return PlainTextResponse(why, status_code=409)
        try:
            projects.map_risks_of(project_id, on_start=lambda s, run_id: ledger.emit(
                s, "ai.mapping.requested", item_type="assessment", item_id=project_id, run_id=run_id),
                on_save=lambda s, o: ledger_events.mapping_outcome(s, project_id, o))
        except ModelUnavailable as exc:
            return PlainTextResponse(str(exc), status_code=502)
        return RedirectResponse(
            url=_assessment_url(root_path, project, project_id), status_code=303
        )

    @app.post("/p/{project}/projects/{project_id}/risks/{risk_id}/mapping", include_in_schema=False)
    async def map_by_hand_form(project: str, project_id: str, risk_id: str, request: Request):
        """A person's mapping of one risk: the ticked objectives, and only those."""
        projects = projects_of(request)
        view = view_in(request, project_id)
        if (why := _read_only(projects, view)) is not None:
            return PlainTextResponse(why, status_code=409)
        form = await request.form()
        try:
            projects.map_by_hand(project_id, risk_id, [str(v) for v in form.getlist("objective")],
                                 on_save=lambda s, c: ledger.emit(
                                     s, "mapping.risk.edited", item_type="risk_mapping",
                item_id=ledger_events.risk_item(project_id, risk_id),
                                     before=c["before"], after=c["after"],
                                     details={"added": sorted(set(c["after"]) - set(c["before"])),
                                              "removed": sorted(set(c["before"]) - set(c["after"]))}))
        except ValueError as exc:
            return PlainTextResponse(f"Invalid mapping: {exc}", status_code=400)
        return RedirectResponse(
            url=_assessment_url(root_path, project, project_id) + f"#map-{risk_id}", status_code=303
        )

    @app.post("/p/{project}/projects/{project_id}/profile", include_in_schema=False)
    async def profile_form(project: str, project_id: str, request: Request):
        """Run the assessment on another objective profile, or on its profile's newer version."""
        projects = projects_of(request)
        view = view_in(request, project_id)
        if (why := _read_only(projects, view)) is not None:
            return PlainTextResponse(why, status_code=409)
        form = await request.form()
        try:
            projects.use_profile(project_id, str(form.get("profile") or ""), record=lambda s, c: ledger.emit(
                s, "assessment.profile.switched", item_type="assessment", item_id=project_id,
                details={"version_before": c["before"], "version_after": c["after"], "dropped": c["dropped"]}))
        except ValueError as exc:
            return PlainTextResponse(f"Invalid profile: {exc}", status_code=400)
        return RedirectResponse(
            url=_assessment_url(root_path, project, project_id), status_code=303
        )

    @app.post("/p/{project}/projects/{project_id}/severity", include_in_schema=False)
    async def rate_form(project: str, project_id: str, request: Request):
        projects = projects_of(request)
        view = view_in(request, project_id)
        if (why := _read_only(projects, view)) is not None:
            return PlainTextResponse(why, status_code=409)
        form = await request.form()
        try:
            # per risk: `impact:<risk id>`, `likelihood:<risk id>` (1-5, blank leaves it as it is) and
            # an optional `comment:<risk id>` (a blank one clears it)
            def part(prefix):
                return {key.split(":", 1)[1]: int(value) for key, value in form.items()
                        if key.startswith(prefix) and str(value).strip()}
            comments = {
                key.split(":", 1)[1]: str(value)
                for key, value in form.items() if key.startswith("comment:")
            }
            projects.rate(project_id, part("impact:"), part("likelihood:"), comments, record=lambda s, changed: (
                [ledger.emit(s, "risk.rated", **e) for e in ledger_events.ratings(changed)],
                [ledger.emit(s, "risk.rating_comment.set", **e) for e in ledger_events.comments(changed)]))
        except ValueError as exc:
            return PlainTextResponse(f"Invalid rating: {exc}", status_code=400)
        return RedirectResponse(
            url=_assessment_url(root_path, project, project_id), status_code=303
        )


    @app.post("/p/{project}/projects/{project_id}/key", include_in_schema=False)
    async def key_form(project: str, project_id: str, request: Request):
        """The key ticks of the matrix: the ticked objectives are key, every other one in the
        matrix is not."""
        projects = projects_of(request)
        view = view_in(request, project_id)
        if (why := _read_only(projects, view)) is not None:
            return PlainTextResponse(why, status_code=409)
        form = await request.form()
        ticked = {str(v) for v in form.getlist("key")}
        in_matrix = {p.objective_id for p in view.priorities if p.risk_ids} | ticked
        try:
            projects.set_keys(project_id, {oid: oid in ticked for oid in in_matrix}, record=lambda s, c: ledger.emit(
                s, "objective.key.set", item_type="assessment", item_id=project_id, before=c["before"],
                after=c["after"], details=ledger_events.keys(c)))
        except ValueError as exc:
            return PlainTextResponse(f"Invalid key objectives: {exc}", status_code=400)
        return RedirectResponse(
            url=_assessment_url(root_path, project, project_id) + "#matrix", status_code=303
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


def _register_project_api(app, projects_of, view_in):
    """One assessed system: its graph, its ranking, its mapping, its tiers.

    Under the project, as the pages are (I5.2): the gate has decided on
    `{project}` and opened its database, and an id is looked for there only.
    """

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
            "selected": list(record.selected or []),
            "profile": view.profile,
            "dropped": view.dropped,
        }

    def _latest_or_409(projects, view) -> None:
        if (why := _read_only(projects, view)) is not None:
            raise HTTPException(status_code=409, detail=why)

    @app.get("/p/{project}/api/projects")
    def list_projects(project: str, request: Request) -> list[dict]:
        return [payload(view) for view in projects_of(request).list()]

    @app.get("/p/{project}/api/projects/{project_id}")
    def get_project(project: str, project_id: str, request: Request) -> dict:
        return payload(view_in(request, project_id))

    @app.post("/p/{project}/api/projects/{project_id}/map")
    def map_risks(project: str, project_id: str, request: Request) -> dict:
        projects = projects_of(request)
        _latest_or_409(projects, view_in(request, project_id))
        try:
            return payload(projects.map_risks_of(project_id, on_start=lambda s, run_id: ledger.emit(
                s, "ai.mapping.requested", item_type="assessment", item_id=project_id, run_id=run_id),
                on_save=lambda s, o: ledger_events.mapping_outcome(s, project_id, o)))
        except ModelUnavailable as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @app.post("/p/{project}/api/projects/{project_id}/risks/{risk_id}/mapping")
    def map_by_hand(
        project: str, project_id: str, risk_id: str, request: Request,
        objective_ids: list[str] = Body(..., embed=True),
    ) -> dict:
        """A person maps one risk to its objectives (replaces that risk's mapping)."""
        projects = projects_of(request)
        _latest_or_409(projects, view_in(request, project_id))
        try:
            return payload(projects.map_by_hand(project_id, risk_id, objective_ids, on_save=lambda s, c: ledger.emit(
                s, "mapping.risk.edited", item_type="risk_mapping",
                item_id=ledger_events.risk_item(project_id, risk_id), before=c["before"], after=c["after"],
                details={"added": sorted(set(c["after"]) - set(c["before"])),
                         "removed": sorted(set(c["before"]) - set(c["after"]))})))
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/p/{project}/api/projects/{project_id}/ratings")
    def rate(
        project: str, project_id: str, request: Request, ratings: dict[str, dict[str, int]] = Body(...)
    ) -> dict:
        """{risk id: {"impact": 1-5, "likelihood": 1-5}}; a part left out keeps what it had."""
        projects = projects_of(request)
        _latest_or_409(projects, view_in(request, project_id))
        impact = {rid: r["impact"] for rid, r in ratings.items() if "impact" in r}
        likelihood = {rid: r["likelihood"] for rid, r in ratings.items() if "likelihood" in r}
        try:
            return payload(projects.rate(project_id, impact, likelihood, record=lambda s, changed: (
                [ledger.emit(s, "risk.rated", **e) for e in ledger_events.ratings(changed)],
                [ledger.emit(s, "risk.rating_comment.set", **e) for e in ledger_events.comments(changed)])))
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/p/{project}/api/projects/{project_id}/severity")
    def rate_impact(
        project: str, project_id: str, request: Request, ratings: dict[str, int] = Body(...)
    ) -> dict:
        """The route from before the matrix, kept for its callers: {risk id: 1-5} sets the impact
        (what a risk's severity was) and leaves the likelihood as it is."""
        projects = projects_of(request)
        _latest_or_409(projects, view_in(request, project_id))
        try:
            return payload(projects.rate(project_id, ratings, {}, record=lambda s, changed: (
                [ledger.emit(s, "risk.rated", **e) for e in ledger_events.ratings(changed)],
                [ledger.emit(s, "risk.rating_comment.set", **e) for e in ledger_events.comments(changed)])))
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/p/{project}/api/projects/{project_id}/key")
    def set_keys(
        project: str, project_id: str, request: Request, keys: dict[str, bool] = Body(...)
    ) -> dict:
        """{objective id: key}: the assessor's choice wins over the default."""
        projects = projects_of(request)
        _latest_or_409(projects, view_in(request, project_id))
        try:
            return payload(projects.set_keys(project_id, keys, record=lambda s, c: ledger.emit(
                s, "objective.key.set", item_type="assessment", item_id=project_id, before=c["before"],
                after=c["after"], details=ledger_events.keys(c))))
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/p/{project}/api/projects/{project_id}/profile")
    def use_profile(
        project: str, project_id: str, request: Request, profile_id: str = Body(..., embed=True),
    ) -> dict:
        """Run the assessment on a profile's current version; what falls outside is dropped."""
        projects = projects_of(request)
        _latest_or_409(projects, view_in(request, project_id))
        try:
            return payload(projects.use_profile(project_id, profile_id, record=lambda s, c: ledger.emit(
                s, "assessment.profile.switched", item_type="assessment", item_id=project_id,
                details={"version_before": c["before"], "version_after": c["after"], "dropped": c["dropped"]})))
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/p/{project}/api/projects/{project_id}/severity-comments")
    def comment_severities(
        project: str, project_id: str, request: Request, comments: dict[str, str] = Body(...)
    ) -> dict:
        """Why each risk is rated as it is: {risk id: comment}; a blank comment clears it."""
        projects = projects_of(request)
        _latest_or_409(projects, view_in(request, project_id))
        try:
            return payload(projects.rate(project_id, {}, {}, comments, record=lambda s, changed: (
                [ledger.emit(s, "risk.rated", **e) for e in ledger_events.ratings(changed)],
                [ledger.emit(s, "risk.rating_comment.set", **e) for e in ledger_events.comments(changed)])))
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.delete("/p/{project}/api/projects/{project_id}", status_code=204)
    def delete_project(project: str, project_id: str, request: Request) -> None:
        view_in(request, project_id)
        projects_of(request).delete(project_id, record=lambda s, held: ledger.emit(
            s, "assessment.deleted", item_type="assessment", item_id=project_id, content=held))
