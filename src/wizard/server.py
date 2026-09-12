"""Composition root + runnable entrypoint.

Loads the control objectives (bundled CSV, no network and no database) and
serves them: an HTML page at the root, plus the JSON API. The LLM client
drives the profile extractor (the model proposes the three applicability facts
from an uploaded card); every model runs through LiteLLM, so the model string carries the provider
(e.g. "anthropic/claude-opus-4-8", "openai/gpt-4o-mini") and LiteLLM reads the
matching key from the environment.

Env:
  WIZARD_CONFIG_FILE       path to the TOML config (default <repo>/wizard.toml)
  WIZARD_OBJECTIVES_FILE   override the bundled objectives CSV
  ANTHROPIC_API_KEY / OPENAI_API_KEY / …  provider key (read by LiteLLM per model)
  WIZARD_PORT              port to serve on (default 8090)
  WIZARD_ROOT_PATH         sub-path when behind a reverse proxy
  WIZARD_CORS_ORIGINS      comma-separated allowlist (default permissive)
  WIZARD_MODEL             RunConfig override (see wizard.config)

A `.env` file next to the repo root is loaded if present (simple KEY=VALUE
lines), so API keys can live in a file instead of the shell environment.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

from wizard.api.app import create_app
from wizard.config import RunConfig
from wizard.control_objectives import default_csv_path, load_control_objectives
from wizard.profiling import ProfileExtractor


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def objectives_source(env: Mapping[str, str]) -> tuple[Path | None, str]:
    """(override path or None, the file name the page shows). A blank
    WIZARD_OBJECTIVES_FILE, as templated env files leave it, means the bundled CSV."""
    override = (env.get("WIZARD_OBJECTIVES_FILE") or "").strip()
    if not override:
        return None, default_csv_path().name
    return Path(override), Path(override).name


def _load_dotenv() -> None:
    """Minimal .env loader (no dependency). Existing env vars win."""
    env_path = _repo_root() / ".env"
    if not env_path.is_file():
        return
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def _build_client():
    """The single LLM client: LiteLLM. The provider is selected by the model
    string (e.g. "anthropic/…", "openai/…") and LiteLLM reads the matching key
    from the environment (.env)."""
    from wizard.llm_client import LiteLLMClient

    return LiteLLMClient()


def build_app():
    _load_dotenv()

    config_file = os.environ.get(
        "WIZARD_CONFIG_FILE", str(_repo_root() / "wizard.toml")
    )
    config = RunConfig.load(os.environ, config_file)

    objectives_file, source_name = objectives_source(os.environ)
    objectives = load_control_objectives(objectives_file)

    cors_env = os.environ.get("WIZARD_CORS_ORIGINS", "").strip()
    cors_origins = [o.strip() for o in cors_env.split(",") if o.strip()] or None
    # The model proposes the applicability profile; LiteLLM reads the provider
    # key at call time, and a missing key surfaces as a failed run on the card
    # page rather than a crash here.
    extractor = ProfileExtractor(client=_build_client(), model=config.model)
    return create_app(
        objectives,
        base_config=config,
        root_path=os.environ.get("WIZARD_ROOT_PATH", ""),
        cors_origins=cors_origins,
        source_name=source_name,
        extractor=extractor,
    )


def main() -> None:
    import uvicorn

    app = build_app()
    port = int(os.environ.get("WIZARD_PORT", "8090"))
    uvicorn.run(app, host="0.0.0.0", port=port)


if __name__ == "__main__":
    main()
