"""The alembic baseline of a project database, 20260926000000_project_database.

Isolation (01-specs.md I5.4, S-D13): the old chain (six revisions ending at
7c3e5a9b1d24, with keys into the platform's core.system) is replaced by one
baseline that makes today's final shape minus `project_id`, with `system_id`
pointing at `project.system(pid)` ON DELETE CASCADE. This file replaces
test_migration_assessment_of_a_version.py and
test_migration_card_version_of_its_project.py: it keeps what of theirs still
holds in a project database (the head, the cascade from a deleted version, and
create_all agreeing with the migration).

Runs the real migration on a scratch database of its own, derived from the
suite's test database and refused by the same rule (conftest.refused_database).
The platform template's `project.system` (0006) is stood in for by the suite's DDL.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import create_engine, text

from conftest import CORE_PROJECT_DDL, refused_database

BASELINE = "20260926000000_project_database"
TABLES = ("project", "graph", "risk", "mapped_objective", "mapping_run")


def _scratch(database_url: str, suffix: str):
    base, name = database_url.rsplit("/", 1)
    name = f"{name}_{suffix}"
    url = f"{base}/{name}"
    why = refused_database(url)
    if why is not None:
        pytest.fail(why, pytrace=False)
    admin = create_engine(f"{base}/postgres", isolation_level="AUTOCOMMIT")
    with admin.connect() as connection:
        connection.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        connection.execute(text(f'CREATE DATABASE "{name}"'))
    engine = create_engine(url)
    with engine.begin() as connection:
        connection.execute(text(CORE_PROJECT_DDL))

    def drop():
        engine.dispose()
        with admin.connect() as connection:
            connection.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin.dispose()

    return engine, drop


@pytest.fixture()
def migrated(database_url):
    """A scratch project database upgraded to head by the service's own migrate()."""
    from aisc_control_objectives import projectdb

    engine, drop = _scratch(database_url, "alembic_baseline")
    projectdb.migrate(engine)
    yield engine
    drop()


def _constraints(connection) -> set[tuple[str, str, str]]:
    # names print qualified or not by the search path; read them one way
    connection.execute(text("SET search_path TO pg_catalog"))
    return {
        tuple(row)
        for row in connection.execute(text(
            "SELECT t.relname, c.conname, pg_get_constraintdef(c.oid, true)"
            "  FROM pg_constraint c JOIN pg_class t ON t.oid = c.conrelid"
            "  JOIN pg_namespace n ON n.oid = t.relnamespace"
            " WHERE n.nspname = 'control_objectives' AND t.relname <> 'alembic_version'"
        ))
    }


def _indexes(connection) -> set[tuple[str, str]]:
    # names print qualified or not by the search path; read them one way
    connection.execute(text("SET search_path TO pg_catalog"))
    return {
        tuple(row)
        for row in connection.execute(text(
            "SELECT indexname, indexdef FROM pg_indexes WHERE schemaname = 'control_objectives'"
            "   AND tablename <> 'alembic_version'"
        ))
    }


def _columns(connection) -> list[tuple]:
    # names print qualified or not by the search path; read them one way
    connection.execute(text("SET search_path TO pg_catalog"))
    return [
        tuple(row)
        for row in connection.execute(text(
            "SELECT table_name, column_name, data_type, is_nullable, column_default"
            "  FROM information_schema.columns WHERE table_schema = 'control_objectives'"
            "   AND table_name <> 'alembic_version' ORDER BY table_name, ordinal_position"
        ))
    ]


def test_upgrading_to_head_gives_the_baseline_and_the_tables(migrated):
    with migrated.connect() as connection:
        assert connection.execute(
            text("SELECT version_num FROM control_objectives.alembic_version")
        ).scalars().all() == [BASELINE]
        tables = set(connection.execute(text(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'control_objectives'"
        )).scalars())
    assert tables == set(TABLES) | {"alembic_version"}


def test_the_baseline_is_the_only_head():
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    from aisc_control_objectives import projectdb

    config = Config(str(projectdb.ALEMBIC_INI))
    config.set_main_option("script_location", str(projectdb.ALEMBIC_DIR))
    script = ScriptDirectory.from_config(config)
    assert script.get_heads() == [BASELINE]
    assert script.get_revision(BASELINE).down_revision is None


def test_upgrading_again_changes_nothing(migrated):
    from aisc_control_objectives import projectdb

    with migrated.connect() as connection:
        before = (_constraints(connection), _indexes(connection), _columns(connection))
    projectdb.migrate(migrated)
    with migrated.connect() as connection:
        assert (_constraints(connection), _indexes(connection), _columns(connection)) == before
        assert connection.execute(
            text("SELECT count(*) FROM control_objectives.alembic_version")
        ).scalar() == 1


def test_deleting_a_version_deletes_its_assessment(migrated):
    """(From the old S7.6 schema test: the key into project.system cascades.)"""
    version = str(uuid.uuid4())
    with migrated.begin() as connection:
        connection.execute(
            text("INSERT INTO project.system (pid, number, name) VALUES (:p, 1, 'MCAS')"),
            {"p": version},
        )
        connection.execute(text(
            "INSERT INTO control_objectives.project"
            " (id, name, objectives_digest, created_at, updated_at, system_id)"
            " VALUES ('a1', 'MCAS', '', now(), now(), :s)"), {"s": version})
        connection.execute(text(
            "INSERT INTO control_objectives.risk (project_id, risk_id, position, text, short_label,"
            " source, vulnerability, consequence, impact, stakeholder, control, follow_up_control,"
            " areas, vair_terms, provenance) VALUES ('a1', 'risk0', 0, 't', '', '', '', '', '', '',"
            " '', '', '{}', '{}', 'form')"))
    with migrated.begin() as connection:
        connection.execute(text("DELETE FROM project.system WHERE pid = :p"), {"p": version})
    with migrated.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM control_objectives.project")).scalar() == 0
        assert connection.execute(text("SELECT count(*) FROM control_objectives.risk")).scalar() == 0


def test_the_assessment_has_one_key_into_project_system_and_no_project_id(migrated):
    with migrated.connect() as connection:
        keys = connection.execute(text(
            "SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint"
            " WHERE conrelid = 'control_objectives.project'::regclass AND contype = 'f'"
        )).all()
        columns = [c for (t, c, *_rest) in _columns(connection) if t == "project"]
    assert [tuple(k) for k in keys] == [(
        "fk_project_system_id_project_system",
        "FOREIGN KEY (system_id) REFERENCES project.system(pid) ON DELETE CASCADE",
    )]
    assert columns == ["id", "name", "objectives_digest", "created_at", "updated_at", "system_id"]


def test_the_model_and_the_migration_agree(migrated, repository):
    """create_all (the suite's schema) and the baseline give the same keys,
    indexes and columns (from the old test_the_model_has_the_same_key)."""
    with migrated.connect() as connection:
        from_migration = (_constraints(connection), _indexes(connection))
    with repository.engine.connect() as connection:
        from_model = (_constraints(connection), _indexes(connection))
    assert from_model[0] == from_migration[0]
    # create_all names its indexes ix_control_objectives_<table>_<column> (as it
    # always has); the baseline keeps the live names. Same indexes either way.
    def unnamed(indexes):
        return {definition.replace(f" {name} ON ", " ON ") for name, definition in indexes}

    assert unnamed(from_model[1]) == unnamed(from_migration[1])
