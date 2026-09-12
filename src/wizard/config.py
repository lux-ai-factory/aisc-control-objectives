"""Run configuration.

Every operator-owned knob lives here. Precedence: environment > TOML file >
defaults, resolved once at startup by `RunConfig.load`.

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

# Every model runs through LiteLLM, so the model string carries the provider:
# "anthropic/claude-opus-4-8", "openai/gpt-4o-mini", "gemini/gemini-1.5-pro",
# "azure/<deployment>", "bedrock/...", "ollama/llama3.1", … The key lives in .env.
DEFAULT_MODEL = "anthropic/claude-opus-4-8"


class RunConfig(BaseModel):
    model: str = DEFAULT_MODEL

    @staticmethod
    def _env_overrides(env: Mapping[str, str]) -> dict[str, Any]:
        """The partial config implied by the WIZARD_* env vars (set keys only)."""
        data: dict[str, Any] = {}
        if "WIZARD_MODEL" in env:
            data["model"] = env["WIZARD_MODEL"]
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
