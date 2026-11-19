"""Where the databases are.

Two variables:

- `DATABASE_URL` is the `platform` database, read for membership only
  (`core.project`, `core.project_member`).
- `PROJECT_DATABASE_URL` is the template of a project's own database, with
  `{database}` where `project_<hex>` goes. Unset, it is `DATABASE_URL` with its
  database replaced by `{database}` (same server, same role).
- `PROJECT_MIGRATE_DATABASE_URL` is the same template for the migrations, as
  control_objectives_migrator, whose sessions act as control_objectives_owner, the
  owner of the schema's tables. Required by the service and the migrate one-shot:
  the role the service queries as reads and writes rows, it never migrates.

The driver is named explicitly (`postgresql+psycopg`) because SQLAlchemy still
defaults `postgresql://` to psycopg2, which is not what is installed.
"""

from __future__ import annotations

import os

#: The platform database, as this service's own role.
DEFAULT_URL = (
    "postgresql+psycopg://control_objectives_rw:control_objectives_rw"
    "@localhost:5432/platform"
)


def _normalised(url: str) -> str:
    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+psycopg://", 1)
    # Prisma-style query strings (?schema=public) mean nothing to SQLAlchemy.
    return url.split("?", 1)[0]


def database_url(env: dict | None = None) -> str:
    """The platform database (membership), with the driver SQLAlchemy needs."""
    return _normalised((env or os.environ).get("DATABASE_URL") or DEFAULT_URL)


def project_database_url(env: dict | None = None) -> str:
    """The template of a project database's URL, `{database}` in place of its name."""
    env = env if env is not None else os.environ
    template = env.get("PROJECT_DATABASE_URL")
    if template:
        template = _normalised(template)
    else:
        template = database_url(env).rsplit("/", 1)[0] + "/{database}"
    if "{database}" not in template:
        raise ValueError("PROJECT_DATABASE_URL must contain {database}")
    return template


def project_migrate_database_url(env: dict | None = None) -> str:
    """The template of a project database's URL for the migrations (control_objectives_migrator)."""
    env = env if env is not None else os.environ
    template = env.get("PROJECT_MIGRATE_DATABASE_URL") or ""
    if "{database}" not in template:
        raise ValueError("PROJECT_MIGRATE_DATABASE_URL must contain {database}: migrations run as"
                         " control_objectives_migrator")
    return _normalised(template)


def ai_actions_on(env: dict | None = None) -> bool:
    """Whether this install offers "Suggest with AI": only when AISC_AI_ACTIONS is `on`. Off by default, so
    an install makes no model call from this service until someone turns it on."""
    env = env if env is not None else os.environ
    return (env.get("AISC_AI_ACTIONS") or "").strip().lower() == "on"
