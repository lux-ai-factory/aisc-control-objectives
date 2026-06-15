"""Run configuration (SPEC_HARDENING WP0).

Every policy knob the operator owns lives here. Precedence:
per-run override (`with_overrides`) > environment (`from_env`) > defaults.
The effective config is echoed on each AssessmentPlan, so plans are always
interpretable after the fact.
"""

from __future__ import annotations

import tomllib
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

# Every model runs through LiteLLM, so the model string carries the provider:
# "anthropic/claude-opus-4-8", "openai/gpt-4o-mini", "gemini/gemini-1.5-pro",
# "azure/<deployment>", "bedrock/...", "ollama/llama3.1", … The key lives in .env.
DEFAULT_MODEL = "anthropic/claude-opus-4-8"

Lens = Literal["relevance", "coverage", "parsimony"]


def _deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    """Merge `overlay` into `base` in place, recursing one level into nested
    dicts (config is at most table-deep). `overlay` wins on conflicts."""
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            base[key] = {**base[key], **value}
        else:
            base[key] = value
    return base


class GuardsConfig(BaseModel):
    evidence: Literal["drop", "demote", "off"] = "drop"
    coverage_claims: Literal["strip", "off"] = "strip"
    dataset_pairing: Literal["enforce", "off"] = "enforce"


class ReviewConfig(BaseModel):
    lenses: list[Lens] = Field(default_factory=list)
    reviewer_model: str | None = None


class FloorConfig(BaseModel):
    """D2 deterministic floor — operational policy only: whether the floor is
    active. *Which* sectors count as high-risk is EU AI Act domain knowledge and
    lives in the document base (knowledge/high-risk-sectors.md), not here."""

    enabled: bool = True


class RunConfig(BaseModel):
    model: str = DEFAULT_MODEL
    max_rounds: int = Field(3, ge=1, le=5)
    guards: GuardsConfig = Field(default_factory=GuardsConfig)
    review: ReviewConfig = Field(default_factory=ReviewConfig)
    floor: FloorConfig = Field(default_factory=FloorConfig)

    @property
    def effective_reviewer_model(self) -> str:
        return self.review.reviewer_model or self.model

    @staticmethod
    def _env_overrides(env: Mapping[str, str]) -> dict[str, Any]:
        """The partial config dict implied by the WIZARD_* env vars (only keys
        that are actually set). Shared by from_env and load."""
        data: dict[str, Any] = {}
        guards: dict[str, Any] = {}
        review: dict[str, Any] = {}
        floor: dict[str, Any] = {}

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
        if "WIZARD_FLOOR_ENABLED" in env:
            # pydantic coerces "true"/"false"/"1"/"0"; junk → ValidationError
            floor["enabled"] = env["WIZARD_FLOOR_ENABLED"]

        if guards:
            data["guards"] = guards
        if review:
            data["review"] = review
        if floor:
            data["floor"] = floor
        return data

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> RunConfig:
        return cls.model_validate(cls._env_overrides(env))

    @classmethod
    def from_file(cls, path: Path | str) -> RunConfig:
        """Load config from a TOML file; a missing file yields defaults. The
        file's structure mirrors the model (top-level keys + [guards]/[review]/
        [floor] tables)."""
        file_path = Path(path)
        if not file_path.is_file():
            return cls()
        with file_path.open("rb") as handle:
            return cls.model_validate(tomllib.load(handle))

    @classmethod
    def load(cls, env: Mapping[str, str], path: Path | str | None) -> RunConfig:
        """Effective config: defaults < TOML file < environment. Env vars are an
        escape hatch over the committed config file; per-run API overrides
        (with_overrides) still sit above this."""
        data: dict[str, Any] = {}
        if path is not None:
            file_path = Path(path)
            if file_path.is_file():
                with file_path.open("rb") as handle:
                    data = tomllib.load(handle)
        _deep_merge(data, cls._env_overrides(env))
        return cls.model_validate(data)

    def with_overrides(self, overrides: dict[str, Any] | None) -> RunConfig:
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
