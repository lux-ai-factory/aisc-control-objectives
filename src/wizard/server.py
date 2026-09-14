"""Composition root + runnable entrypoint.

Loads the control objectives (bundled CSV, no network and no database) and
serves them: an HTML page at the root, plus the JSON API. The model proposes
the three applicability facts from an uploaded card, and it is reached BAF's
way (see wizard.llm): BAF_LLM_PROVIDER and BAF_LLM_MODEL name it, and the
provider's own variable carries the key.

Env:
  WIZARD_CONFIG_FILE       path to the TOML config (default <repo>/wizard.toml)
  WIZARD_OBJECTIVES_FILE   override the bundled objectives CSV
  BAF_LLM_PROVIDER / BAF_LLM_MODEL        which model, BAF's names
  BAF_LLM_BASE_URL         endpoint for the ollama / compatible providers
  MISTRAL_API_KEY / OPENAI_API_KEY / …    the provider's own key
  WIZARD_PORT              port to serve on (default 8090)
  WIZARD_ROOT_PATH         sub-path when behind a reverse proxy
  WIZARD_CORS_ORIGINS      comma-separated allowlist (default permissive)

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
    """Minimal .env loader (no dependency). Existing env vars win.

    Accepts `KEY=value`, an optional `export ` prefix, quoted values, and a
    trailing `# comment` on an unquoted value. Anything else is not supported:
    a key that looks set and is not surfaces only as an opaque provider error
    on a card page, so the accepted shapes are pinned by tests.
    """
    env_path = _repo_root() / ".env"
    if not env_path.is_file():
        return
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export "):]
        key, _, value = line.partition("=")
        value = value.strip()
        if value[:1] in {'"', "'"}:
            closing = value.find(value[0], 1)
            value = value[1:closing] if closing > 0 else value[1:]
        else:
            value = value.split(" #", 1)[0].strip()
        os.environ.setdefault(key.strip(), value)


def _build_completer(config: RunConfig):
    """The one way to a model: a BAF wrapper, per wizard.llm.

    A provider that is missing its key raises here, at startup, where the
    message says which variable; the alternative is every upload failing with
    a provider error on the card page.
    """
    from wizard.llm import build_llm, completer

    return completer(build_llm(config.provider, config.model))


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
    extractor = ProfileExtractor(complete=_build_completer(config))
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
