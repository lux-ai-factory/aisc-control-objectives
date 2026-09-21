"""Run configuration.

Every operator-owned knob lives here. Precedence: environment > TOML file >
defaults, resolved once at startup by `RunConfig.load`. The model is named the
way BAF names it, because BAF is what reaches the model.

The knobs of the retired recommendation pipeline (review rounds, guards, the
high-risk floor) are gone with it; what is left is the model the service talks
to. New knobs land here as the card-to-objectives mapping is specified.
"""

from __future__ import annotations

import tomllib
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from aisc_control_objectives.llm import DEFAULT_MODEL, DEFAULT_PROVIDER


# The model is named BAF's way: a provider and a model name, the same two
# variables the qualification filler reads, so a deployment configures both
# services alike. aisc_control_objectives.llm.PROVIDERS is the list of providers.
class RunConfig(BaseModel):
    provider: str = DEFAULT_PROVIDER
    model: str = DEFAULT_MODEL

    @staticmethod
    def _env_overrides(env: Mapping[str, str]) -> dict[str, Any]:
        """The partial config implied by BAF's env vars (set keys only)."""
        data: dict[str, Any] = {}
        if env.get("BAF_LLM_PROVIDER"):
            data["provider"] = env["BAF_LLM_PROVIDER"]
        if env.get("BAF_LLM_MODEL"):
            data["model"] = env["BAF_LLM_MODEL"]
        return data

    @classmethod
    def load(cls, env: Mapping[str, str], path: Path | str | None) -> RunConfig:
        """Effective config: defaults < TOML file < environment. Env vars are an
        escape hatch over the committed config file."""
        data: dict[str, Any] = {}
        if path is not None:
            file_path = Path(path)
            if file_path.is_file():
                with file_path.open("rb") as handle:
                    data = tomllib.load(handle)
        data.update(cls._env_overrides(env))
        return cls.model_validate(data)
