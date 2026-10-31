"""Composition root and entry point (`python -m aisc_control_objectives.server`).

Loads the control objectives (bundled CSV, no network and no database) and
serves them, and the projects assessed against them. The model maps a system's
risks onto the objectives that mitigate them, and it is reached BAF's way (see
aisc_control_objectives.llm): BAF_LLM_PROVIDER and BAF_LLM_MODEL name it, and the provider's own
variable carries the key.

Every environment variable the service reads, with its default, is in the
Configuration table of the README.

A `.env` file in apps/control-objectives (this repository's root) is loaded if
present (simple KEY=VALUE lines), so API keys can live in a file instead of the
shell environment.
"""

from __future__ import annotations

import os
from pathlib import Path

from aisc_control_objectives import baf_llm
from aisc_control_objectives.api.app import create_app
from aisc_control_objectives.config import RunConfig
from aisc_control_objectives.control_objectives import load_control_objectives, objectives_source
from aisc_control_objectives.projectdb import ProjectDatabases
from aisc_control_objectives.projects import Projects
from aisc_control_objectives.risk_mapping import RiskMapper
from aisc_control_objectives.settings import database_url, project_database_url


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _load_dotenv() -> None:
    """Minimal .env loader (no dependency). Existing env vars win.

    Accepts `KEY=value`, an optional `export ` prefix, quoted values, and a
    trailing `# comment` on an unquoted value. Nothing else is parsed: a key
    that looks set but is not shows up only as an opaque provider error later,
    so tests pin the accepted shapes.
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


def reproducible(complete):
    """A completer that asks the model for temperature 0.0: the same card and catalogue give the same
    mapping. (The shared completer sends no temperature unless asked, since some models refuse any.)"""
    def mapping(system: str, user: str) -> str:
        return complete(system, user, temperature=0.0)
    return mapping


def _build_completer(config: RunConfig):
    """The one way to a model: a BAF wrapper, per aisc_control_objectives.llm.

    A provider that is missing its key raises here, at startup, with a message
    that names the variable, rather than on every mapping run later.
    """
    from aisc_control_objectives.llm import build_llm, completer

    return reproducible(completer(build_llm(config.provider, config.model)))


def build_app():
    _load_dotenv()

    config_file = os.environ.get(
        "CONTROL_OBJECTIVES_CONFIG_FILE", str(_repo_root() / "control-objectives.toml")
    )
    config = RunConfig.load(os.environ, config_file)

    objectives_file, source_name = objectives_source(os.environ)
    objectives = load_control_objectives(objectives_file)

    cors_env = os.environ.get("CONTROL_OBJECTIVES_CORS_ORIGINS", "").strip()
    cors_origins = [o.strip() for o in cors_env.split(",") if o.strip()] or None
    complete = _build_completer(config)
    startup = baf_llm.config_from_env(os.environ, config.provider, config.model)

    def mapper_for(pid: str):
        """The mapper for one project: the model it chose on the platform, else the
        startup one. Asked at every map, so a changed choice applies to the next."""
        chosen = baf_llm.config_for(pid, "risk_mapper", fallback=startup)
        llm = baf_llm.build_llm(chosen, agent_name="control_objectives_llm")
        return (RiskMapper(complete=reproducible(baf_llm.completer(llm)), catalogue=objectives),
                f"{chosen.provider}/{chosen.model}")

    # Each project's assessments are in its own database, opened per request
    # by the gate after membership is decided; nothing connects here.
    databases = ProjectDatabases(database_url(), project_database_url())
    projects = Projects(
        repository=None,
        catalogue=objectives,
        mapper=RiskMapper(complete=complete, catalogue=objectives),
        model=f"{config.provider}/{config.model}",
        mapper_for=mapper_for,
    )
    return create_app(
        objectives,
        projects,
        base_config=config,
        root_path=os.environ.get("CONTROL_OBJECTIVES_ROOT_PATH", ""),
        cors_origins=cors_origins,
        source_name=source_name,
        # Who is in a project, read from the platform database, and the door
        # to each project's own database.
        engine=databases.platform,
        databases=databases,
    )


def main() -> None:
    import uvicorn

    app = build_app()
    port = int(os.environ.get("CONTROL_OBJECTIVES_PORT", "8090"))
    uvicorn.run(app, host="0.0.0.0", port=port)


if __name__ == "__main__":
    main()
