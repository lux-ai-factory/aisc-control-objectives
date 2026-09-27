"""Where the databases are.

Two variables (isolation 2026-09-25, 01-specs.md I5.1, S-D6):

- `DATABASE_URL` is the `platform` database, read for membership only
  (`core.project`, `core.project_member`).
- `PROJECT_DATABASE_URL` is the template of a project's own database, with
  `{database}` where `project_<hex>` goes. Unset, it is `DATABASE_URL` with its
  database replaced by `{database}` (same server, same role).

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
