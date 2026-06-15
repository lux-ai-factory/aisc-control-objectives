"""Wizard service (SPEC §7) — app factory with injected dependencies.

v1 keeps plans in memory and runs the pipeline synchronously inside the
request (the MCAS-sized catalogue makes runs short); the SPEC's background-job
and SSE upgrades slot in behind the same routes. Persistence (the `wizard`
Postgres database) replaces InMemoryPlanStore post-demo.
"""

from __future__ import annotations

import os
import re
from typing import Protocol

from fastapi import Body, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ValidationError

from wizard.config import RunConfig
from wizard.models.plan import AssessmentPlan
from wizard.models.system_card import SystemCard


class QualificationProvider(Protocol):
    def list_qualifications(self) -> list[dict]: ...
    def get_system_card(self, qualification_id: str) -> SystemCard | None: ...


class PlanRunner(Protocol):
    def run(
        self, card: SystemCard, config: RunConfig | None = None
    ) -> AssessmentPlan: ...


class CreatePlanRequest(BaseModel):
    qualification_id: str
    config: dict | None = None  # partial RunConfig override (WP0)


class FinalizeRequest(BaseModel):
    deselect: list[str] = []


class InMemoryPlanStore:
    def __init__(self):
        self._plans: dict[str, AssessmentPlan] = {}

    def save(self, plan: AssessmentPlan) -> None:
        self._plans[plan.plan_id] = plan

    def get(self, plan_id: str) -> AssessmentPlan | None:
        return self._plans.get(plan_id)

    def list(self) -> list[AssessmentPlan]:
        return sorted(self._plans.values(), key=lambda p: p.created_at, reverse=True)


class ActiveRecommendation(BaseModel):
    """The currently-selected run reduced to what the catalogue's 'Recommended'
    filter needs: slug → recommendation score (1–5) plus a little context."""

    plan_id: str
    system_name: str
    scores: dict[str, int]  # catalogue slug → 1–5 (highest wins if duplicated)
    test_count: int  # recommended tests + datasets
    control_count: int  # recommended control checklists

    @classmethod
    def from_plan(cls, plan: AssessmentPlan) -> ActiveRecommendation:
        scores: dict[str, int] = {}
        for item in [*plan.tests, *plan.datasets, *plan.checklists]:
            scores[item.item_id] = max(scores.get(item.item_id, 0), item.score)
        return cls(
            plan_id=plan.plan_id,
            system_name=plan.system_name,
            scores=scores,
            test_count=len(plan.tests) + len(plan.datasets),
            control_count=len(plan.checklists),
        )


class ActiveRecommendationStore:
    """Server-owned 'which run drives Recommended' state.

    The active selection used to live only in the browser's localStorage, which
    breaks once the Wizard has its own (separate-origin) UI. Holding it here makes
    the Wizard API the single source of truth; the catalogue reads it over HTTP.

    Single-user demo: ONE global active recommendation held in memory, optionally
    persisted to a JSON file so it survives a process restart (Postgres is the
    post-demo home). It is read from disk once at startup and is therefore not
    safe across multiple worker processes — run the service single-worker, which
    is the current deployment. The stored value is the validated model, so a
    stale / hand-edited / older-schema file degrades to "no active
    recommendation" instead of crashing later reads."""

    def __init__(self, path: str | None = None):
        self._path = path
        self._current: ActiveRecommendation | None = None
        if path and os.path.isfile(path):
            try:
                with open(path) as fh:
                    # ValueError covers JSONDecodeError AND pydantic's
                    # ValidationError (both subclass it), so a corrupt or
                    # schema-mismatched file becomes None rather than a later 500.
                    self._current = ActiveRecommendation.model_validate_json(fh.read())
            except (OSError, ValueError):
                self._current = None

    def get(self) -> ActiveRecommendation | None:
        return self._current

    def set(self, recommendation: ActiveRecommendation) -> None:
        self._current = recommendation
        self._write()

    def clear(self) -> None:
        self._current = None
        if self._path:
            try:
                os.remove(self._path)
            except OSError:
                pass

    def _write(self) -> None:
        if not self._path or self._current is None:
            return
        # Write to a temp file in the same dir then atomically rename, so a crash
        # mid-write can't leave a truncated/corrupt file in place.
        tmp = f"{self._path}.tmp"
        try:
            with open(tmp, "w") as fh:
                fh.write(self._current.model_dump_json())
            os.replace(tmp, self._path)
        except OSError:
            pass


def _pdf_response(plan: AssessmentPlan) -> Response:
    """Render a plan to a PDF download response. Shared by both PDF routes.

    `plan.plan_id` is client-controlled, so it is sanitised before going into the
    Content-Disposition filename (a quote or CR/LF would otherwise break or
    inject the header)."""
    # imported lazily so the API doesn't require the native PDF stack unless a
    # PDF is actually requested
    from wizard.rendering import render_plan_pdf

    pdf = render_plan_pdf(plan)
    safe_id = re.sub(r"[^A-Za-z0-9._-]", "_", plan.plan_id) or "plan"
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="assessment-plan-{safe_id}.pdf"'
        },
    )


def create_app(
    qualification_provider: QualificationProvider,
    plan_runner: PlanRunner,
    base_config: RunConfig | None = None,
    cors_origins: list[str] | None = None,
    root_path: str = "",
    active_state_file: str | None = None,
) -> FastAPI:
    # `root_path` lets the service sit behind a reverse proxy under a sub-path
    # (e.g. Caddy `/wizard*`), so its `/api/*` routes don't collide with another
    # service's `/api/*`. Empty (the default) keeps the standalone behaviour.
    app = FastAPI(title="Wizard to Select Tests & Datasets", root_path=root_path)
    # The catalogue UI (a separate origin/port) calls this service directly,
    # so browsers need CORS. Defaults to permissive in dev; override per deploy.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins or ["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    store = InMemoryPlanStore()
    active = ActiveRecommendationStore(active_state_file)
    base = base_config or RunConfig.from_env(os.environ)

    def _run_and_store(card: SystemCard, overrides: dict | None):
        try:
            effective = base.with_overrides(overrides)
        except ValidationError as exc:
            raise HTTPException(422, f"invalid config override: {exc}") from exc
        plan = plan_runner.run(card, config=effective)
        store.save(plan)  # failed plans are stored too — they are audit records
        if plan.status == "failed":
            return JSONResponse(status_code=502, content=plan.model_dump(mode="json"))
        return plan

    @app.get("/api/config")
    def get_config() -> RunConfig:
        return base

    @app.get("/api/qualifications")
    def list_qualifications() -> list[dict]:
        return qualification_provider.list_qualifications()

    @app.post("/api/plans", status_code=201)
    def create_plan(request: CreatePlanRequest) -> AssessmentPlan:
        card = qualification_provider.get_system_card(request.qualification_id)
        if card is None:
            raise HTTPException(404, f"qualification '{request.qualification_id}' not found or has no system card")
        return _run_and_store(card, request.config)

    @app.post("/api/plans/from-card", status_code=201)
    def create_plan_from_card(
        body: dict = Body(...),
    ) -> AssessmentPlan:
        """Run the pipeline against an uploaded system card directly, with no
        qualification lookup (SPEC flow variant — the catalogue 'upload a
        system card' entry point). `config` is an optional partial RunConfig
        override alongside the card under a `config` key, or the body is the
        bare card itself."""
        raw_card = body.get("card", body)
        overrides = body.get("config")
        try:
            card = SystemCard.from_card_json(raw_card)
        except ValidationError as exc:
            raise HTTPException(422, f"invalid system card: {exc}") from exc
        return _run_and_store(card, overrides)

    @app.get("/api/plans")
    def list_plans() -> list[AssessmentPlan]:
        return store.list()

    @app.get("/api/plans/{plan_id}")
    def get_plan(plan_id: str) -> AssessmentPlan:
        plan = store.get(plan_id)
        if plan is None:
            raise HTTPException(404, f"plan '{plan_id}' not found")
        return plan

    @app.get("/api/plans/{plan_id}/pdf")
    def export_plan_pdf(plan_id: str) -> Response:
        plan = store.get(plan_id)
        if plan is None:
            raise HTTPException(404, f"plan '{plan_id}' not found")
        return _pdf_response(plan)

    @app.post("/api/plans/render-pdf")
    def render_posted_plan_pdf(plan: AssessmentPlan = Body(...)) -> Response:
        """Render a PDF from a plan supplied in the request body.

        The server keeps plans only in memory, so the GET-by-id route 404s after
        a restart. The catalogue UI holds the full plan in localStorage, so it
        POSTs it here — PDF export then works for any run regardless of restarts.
        """
        return _pdf_response(plan)

    # ── Active recommendation (drives the catalogue's "Recommended" filter) ──
    # The Wizard API owns this state so any client — including a separate-origin
    # Wizard UI — can set it and the catalogue can read it over HTTP.
    @app.put("/api/recommendation/active")
    def set_active_recommendation(
        plan: AssessmentPlan = Body(...),
    ) -> ActiveRecommendation:
        recommendation = ActiveRecommendation.from_plan(plan)
        active.set(recommendation)
        return recommendation

    @app.get("/api/recommendation/active")
    def get_active_recommendation() -> ActiveRecommendation | None:
        return active.get()

    @app.delete("/api/recommendation/active", status_code=204)
    def clear_active_recommendation() -> Response:
        active.clear()
        return Response(status_code=204)

    @app.post("/api/plans/{plan_id}/finalize")
    def finalize_plan(
        plan_id: str,
        request: FinalizeRequest = Body(default_factory=FinalizeRequest),
    ) -> AssessmentPlan:
        plan = store.get(plan_id)
        if plan is None:
            raise HTTPException(404, f"plan '{plan_id}' not found")
        if plan.status in ("finalized", "exported"):
            raise HTTPException(409, f"plan is already {plan.status}")
        if plan.status == "failed":
            raise HTTPException(409, "a failed plan cannot be finalized")
        updated = plan.finalized_without(set(request.deselect))
        store.save(updated)
        return updated

    return app
