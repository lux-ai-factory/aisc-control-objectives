"""Where the database is.

`DATABASE_URL` is the same variable the qualification app reads, so a
deployment configures both services alike. The driver is named explicitly
(`postgresql+psycopg`) because SQLAlchemy still defaults `postgresql://` to
psycopg2, which is not what is installed.
"""

from __future__ import annotations

import os

DEFAULT_URL = "postgresql+psycopg://aisc-postgres-user:dev-password@localhost:5432/wizard"


def database_url(env: dict | None = None) -> str:
    """The database to use, with the driver SQLAlchemy needs."""
    url = (env or os.environ).get("DATABASE_URL") or DEFAULT_URL
    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+psycopg://", 1)
    # Prisma-style query strings (?schema=public) mean nothing to SQLAlchemy.
    return url.split("?", 1)[0]
