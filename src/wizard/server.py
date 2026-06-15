"""Composition root + runnable entrypoint (SPEC §0, §7).

Wires the LLM client and live catalogue data into the FastAPI app, then serves
it. Strictly additive — reads the catalogue over its existing HTTP API and never
writes to it.

All models run through LiteLLM (`wizard.llm_client.LiteLLMClient`), so the model
string carries the provider (e.g. "anthropic/claude-opus-4-8", "openai/gpt-4o-mini").
The model is chosen in a TOML config file (default `wizard.toml` at the repo
root, override with WIZARD_CONFIG_FILE); env vars override the file. The provider
API key lives in `.env` and LiteLLM reads the one matching the model's provider.

Env:
  WIZARD_CONFIG_FILE    path to the TOML config (default <repo>/wizard.toml)
  ANTHROPIC_API_KEY / OPENAI_API_KEY / …  provider key (read by LiteLLM per model)
  WIZARD_CATALOGUE_URL  catalogue backend base URL (default http://localhost:8001)
  WIZARD_PORT           port to serve on (default 8090)
  WIZARD_SKILLS_DIR     skills directory (default <repo>/skills)
  WIZARD_KNOWLEDGE_DIR  reference-document base (default <repo>/knowledge)
  WIZARD_MODEL etc.     RunConfig overrides (see wizard.config)

A `.env` file next to the repo root is loaded if present (simple KEY=VALUE
lines), so API keys can live in a file instead of the shell environment.
"""

from __future__ import annotations

import os
from pathlib import Path

from wizard.api.app import create_app
from wizard.clients.catalogue_http import load_catalogue
from wizard.config import RunConfig


def _load_dotenv() -> None:
    """Minimal .env loader (no dependency). Existing env vars win."""
    env_path = Path(__file__).resolve().parents[2] / ".env"
    if not env_path.is_file():
        return
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


class _UploadOnlyQualificationProvider:
    """Stub provider for the upload-driven flow: the wizard receives system
    cards via POST /api/plans/from-card, so it never fetches them by id."""

    def list_qualifications(self) -> list[dict]:
        return []

    def get_system_card(self, qualification_id: str):
        return None


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


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
    base = RunConfig.load(os.environ, config_file)
    client = _build_client()

    catalogue_url = os.environ.get("WIZARD_CATALOGUE_URL", "http://localhost:8001")
    tools, checklists = load_catalogue(catalogue_url)

    from wizard.agents.runner import WizardPlanRunner
    from wizard.knowledge import load_high_risk_sectors
    from wizard.skills import SkillsLoader

    # Skills are an optional knowledge channel (Phase 2); the loader is empty
    # (and the pipeline behaves as unskilled) until files are dropped here.
    skills_dir = os.environ.get("WIZARD_SKILLS_DIR", str(_repo_root() / "skills"))
    skills = SkillsLoader.from_dir(skills_dir)
    # The Annex III high-risk sectors (D2 floor) are domain knowledge loaded
    # from the document base, not operator config.
    knowledge_dir = os.environ.get(
        "WIZARD_KNOWLEDGE_DIR", str(_repo_root() / "knowledge")
    )
    high_risk_sectors = load_high_risk_sectors(knowledge_dir)
    runner = WizardPlanRunner(
        client=client,
        tools=tools,
        checklists=checklists,
        config=base,
        skills=skills,
        high_risk_sectors=high_risk_sectors,
    )
    # Behind a reverse proxy the service may be mounted under a sub-path
    # (e.g. Caddy `/wizard*`); WIZARD_ROOT_PATH wires FastAPI's root_path.
    root_path = os.environ.get("WIZARD_ROOT_PATH", "")
    # The active recommendation is persisted here so it survives a restart (the
    # catalogue's "Recommended" filter then doesn't go blank). Postgres later.
    active_state_file = os.environ.get(
        "WIZARD_STATE_FILE", str(_repo_root() / ".wizard_state.json")
    )
    # CORS defaults to permissive for dev; tighten per deploy with a
    # comma-separated allowlist (e.g. WIZARD_CORS_ORIGINS=https://catalogue.example).
    cors_env = os.environ.get("WIZARD_CORS_ORIGINS", "").strip()
    cors_origins = [o.strip() for o in cors_env.split(",") if o.strip()] or None
    return create_app(
        _UploadOnlyQualificationProvider(),
        runner,
        base_config=base,
        root_path=root_path,
        active_state_file=active_state_file,
        cors_origins=cors_origins,
    )


def main() -> None:
    import uvicorn

    app = build_app()
    port = int(os.environ.get("WIZARD_PORT", "8090"))
    uvicorn.run(app, host="0.0.0.0", port=port)


if __name__ == "__main__":
    main()
