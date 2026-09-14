"""Wizard service — app factory.

Pages: the objectives (with the upload form) at the root, and one page per
assessed card where a person confirms the profile and reads the verdicts. The
JSON API mirrors both. Uploads live in memory, so a restart loses them.
"""

from __future__ import annotations

import json
from typing import Literal

from fastapi import Body, FastAPI, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError, create_model

from wizard.cards import (
    CardRecord,
    CardStore,
    add_qualification,
    assess_card,
    confirm_profile,
    rate_severity,
)
from wizard.config import RunConfig
from wizard.control_objectives import ControlObjectiveCatalogue
from wizard.models.control_objective import ControlObjective, MacroRequirement
from wizard.models.profile import Answer, Profile
from wizard.prioritising import Severity
from wizard.models.qualification import Qualification
from wizard.models.system_card import SystemCard
from wizard.profiling import Extractor
from wizard.risk_mapping import Mapper
from wizard.rendering import STATIC, render_card_page, render_objectives_page

#: Filter over the assessment mode. A paired ("Control + Test") objective
#: answers to both, so the partitions overlap rather than splitting the set.
ModeFilter = Literal["control", "test"]


#: A person's confirmation: any subset of the facts, derived from Profile.FACTS
#: so the API cannot drift from the profile.
ProfileAnswers = create_model(
    "ProfileAnswers", **{name: (Answer | None, None) for name in Profile.FACTS}
)


class NoModel:
    """Stand-in when no extractor is wired: every fact comes back undetermined
    and the person fills the profile in by hand."""

    def propose(self, card: SystemCard, findings=()) -> Profile:
        return Profile()


class NoMapper:
    """Stand-in when no mapper is wired: every risk maps to nothing, and the
    page says so rather than pretending the risks were read."""

    def propose(self, risk, findings=()):
        from wizard.risk_mapping import Mapping

        return Mapping(risk_id=risk.id)


def create_app(
    objectives: ControlObjectiveCatalogue,
    *,
    base_config: RunConfig | None = None,
    root_path: str = "",
    cors_origins: list[str] | None = None,
    source_name: str = "ai_act_control_objectives.csv",
    extractor: Extractor | None = None,
    mapper: Mapper | None = None,
    store: CardStore | None = None,
) -> FastAPI:
    config = base_config or RunConfig()
    extractor = extractor or NoModel()
    mapper = mapper or NoMapper()
    store = store or CardStore()
    app = FastAPI(title="Wizard", root_path=root_path)

    # Permissive by default for dev; tighten per deploy with an allowlist.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins or ["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    # Brand assets for the pages (the Luxembourg AI Factory mark).
    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    def _record_or_404(record_id: str) -> CardRecord:
        record = store.get(record_id)
        if record is None:
            raise HTTPException(status_code=404, detail=f"Unknown card {record_id}")
        return record

    # ── pages ──────────────────────────────────────────────────────────────

    @app.get("/", include_in_schema=False, response_class=HTMLResponse)
    def objectives_page() -> HTMLResponse:
        return HTMLResponse(
            render_objectives_page(objectives, source_name=source_name, root_path=root_path)
        )

    @app.post("/cards", include_in_schema=False)
    def upload_card_form(card: UploadFile):
        """The upload form: parse, assess, land on the card's page.

        Deliberately not `async`: assess_card blocks for as long as the model
        takes, and a coroutine would hold the event loop (and /health) for it.
        A plain def runs in the threadpool, like the JSON twin below."""
        raw = card.file.read()
        try:
            data = json.loads(raw)
        except ValueError:
            return PlainTextResponse("The uploaded file is not valid JSON.", status_code=400)
        try:
            system_card = SystemCard.from_card_json(data)
        except ValidationError as exc:
            return PlainTextResponse(
                f"The JSON is not a system card: {exc.error_count()} field problem(s).\n{exc}",
                status_code=400,
            )
        record = assess_card(system_card, extractor, objectives)
        store.save(record)
        return RedirectResponse(url=f"{root_path}/cards/{record.id}", status_code=303)

    @app.get("/cards/{record_id}", include_in_schema=False, response_class=HTMLResponse)
    def card_page(record_id: str) -> HTMLResponse:
        record = _record_or_404(record_id)
        return HTMLResponse(render_card_page(record, objectives, root_path=root_path))

    @app.post("/cards/{record_id}/profile", include_in_schema=False)
    async def confirm_profile_form(record_id: str, request: Request):
        record = _record_or_404(record_id)
        form = await request.form()
        try:
            answers = ProfileAnswers.model_validate(
                {name: form.get(name) for name in Profile.FACTS if form.get(name)}
            )
        except ValidationError as exc:
            return PlainTextResponse(f"Invalid answer: {exc}", status_code=400)
        store.save(confirm_profile(record, answers.model_dump(exclude_none=True), objectives))
        return RedirectResponse(url=f"{root_path}/cards/{record_id}", status_code=303)

    @app.post("/cards/{record_id}/qualification", include_in_schema=False)
    def add_qualification_form(record_id: str, qualification: UploadFile):
        """Not async: mapping every risk blocks for as long as the model takes."""
        record = _record_or_404(record_id)
        try:
            raw = json.loads(qualification.file.read())
        except ValueError:
            return PlainTextResponse("The uploaded file is not valid JSON.", status_code=400)
        if not Qualification.looks_like_one(raw):
            return PlainTextResponse(
                "That JSON has no `risks`, so it is not a qualification export. "
                "The system card goes in the box on the front page.",
                status_code=400,
            )
        try:
            parsed = Qualification.from_export(raw)
        except ValidationError as exc:
            return PlainTextResponse(f"Not a qualification export: {exc}", status_code=400)
        store.save(add_qualification(record, parsed, mapper, objectives))
        return RedirectResponse(url=f"{root_path}/cards/{record_id}", status_code=303)

    @app.post("/cards/{record_id}/severity", include_in_schema=False)
    async def rate_severity_form(record_id: str, request: Request):
        record = _record_or_404(record_id)
        form = await request.form()
        risks = record.qualification.risks if record.qualification else []
        ratings = {risk.id: int(form[risk.id]) for risk in risks if form.get(risk.id)}
        try:
            store.save(rate_severity(record, ratings, objectives))
        except ValidationError as exc:
            return PlainTextResponse(f"Invalid rating: {exc}", status_code=400)
        return RedirectResponse(url=f"{root_path}/cards/{record_id}", status_code=303)

    # ── JSON API ───────────────────────────────────────────────────────────

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

    @app.post("/api/cards", response_model=CardRecord, status_code=201)
    def upload_card(card: SystemCard = Body(...)) -> CardRecord:
        """Assess a system card: propose its profile, decide the objectives."""
        record = assess_card(card, extractor, objectives)
        store.save(record)
        return record

    @app.get("/api/cards", response_model=list[CardRecord])
    def list_cards() -> list[CardRecord]:
        return store.list()

    @app.get("/api/cards/{record_id}", response_model=CardRecord)
    def get_card(record_id: str) -> CardRecord:
        return _record_or_404(record_id)

    @app.post("/api/cards/{record_id}/qualification", response_model=CardRecord)
    def add_qualification_json(record_id: str, qualification: dict = Body(...)) -> CardRecord:
        """Attach the qualification export whose risks drive the tiers."""
        record = _record_or_404(record_id)
        updated = add_qualification(
            record, Qualification.from_export(qualification), mapper, objectives
        )
        store.save(updated)
        return updated

    @app.post("/api/cards/{record_id}/severity", response_model=CardRecord)
    def rate(record_id: str, severity: Severity) -> CardRecord:
        """How much each macro requirement matters for this system, 1-5. The
        tiers follow; what applies does not change."""
        record = _record_or_404(record_id)
        updated = rate_severity(record, severity.ratings, objectives)
        store.save(updated)
        return updated

    @app.post("/api/cards/{record_id}/profile", response_model=CardRecord)
    def confirm(record_id: str, answers: ProfileAnswers) -> CardRecord:  # type: ignore[valid-type]
        """A person confirms or overrides the profile; the verdicts follow."""
        record = _record_or_404(record_id)
        updated = confirm_profile(
            record, answers.model_dump(exclude_none=True), objectives
        )
        store.save(updated)
        return updated

    return app
