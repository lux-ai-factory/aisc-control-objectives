"""Composition root: card → prefilter → agents → AssessmentPlan.

Implements the API layer's PlanRunner port. All policy comes from RunConfig
(WP0): per-run config (passed to run()) > instance config > env > defaults.
The effective config is echoed on the plan (`run_config`) so every plan
records the policies that produced it.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from typing import Any

from wizard.agents.llm import (
    LLMChecklistProposer,
    LLMReviewer,
    LLMTestProposer,
    MultiLensReviewer,
)
from wizard.agents.orchestrator import Orchestrator, Reviewer
from wizard.config import RunConfig
from wizard.matching.prefilter import (
    known_candidate_ids,
    prefilter_checklists,
    prefilter_tools,
)
from wizard.matching.tag_map import map_system_card_tags
from wizard.models.catalogue import CatalogueTool, ChecklistDoc
from wizard.models.plan import AssessmentPlan
from wizard.models.system_card import SystemCard


class WizardPlanRunner:
    def __init__(
        self,
        client: Any,
        tools: list[CatalogueTool],
        checklists: list[ChecklistDoc],
        config: RunConfig | None = None,
    ):
        self.client = client
        self.tools = tools
        self.checklists = checklists
        self.config = config or RunConfig.from_env(os.environ)

    def _build_reviewer(self, config: RunConfig, known_ids: set[str]) -> Reviewer:
        if config.review.lenses:
            return MultiLensReviewer(
                client=self.client,
                known_item_ids=known_ids,
                lenses=config.review.lenses,
                model=config.effective_reviewer_model,
            )
        return LLMReviewer(
            client=self.client,
            known_item_ids=known_ids,
            model=config.effective_reviewer_model,
        )

    def run(self, card: SystemCard, config: RunConfig | None = None) -> AssessmentPlan:
        cfg = config or self.config

        # surface taxonomy drift on the plan — never drop tags silently
        mapped = map_system_card_tags(
            sorted(card.target_system_slugs), sorted(card.sector_slugs)
        )
        tag_warnings = [
            f"tag-unmapped: '{tag}' has no catalogue ai_type mapping; "
            "its matching signal was lost"
            for tag in mapped.unmapped
        ]

        test_candidates = prefilter_tools(card, self.tools)
        checklist_candidates = prefilter_checklists(card, self.checklists)
        known_ids = known_candidate_ids(test_candidates, checklist_candidates)
        orchestrator = Orchestrator(
            test_proposer=LLMTestProposer(client=self.client, model=cfg.model),
            checklist_proposer=LLMChecklistProposer(client=self.client, model=cfg.model),
            reviewer=self._build_reviewer(cfg, known_ids),
            max_rounds=cfg.max_rounds,
            guards=cfg.guards,
        )
        try:
            plan = orchestrator.run(card, test_candidates, checklist_candidates)
        except Exception as exc:  # A4: a failed run is a failed plan, never a half-plan
            return AssessmentPlan(
                plan_id=str(uuid.uuid4()),
                qualification_id=card.qualification_id,
                system_name=card.system_name,
                created_at=datetime.now(timezone.utc),
                status="failed",
                warnings=[f"run-failed: {type(exc).__name__}: {exc}"] + tag_warnings,
                run_config=cfg.model_dump(),
            )
        return plan.model_copy(
            update={
                "run_config": cfg.model_dump(),
                "warnings": plan.warnings + tag_warnings,
            }
        )
