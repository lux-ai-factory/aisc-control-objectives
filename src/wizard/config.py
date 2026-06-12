"""Run configuration (SPEC_HARDENING WP0).

Every policy knob the operator owns lives here. Precedence:
per-run override (`with_overrides`) > environment (`from_env`) > defaults.
The effective config is echoed on each AssessmentPlan, so plans are always
interpretable after the fact.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

from pydantic import BaseModel, Field

DEFAULT_MODEL = "claude-opus-4-8"

Lens = Literal["relevance", "coverage", "parsimony"]


class GuardsConfig(BaseModel):
    evidence: Literal["drop", "demote", "off"] = "drop"
    coverage_claims: Literal["strip", "off"] = "strip"
    dataset_pairing: Literal["enforce", "off"] = "enforce"


class ReviewConfig(BaseModel):
    lenses: list[Lens] = Field(default_factory=list)
    reviewer_model: str | None = None


class RunConfig(BaseModel):
    model: str = DEFAULT_MODEL
    max_rounds: int = Field(3, ge=1, le=5)
    guards: GuardsConfig = Field(default_factory=GuardsConfig)
    review: ReviewConfig = Field(default_factory=ReviewConfig)

    @property
    def effective_reviewer_model(self) -> str:
        return self.review.reviewer_model or self.model

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> "RunConfig":
        data: dict[str, Any] = {}
        guards: dict[str, Any] = {}
        review: dict[str, Any] = {}

        if "WIZARD_MODEL" in env:
            data["model"] = env["WIZARD_MODEL"]
        if "WIZARD_MAX_ROUNDS" in env:
            # pass the raw string through — pydantic coerces digits and turns
            # junk into a ValidationError with field context (not a ValueError)
            data["max_rounds"] = env["WIZARD_MAX_ROUNDS"]
        if "WIZARD_EVIDENCE_POLICY" in env:
            guards["evidence"] = env["WIZARD_EVIDENCE_POLICY"]
        if "WIZARD_COVERAGE_CLAIMS" in env:
            guards["coverage_claims"] = env["WIZARD_COVERAGE_CLAIMS"]
        if "WIZARD_DATASET_PAIRING" in env:
            guards["dataset_pairing"] = env["WIZARD_DATASET_PAIRING"]
        if "WIZARD_REVIEW_LENSES" in env:
            review["lenses"] = [
                lens.strip()
                for lens in env["WIZARD_REVIEW_LENSES"].split(",")
                if lens.strip()
            ]
        if "WIZARD_REVIEWER_MODEL" in env:
            review["reviewer_model"] = env["WIZARD_REVIEWER_MODEL"]

        if guards:
            data["guards"] = guards
        if review:
            data["review"] = review
        return cls.model_validate(data)

    def with_overrides(self, overrides: dict[str, Any] | None) -> "RunConfig":
        """Deep-merge a partial override dict and re-validate."""
        if not overrides:
            return self
        merged = self.model_dump()
        for key, value in overrides.items():
            if isinstance(value, dict) and isinstance(merged.get(key), dict):
                merged[key] = {**merged[key], **value}
            else:
                merged[key] = value
        return RunConfig.model_validate(merged)
