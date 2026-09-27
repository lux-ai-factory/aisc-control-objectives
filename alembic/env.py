"""Alembic's entry point.

The URL comes from the environment, never the file, so the same migrations run
against dev, the platform and a test database.

There is one database, and this service owns a schema in it. Migrations run
with `search_path` set to that schema, so an unqualified `op.create_table`
lands in `control_objectives` whatever role is connected, and the version
table lives there too. `core` is on the path because this service reads it and
points at it; it never creates anything there, which is also why external
tables are excluded below.

The schema is normally made by the platform's own init, and the service's role
is deliberately not allowed to create schemas in the database. So it is made
here only when it is missing AND the connected role may: on the platform it
already exists, and in a scratch database (a test, a laptop) the owning role
makes it.
"""

from alembic import context
from sqlalchemy import create_engine, text

from aisc_control_objectives.db.tables import SCHEMA, Base
from aisc_control_objectives.settings import database_url

target_metadata = Base.metadata


def _include_object(obj, name, type_, reflected, compare_to) -> bool:
    """Tables another service owns are read, never migrated."""
    if type_ == "table" and obj.info.get("external"):
        return False
    return True


def run_migrations_online() -> None:
    engine = create_engine(database_url())
    with engine.connect() as connection:
        exists = connection.execute(
            text("SELECT to_regnamespace(:s) IS NOT NULL"), {"s": SCHEMA}
        ).scalar()
        if not exists:
            connection.execute(text(f"CREATE SCHEMA {SCHEMA}"))
        connection.execute(text(f"SET search_path TO {SCHEMA}, core"))
        connection.commit()
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            version_table_schema=SCHEMA,
            include_schemas=True,
            include_object=_include_object,
        )
        with context.begin_transaction():
            context.run_migrations()


run_migrations_online()
