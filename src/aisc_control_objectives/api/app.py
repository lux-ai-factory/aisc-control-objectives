"""The app factory: pages, the JSON API and the access gate.

Two things to look at, and one project at a time:

    /                               to the launcher, where a project is chosen
    /objectives                     the control objectives, as a reference
    /p/{project}                    the way in, inside one platform project
    /p/{project}/projects           its assessments, one per AI card version
    /p/{project}/projects/{id}      one assessment: risk register, matrix, key objectives
    /p/{project}/sets, /profiles    the project's own objective sets and profiles

    /p/{project}/api/...            the JSON API, inside the project too

Each project's assessments are in that project's own database: the gate opens the database of the project in the path, after
deciding the caller may be there, and every handler works in that database
only. An assessment of another project is not there, so it is a 404.
Everything is persisted, so a restart loses nothing.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Literal
from urllib.parse import urlencode

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

from aisc_control_objectives import ledger, projectdb, upstream
from aisc_control_objectives.access import NOT_FOUND, ProjectAccess
from aisc_control_objectives.api import ledger_events
from aisc_control_objectives.api.library_routes import register_library
from aisc_control_objectives.config import RunConfig
from aisc_control_objectives.control_objectives import ControlObjectiveCatalogue
from aisc_control_objectives.db.repository import ProjectRepository, digest_of
from aisc_control_objectives.models.control_objective import ControlObjective, MacroRequirement
from aisc_control_objectives.models.ontology import Ontology
from aisc_control_objectives.projects import AssessmentChanged, ModelUnavailable, Projects
from aisc_control_objectives.rendering import (
    DEFAULT_LAUNCHER_URL,
    STATIC,
    render_objectives_page,
    render_project_page,
    render_projects_page,
)

#: Filter over the assessment mode. A paired ("Control + Test") objective
#: answers to both, so the partitions overlap rather than splitting the set.
ModeFilter = Literal["control", "test"]

NO_CARD = "No AI card for the latest version yet"

#: How long the assessment page waits for qualification to say whether the card changed: past it, the
#: page shows without saying.
CARD_CHECK_TIMEOUT = 3.0

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
    return (f"Read-only: a newer AI card version (v{latest}) has its own assessment." if latest
            else "Read-only: its AI card version no longer exists.")


def may_read_errors(request: Request) -> bool:
    """Whether this caller reads a model run's error text: those who may change the
    project. A viewer sees only how the run stopped. Without a gate (the single-database domain
    tests) the service runs open, and the text is shown."""
    opened = getattr(request.state, "opened", None)
    access = opened.access if opened is not None else getattr(request.state, "access", None)
    if access is None:
        return getattr(request.state, "caller", None) is None
    return access.may_write


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


def _card_changed(pid: str, record, auth: str | None) -> bool:
    """Whether qualification now serves another card for the assessment's version than the one it
    started from. A qualification that does not answer, or a version with no card, says nothing."""
    if not record.system_id or not record.digest:
        return False
    try:
        card = upstream.card_jsonld(pid, record.system_id, auth, timeout=CARD_CHECK_TIMEOUT)
    except upstream.UpstreamDown:
        return False
    return card is not None and digest_of(card) != record.digest


def _caller(request: Request) -> str | None:
    """The caller's token as an Authorization value, to call the platform and qualification as them."""
    token = token_from_headers(request.headers)
    return f"Bearer {token}" if token else None


def _pid(request: Request, project: str) -> str:
    opened = getattr(request.state, "opened", None)
    return opened.pid if opened is not None else project


def _update_card(projects, project_id: str, view, pid: str, auth: str | None):
    """Re-read the card of the assessment's version and carry the work over (see Projects.update_card)."""
    jsonld, raw = _card_of({"project_id": pid, "pid": view.record.system_id}, auth)

    def record(session, change: dict) -> None:
        events = ledger_events.card_update({**change, "card_version": view.record.system_id})
        ledger.emit(session, "assessment.card_updated", **events["updated"])
        for e in events["ratings"]:
            ledger.emit(session, "risk.rated", **e)
        for e in events["comments"]:
            ledger.emit(session, "risk.rating_comment.set", **e)
        for e in events["mappings"]:
            ledger.emit(session, "mapping.risk.edited", **e)
        if events["keys"] is not None:
            ledger.emit(session, "objective.key.set", **events["keys"])

    return projects.update_card(project_id, jsonld, raw, record=record)


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
    # tests build it, and why server.build_app always passes one
    # (tests/test_api_auth.py checks this).
    if engine is not None:
        app.add_middleware(ProjectAccess, engine=engine, databases=databases)

    # The witnessed request (X-AISC-Request-Id) this request's ledger events cite.
    app.add_middleware(ledger.RequestId)

    # Same-origin behind Caddy: no other origin is allowed unless
    # CONTROL_OBJECTIVES_CORS_ORIGINS names it.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins or [],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    # A route rather than a StaticFiles mount: Caddy strips /control-objectives,
    # and a mount under a root_path only finds files when the prefix is still
    # on the path, so behind the proxy a mount would answer 404.
    @app.get("/static/{name}", include_in_schema=False)
    def static(name: str) -> FileResponse:
        path = STATIC / name
        if path.parent != STATIC or not path.is_file():
            raise HTTPException(status_code=404)
        return FileResponse(path)

    if databases is not None:
        @app.exception_handler(DBAPIError)
        async def database_error(request: Request, exc: DBAPIError):
            """A project database dropped under a running service: forget
            its engine, and the project is gone, 404. Anything else is a 500."""
            opened = getattr(request.state, "opened", None)
            if opened is not None and projectdb.is_missing_database(exc):
                databases.evict(opened.pid)
                message, status = NOT_FOUND.message, NOT_FOUND.status
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

    def editable(request: Request, project_id: str):
        """The service, the assessment, and why it may no longer change (None when it may): each
        family of routes refuses a write with that reason its own way (the pages as text, the API as JSON)."""
        projects = projects_of(request)
        view = view_in(request, project_id)
        return projects, view, _read_only(projects, view)

    _register_pages(app, objectives, projects_of, editable, source_name, root_path)
    _register_objective_api(app, objectives, config)
    _register_project_api(app, projects_of, view_in, editable)
    register_library(app, projects_of, root_path)
    return app


def _register_pages(app, objectives, projects_of, editable, source_name, root_path):
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

    @app.get("/p/{project}", include_in_schema=False)
    def project_root(project: str, request: Request) -> RedirectResponse:
        """The launcher's card opens here: straight to the assessment of the project's latest card
        version (the page with the left menu), or to the assessments page, where one is started,
        when that version has none, there is no card yet, or the platform does not answer."""
        opened = getattr(request.state, "opened", None)
        pid = opened.pid if opened is not None else project
        token = token_from_headers(request.headers)
        try:
            latest = upstream.latest_version(pid, f"Bearer {token}" if token else None)
        except upstream.UpstreamDown:
            latest = None
        found = projects_of(request).find_by_system(latest["pid"]) if latest else None
        if found is not None:
            return RedirectResponse(url=_assessment_url(root_path, project, found.record.id), status_code=307)
        return RedirectResponse(url=f"{root_path}/p/{project}/projects", status_code=307)

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
        projects = projects_of(request)
        views = projects.list()
        latest = next((view for view in views if projects.is_latest(view)), None)
        return HTMLResponse(
            render_projects_page(views, latest=latest, root_path=root_path, project=project)
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
                                                details=ledger_events.started(latest["pid"], c),
                                                content=c.get("carried")),
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
    def project_page(project: str, project_id: str, request: Request, dropped: str = "",
                     replace: str = "", reset: str = "") -> HTMLResponse:
        projects, view, read_only = editable(request, project_id)
        library = projects.library
        # `dropped` comes back from a profile switch in the address, where anyone can write
        # anything: only ids of real objectives are shown
        named = [oid for oid in dict.fromkeys(dropped.split(",")) if oid]
        known = library.known_ids() if named else set()
        return HTMLResponse(
            render_project_page(
                view, objectives, root_path=root_path, project=project,
                read_only=read_only,
                profiles=library.profiles(),
                show_error=may_read_errors(request),
                dropped=[oid for oid in named if oid in known],
                confirm_replace=replace == "ask" and read_only is None and bool(view.record.by_hand),
                confirm_reset=reset == "ask" and read_only is None and bool(view.record.keys),
                card_changed=_card_changed(_pid(request, project), view.record, _caller(request)),
            )
        )

    @app.post("/p/{project}/projects/{project_id}/map", include_in_schema=False)
    async def map_form(project: str, project_id: str, request: Request):
        """The one agentic step, on a button: it costs a model call per risk. "Also replace objectives
        I linked by hand" maps the risks a person mapped as well, once the page has asked."""
        projects, view, why = editable(request, project_id)
        if why is not None:
            return PlainTextResponse(why, status_code=409)
        form = await request.form()
        replace = bool(form.get("replace"))
        if replace and view.record.by_hand and not form.get("confirmed"):
            return RedirectResponse(
                url=_assessment_url(root_path, project, project_id) + "?replace=ask#matrix", status_code=303)
        try:
            projects.map_risks_of(project_id, replace=replace,
                                  on_start=ledger_events.on_mapping_requested(project_id),
                                  on_save=ledger_events.on_mapping_run(project_id))
        except ModelUnavailable as exc:
            return PlainTextResponse(str(exc), status_code=502)
        except AssessmentChanged as exc:
            return PlainTextResponse(str(exc), status_code=409)
        # back on the part of the page's menu that shows what the AI suggested
        return RedirectResponse(
            url=_assessment_url(root_path, project, project_id) + "#matrix", status_code=303
        )

    @app.post("/p/{project}/projects/{project_id}/card", include_in_schema=False)
    def card_form(project: str, project_id: str, request: Request):
        """"Update": the card of the assessment's version changed; read it again and carry the work over."""
        projects, view, why = editable(request, project_id)
        if why is not None:
            return PlainTextResponse(why, status_code=409)
        try:
            _update_card(projects, project_id, view, _pid(request, project), _caller(request))
        except _Refused as refused:
            return PlainTextResponse(refused.message, status_code=refused.status_code)
        except AssessmentChanged as exc:
            return PlainTextResponse(str(exc), status_code=409)
        return RedirectResponse(url=_assessment_url(root_path, project, project_id), status_code=303)

    @app.post("/p/{project}/projects/{project_id}/risks/{risk_id}/mapping", include_in_schema=False)
    async def map_by_hand_form(project: str, project_id: str, risk_id: str, request: Request):
        """A person's mapping of one risk: the ticked objectives, and only those."""
        projects, view, why = editable(request, project_id)
        if why is not None:
            return PlainTextResponse(why, status_code=409)
        form = await request.form()
        try:
            projects.map_by_hand(project_id, risk_id, [str(v) for v in form.getlist("objective")],
                                 on_save=ledger_events.on_mapping_edited(project_id, risk_id))
        except ValueError as exc:
            return PlainTextResponse(f"Invalid links: {exc}", status_code=400)
        except AssessmentChanged as exc:
            return PlainTextResponse(str(exc), status_code=409)
        return RedirectResponse(
            url=_assessment_url(root_path, project, project_id) + f"#map-{risk_id}", status_code=303
        )

    @app.post("/p/{project}/projects/{project_id}/profile", include_in_schema=False)
    async def profile_form(project: str, project_id: str, request: Request):
        """Run the assessment on another objective profile, or on its profile's newer version."""
        projects, view, why = editable(request, project_id)
        if why is not None:
            return PlainTextResponse(why, status_code=409)
        form = await request.form()
        try:
            switched = projects.use_profile(project_id, str(form.get("profile") or ""),
                                            record=ledger_events.on_profile_switched(project_id))
        except ValueError as exc:
            return PlainTextResponse(f"Invalid profile: {exc}", status_code=400)
        # the page says what the switch dropped
        url = _assessment_url(root_path, project, project_id)
        if switched.dropped:
            url += "?" + urlencode({"dropped": ",".join(switched.dropped)}, safe=",")
        return RedirectResponse(url=url, status_code=303)

    @app.post("/p/{project}/projects/{project_id}/severity", include_in_schema=False)
    async def rate_form(project: str, project_id: str, request: Request):
        projects, view, why = editable(request, project_id)
        if why is not None:
            return PlainTextResponse(why, status_code=409)
        form = await request.form()
        try:
            # per risk: `impact:<risk id>`, `likelihood:<risk id>` (1-5, blank clears it; a risk the
            # form does not name keeps what it had) and an optional `comment:<risk id>` (a blank one
            # clears it)
            def part(prefix):
                return {key.split(":", 1)[1]: int(value) if str(value).strip() else None
                        for key, value in form.items() if key.startswith(prefix)}
            comments = {
                key.split(":", 1)[1]: str(value)
                for key, value in form.items() if key.startswith("comment:")
            }
            projects.rate(project_id, part("impact:"), part("likelihood:"), comments,
                          record=ledger_events.on_rated())
        except ValueError as exc:
            return PlainTextResponse(f"Invalid rating: {exc}", status_code=400)
        # back on the part of the page's menu the ratings were saved from
        return RedirectResponse(
            url=_assessment_url(root_path, project, project_id) + "#register", status_code=303
        )


    @app.post("/p/{project}/projects/{project_id}/key", include_in_schema=False)
    async def key_form(project: str, project_id: str, request: Request):
        """The key ticks of the matrix. Only a tick the assessor changed is saved as their choice:
        `shown` says what the page showed (`<objective id>:1` key, `:0` not), and every tick left as
        it was keeps following the ratings. Without it, the comparison is with what is key now."""
        projects, view, why = editable(request, project_id)
        if why is not None:
            return PlainTextResponse(why, status_code=409)
        form = await request.form()
        ticked = {str(v) for v in form.getlist("key")}
        shown = {}
        for value in form.getlist("shown"):
            objective_id, _, flag = str(value).rpartition(":")
            if objective_id:
                shown[objective_id] = flag == "1"
        if not shown:
            shown = {p.objective_id: p.key for p in view.priorities if p.risk_ids}
        changed = {oid: oid in ticked for oid in set(shown) | ticked if (oid in ticked) != shown.get(oid, False)}
        try:
            projects.set_keys(project_id, changed, record=ledger_events.on_keys_set(project_id))
        except ValueError as exc:
            return PlainTextResponse(f"Invalid key objectives: {exc}", status_code=400)
        return RedirectResponse(
            url=_assessment_url(root_path, project, project_id) + "#matrix", status_code=303
        )

    @app.post("/p/{project}/projects/{project_id}/keys/reset", include_in_schema=False)
    async def reset_keys_form(project: str, project_id: str, request: Request):
        """"Reset keys to defaults": every key choice of the assessment is removed, once the page has
        asked, and every objective follows the ratings again."""
        projects, view, why = editable(request, project_id)
        if why is not None:
            return PlainTextResponse(why, status_code=409)
        form = await request.form()
        if not form.get("confirmed"):
            return RedirectResponse(
                url=_assessment_url(root_path, project, project_id) + "?reset=ask#matrix", status_code=303)
        projects.reset_keys(project_id, record=ledger_events.on_keys_set(project_id))
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


def _register_project_api(app, projects_of, view_in, editable):
    """One assessed system: its graph, its ratings, its mapping, its priorities.

    Under the project, as the pages are: the gate has decided on
    `{project}` and opened its database, and an id is looked for there only.
    """

    def payload(view, request: Request | None = None) -> dict:
        record = view.record
        run = record.mapping_run.model_dump() if record.mapping_run else None
        if run is not None and request is not None and not may_read_errors(request):
            run["error"] = ""
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
            "mapping_run": run,
            "priorities": [p.model_dump() for p in view.priorities],
            "mapped": view.mapped,
            "selected": list(record.selected or []),
            "profile": view.profile,
            "dropped": view.dropped,
            "carried": record.carried,
        }

    def latest_or_409(request: Request, project_id: str):
        """The service and the assessment, or the 409 a write to an assessment that may no longer change gets."""
        projects, view, why = editable(request, project_id)
        if why is not None:
            raise HTTPException(status_code=409, detail=why)
        return projects, view

    @app.get("/p/{project}/api/projects")
    def list_projects(project: str, request: Request) -> list[dict]:
        return [payload(view, request) for view in projects_of(request).list()]

    @app.get("/p/{project}/api/projects/{project_id}")
    def get_project(project: str, project_id: str, request: Request) -> dict:
        return payload(view_in(request, project_id), request)

    @app.post("/p/{project}/api/projects/{project_id}/map")
    def map_risks(project: str, project_id: str, request: Request, replace: bool = Query(False)) -> dict:
        """Map the risks no person has mapped; `replace` maps those too."""
        projects, _view = latest_or_409(request, project_id)
        try:
            return payload(projects.map_risks_of(project_id, replace=replace,
                                                 on_start=ledger_events.on_mapping_requested(project_id),
                                                 on_save=ledger_events.on_mapping_run(project_id)))
        except ModelUnavailable as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        except AssessmentChanged as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/p/{project}/api/projects/{project_id}/card")
    def update_card(project: str, project_id: str, request: Request) -> dict:
        """Put the assessment on its version's card as qualification serves it now; the work on the risks
        that stayed the same comes along. Nothing changes when the card is the one it holds."""
        projects, view = latest_or_409(request, project_id)
        try:
            return payload(_update_card(projects, project_id, view, _pid(request, project), _caller(request)))
        except _Refused as refused:
            raise HTTPException(status_code=refused.status_code, detail=refused.message) from refused
        except AssessmentChanged as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/p/{project}/api/projects/{project_id}/risks/{risk_id}/mapping")
    def map_by_hand(
        project: str, project_id: str, risk_id: str, request: Request,
        objective_ids: list[str] = Body(..., embed=True),
    ) -> dict:
        """A person maps one risk to its objectives (replaces that risk's mapping)."""
        projects, _view = latest_or_409(request, project_id)
        try:
            return payload(projects.map_by_hand(project_id, risk_id, objective_ids,
                                                on_save=ledger_events.on_mapping_edited(project_id, risk_id)))
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except AssessmentChanged as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/p/{project}/api/projects/{project_id}/ratings")
    def rate(
        project: str, project_id: str, request: Request, ratings: dict[str, dict[str, int | None]] = Body(...)
    ) -> dict:
        """{risk id: {"impact": 1-5, "likelihood": 1-5}}; a part left out keeps what it had, null clears it."""
        projects, _view = latest_or_409(request, project_id)
        impact = {rid: r["impact"] for rid, r in ratings.items() if "impact" in r}
        likelihood = {rid: r["likelihood"] for rid, r in ratings.items() if "likelihood" in r}
        try:
            return payload(projects.rate(project_id, impact, likelihood, record=ledger_events.on_rated()))
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/p/{project}/api/projects/{project_id}/key")
    def set_keys(
        project: str, project_id: str, request: Request, keys: dict[str, bool] = Body(...)
    ) -> dict:
        """{objective id: key}: the assessor's choice wins over the default."""
        projects, _view = latest_or_409(request, project_id)
        try:
            return payload(projects.set_keys(project_id, keys, record=ledger_events.on_keys_set(project_id)))
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/p/{project}/api/projects/{project_id}/keys/reset")
    def reset_keys(project: str, project_id: str, request: Request) -> dict:
        """Remove every key choice: each objective follows the ratings again."""
        projects, _view = latest_or_409(request, project_id)
        return payload(projects.reset_keys(project_id, record=ledger_events.on_keys_set(project_id)))

    @app.post("/p/{project}/api/projects/{project_id}/profile")
    def use_profile(
        project: str, project_id: str, request: Request, profile_id: str = Body(..., embed=True),
    ) -> dict:
        """Run the assessment on a profile's current version; what falls outside is dropped."""
        projects, _view = latest_or_409(request, project_id)
        try:
            return payload(projects.use_profile(project_id, profile_id,
                                                record=ledger_events.on_profile_switched(project_id)))
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/p/{project}/api/projects/{project_id}/severity-comments")
    def comment_severities(
        project: str, project_id: str, request: Request, comments: dict[str, str] = Body(...)
    ) -> dict:
        """Why each risk is rated as it is: {risk id: comment}; a blank comment clears it."""
        projects, _view = latest_or_409(request, project_id)
        try:
            return payload(projects.rate(project_id, {}, {}, comments, record=ledger_events.on_rated()))
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.delete("/p/{project}/api/projects/{project_id}", status_code=204)
    def delete_project(project: str, project_id: str, request: Request) -> None:
        # an older version's assessment is kept as it was, like every other write to it
        projects, _view = latest_or_409(request, project_id)
        projects.delete(project_id, record=lambda s, held: ledger.emit(
            s, "assessment.deleted", item_type="assessment", item_id=project_id, content=held))
