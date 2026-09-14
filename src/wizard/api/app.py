"""Wizard service — app factory.

Two things to look at, and one project at a time:

    /                      the way in
    /objectives            the 50 control objectives, as a reference
    /projects              the systems being assessed
    /projects/{id}         upload · three questions · Confirm/Refuse · map · tiers

The JSON API mirrors the pages. Everything is persisted, so a restart loses
nothing.
"""

from __future__ import annotations

import json
from typing import Any, Literal

from fastapi import Body, FastAPI, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from wizard.config import RunConfig
from wizard.control_objectives import ControlObjectiveCatalogue
from wizard.models.control_objective import ControlObjective, MacroRequirement
from wizard.models.ontology import Ontology
from wizard.models.profile import Profile
from wizard.projects import Projects
from wizard.rendering import (
    STATIC,
    render_home_page,
    render_objectives_page,
    render_project_page,
    render_projects_page,
)

#: Filter over the assessment mode. A paired ("Control + Test") objective
#: answers to both, so the partitions overlap rather than splitting the set.
ModeFilter = Literal["control", "test"]

GRAPH_WANTED = (
    "that names no AIRO classes, so it is not an ontology export. Download "
    "ontology.jsonld from the system's card in the qualification app."
)


class AnswerBody(BaseModel):
    """The company's decision. Booleans: a model may be unsure, a register may not."""

    high_risk: bool
    personal_data: bool
    interacts_with_natural_persons: bool


class NoModel:
    """Stand-in when no extractor is wired: every fact comes back undetermined
    and the company answers all three by hand."""

    def propose(self, ontology, findings=()) -> Profile:
        return Profile()


class NoMapper:
    """Stand-in when no mapper is wired: every risk maps to nothing, and the
    page says so rather than pretending the risks were read."""

    def propose(self, risk, findings=()):
        from wizard.risk_mapping import Mapping

        return Mapping(risk_id=risk.id)


def create_app(
    objectives: ControlObjectiveCatalogue,
    projects: Projects,
    *,
    base_config: RunConfig | None = None,
    root_path: str = "",
    cors_origins: list[str] | None = None,
    source_name: str = "ai_act_control_objectives.csv",
) -> FastAPI:
    config = base_config or RunConfig()
    app = FastAPI(title="Wizard", root_path=root_path)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins or ["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    def _view_or_404(project_id: str):
        view = projects.view(project_id)
        if view is None:
            raise HTTPException(status_code=404, detail=f"Unknown project {project_id}")
        return view

    _register_pages(app, objectives, projects, _view_or_404, source_name, root_path)
    _register_objective_api(app, objectives, config)
    _register_project_api(app, projects, _view_or_404)
    return app


def _register_pages(app, objectives, projects, _view_or_404, source_name, root_path):
    """The three pages, and the forms that post to them."""

    @app.get("/", include_in_schema=False, response_class=HTMLResponse)
    def home_page() -> HTMLResponse:
        return HTMLResponse(
            render_home_page(objectives, len(projects.list()), root_path=root_path)
        )

    @app.get("/objectives", include_in_schema=False, response_class=HTMLResponse)
    def objectives_page() -> HTMLResponse:
        return HTMLResponse(
            render_objectives_page(objectives, source_name=source_name, root_path=root_path)
        )

    @app.get("/projects", include_in_schema=False, response_class=HTMLResponse)
    def projects_page() -> HTMLResponse:
        return HTMLResponse(render_projects_page(projects.list(), root_path=root_path))

    @app.post("/projects", include_in_schema=False)
    async def create_project_form(request: Request):
        form = await request.form()
        upload: UploadFile = form["ontology"]
        name = str(form.get("name") or "").strip()
        raw_bytes = upload.file.read()
        try:
            raw = json.loads(raw_bytes)
        except ValueError:
            return PlainTextResponse("The uploaded file is not valid JSON.", status_code=400)
        if not Ontology.looks_like_one(raw):
            return PlainTextResponse(f"That JSON {GRAPH_WANTED}", status_code=400)
        view = projects.create(name=name, jsonld=raw_bytes.decode("utf-8"), raw=raw)
        return RedirectResponse(url=f"{root_path}/projects/{view.record.id}", status_code=303)

    @app.get("/projects/{project_id}", include_in_schema=False, response_class=HTMLResponse)
    def project_page(project_id: str) -> HTMLResponse:
        return HTMLResponse(
            render_project_page(_view_or_404(project_id), objectives, root_path=root_path)
        )

    @app.post("/projects/{project_id}/answer", include_in_schema=False)
    async def answer_form(project_id: str, request: Request):
        _view_or_404(project_id)
        form = await request.form()
        if not all(form.get(name) in {"yes", "no"} for name in Profile.FACTS):
            return PlainTextResponse("Answer all three questions.", status_code=400)
        projects.answer(project_id, {name: form[name] == "yes" for name in Profile.FACTS})
        return RedirectResponse(url=f"{root_path}/projects/{project_id}", status_code=303)

    @app.post("/projects/{project_id}/map", include_in_schema=False)
    def map_form(project_id: str):
        _view_or_404(project_id)
        try:
            projects.map_risks_of(project_id)
        except PermissionError as exc:
            return PlainTextResponse(str(exc), status_code=409)
        return RedirectResponse(url=f"{root_path}/projects/{project_id}", status_code=303)

    @app.post("/projects/{project_id}/severity", include_in_schema=False)
    async def rate_form(project_id: str, request: Request):
        view = _view_or_404(project_id)
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
        return RedirectResponse(url=f"{root_path}/projects/{project_id}", status_code=303)


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


def _register_project_api(app, projects, _view_or_404):
    """One assessed system: its graph, its answer, its mapping, its tiers."""

    def payload(view) -> dict:
        record = view.record
        return {
            "id": record.id,
            "name": record.name,
            "system_name": record.system_name,
            "qualification_id": record.qualification_id,
            "digest": record.digest,
            "objectives_digest": record.objectives_digest,
            "created_at": record.created_at.isoformat(),
            "updated_at": record.updated_at.isoformat(),
            "risks": [risk.model_dump() for risk in record.ontology.risks],
            "severity": record.severity.model_dump(),
            "profile_run": record.profile_run.model_dump() if record.profile_run else None,
            "answer": record.answer.__dict__ if record.answer else None,
            "mapping_run": record.mapping_run.model_dump() if record.mapping_run else None,
            "verdicts": [v.model_dump() for v in view.verdicts],
            "priorities": [p.model_dump() for p in view.priorities],
            "can_map": view.can_map,
        }

    def _graph_or_422(raw: object) -> None:
        if not Ontology.looks_like_one(raw):
            raise HTTPException(status_code=422, detail=f"that body {GRAPH_WANTED}")

    @app.post("/api/projects", status_code=201)
    def create_project(ontology: Any = Body(...), name: str = Query("")) -> dict:
        """Upload a filled AIRO graph; the first workflow runs on it."""
        _graph_or_422(ontology)
        return payload(projects.create(name=name, jsonld=json.dumps(ontology), raw=ontology))

    @app.get("/api/projects")
    def list_projects() -> list[dict]:
        return [payload(view) for view in projects.list()]

    @app.get("/api/projects/{project_id}")
    def get_project(project_id: str) -> dict:
        return payload(_view_or_404(project_id))

    @app.post("/api/projects/{project_id}/ontology")
    def replace_graph(project_id: str, ontology: Any = Body(...)) -> dict:
        _view_or_404(project_id)
        _graph_or_422(ontology)
        return payload(
            projects.replace_graph(project_id, jsonld=json.dumps(ontology), raw=ontology)
        )

    @app.post("/api/projects/{project_id}/answer")
    def answer(project_id: str, body: AnswerBody) -> dict:
        _view_or_404(project_id)
        return payload(projects.answer(project_id, body.model_dump()))

    @app.post("/api/projects/{project_id}/map")
    def map_risks(project_id: str) -> dict:
        _view_or_404(project_id)
        try:
            return payload(projects.map_risks_of(project_id))
        except PermissionError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/api/projects/{project_id}/severity")
    def rate(project_id: str, ratings: dict[str, int] = Body(...)) -> dict:
        _view_or_404(project_id)
        try:
            return payload(projects.rate(project_id, ratings))
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.delete("/api/projects/{project_id}", status_code=204)
    def delete_project(project_id: str) -> None:
        _view_or_404(project_id)
        projects.delete(project_id)
