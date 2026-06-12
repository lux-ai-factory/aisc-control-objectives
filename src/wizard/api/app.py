"""Wizard service (SPEC §7) — app factory with injected dependencies.

v1 keeps plans in memory and runs the pipeline synchronously inside the
request (the MCAS-sized catalogue makes runs short); the SPEC's background-job
and SSE upgrades slot in behind the same routes. Persistence (the `wizard`
Postgres database) replaces InMemoryPlanStore post-demo.
"""

from __future__ import annotations

import os
from typing import Protocol

from fastapi import Body, FastAPI, HTTPException
from fastapi.responses import JSONResponse
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


def create_app(
    qualification_provider: QualificationProvider,
    plan_runner: PlanRunner,
    base_config: RunConfig | None = None,
) -> FastAPI:
    app = FastAPI(title="Wizard to Select Tests & Datasets")
    store = InMemoryPlanStore()
    base = base_config or RunConfig.from_env(os.environ)

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
        try:
            effective = base.with_overrides(request.config)
        except ValidationError as exc:
            raise HTTPException(422, f"invalid config override: {exc}") from exc
        plan = plan_runner.run(card, config=effective)
        store.save(plan)  # failed plans are stored too — they are audit records
        if plan.status == "failed":
            # model_dump(mode="json") already yields JSON-safe primitives
            return JSONResponse(status_code=502, content=plan.model_dump(mode="json"))
        return plan

    @app.get("/api/plans")
    def list_plans() -> list[AssessmentPlan]:
        return store.list()

    @app.get("/api/plans/{plan_id}")
    def get_plan(plan_id: str) -> AssessmentPlan:
        plan = store.get(plan_id)
        if plan is None:
            raise HTTPException(404, f"plan '{plan_id}' not found")
        return plan

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
