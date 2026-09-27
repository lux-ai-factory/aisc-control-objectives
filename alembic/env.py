"""Alembic's entry point: one project database at a time.

Each project has a database of its own, and this service owns the schema
`control_objectives` in it (the platform's template 0008 makes the schema and
lets this service's role create in it). The migrations run there and nowhere
else:

- normally from `aisc_control_objectives.projectdb.migrate(engine)`, which hands
  over a connection in a transaction it owns (`config.attributes["connection"]`;
  the app on first open of a database, and
  `python -m aisc_control_objectives.migrate_projects` for every one);
- by hand, `alembic -x url=postgresql+psycopg://.../project_<hex> upgrade head`.
  There is no default URL, and the `platform` database is refused: nothing of
  this service lives there any more.

The version table lives in the schema, and the search path is the schema alone,
so an unqualified name never resolves anywhere else. A transaction-scoped
advisory lock keeps the app and the one-shot from migrating one database at the
same time. The schema is made here only when it is missing and the connected
role may create in the database (a scratch database in a test); in a project
database without it the template has not run, and that is said plainly.
"""

from urllib.parse import urlsplit

from alembic import context
from sqlalchemy import text
from sqlalchemy.pool import NullPool

from aisc_control_objectives.db.tables import SCHEMA, Base
from aisc_control_objectives.projectdb import SchemaMissing, make_engine

target_metadata = Base.metadata

#: One lock name for every migration of this service, in whichever database.
_LOCK = "aisc_control_objectives.alembic"


def _include_object(obj, name, type_, reflected, compare_to) -> bool:
    """Tables another owner makes (project.system) are pointed at, never migrated."""
    if type_ == "table" and obj.info.get("external"):
        return False
    return True


def _migrate(connection) -> None:
    connection.execute(text("SELECT pg_advisory_xact_lock(hashtext(:k))"), {"k": _LOCK})
    missing = connection.execute(
        text("SELECT to_regnamespace(:s) IS NULL"), {"s": SCHEMA}
    ).scalar()
    if missing:
        may_create = connection.execute(
            text("SELECT has_database_privilege(current_user, current_database(), 'CREATE')")
        ).scalar()
        if not may_create:
            raise SchemaMissing(
                f"schema {SCHEMA} is missing: platform template 0008 not applied"
            )
        connection.execute(text(f"CREATE SCHEMA {SCHEMA}"))
    connection.execute(text(f"SET search_path TO {SCHEMA}"))
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        version_table_schema=SCHEMA,
        include_schemas=True,
        include_object=_include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    given = context.config.attributes.get("connection")
    if given is not None:
        # the caller owns the transaction and commits it
        _migrate(given)
        return
    url = context.get_x_argument(as_dictionary=True).get("url")
    if not url:
        raise SystemExit(
            "runs per project database: python -m aisc_control_objectives.migrate_projects"
            " (or alembic -x url=<a project database> upgrade head)"
        )
    if urlsplit(url).path.rsplit("/", 1)[-1] == "platform":
        raise SystemExit("refusing the platform database: control objectives live in project databases")
    engine = make_engine(url, poolclass=NullPool)
    try:
        with engine.begin() as connection:
            _migrate(connection)
    finally:
        engine.dispose()


run_migrations_online()
