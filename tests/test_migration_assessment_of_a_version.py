"""WP7 data: Alembic revision 4d2a9c1e7b60, "an assessment is of one system version".

Runs the real migrations on a scratch database of its own: up to 3b91d0e7a52c
(the live head), rows in the live shape, then up to 4d2a9c1e7b60. Rule ids
refer to docs/superpowers/pipeline-2026-09-23/03-specs.md, WP7 "Data".
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

ROOT = Path(__file__).resolve().parents[1]
BEFORE = "3b91d0e7a52c"
AFTER = "4d2a9c1e7b60"

CORE_DDL = """
CREATE SCHEMA IF NOT EXISTS core;
CREATE TABLE core.project (pid uuid PRIMARY KEY, name text NOT NULL, slug text NOT NULL UNIQUE);
CREATE TABLE core.system (
    pid uuid PRIMARY KEY,
    project_id uuid REFERENCES core.project (pid) ON DELETE CASCADE,
    number int NOT NULL
);
"""


@pytest.fixture()
def migrated_db(database_url, monkeypatch):
    """A scratch database at the live head, and a function to migrate it."""
    from alembic import command
    from alembic.config import Config

    base, name = database_url.rsplit("/", 1)
    name = f"{name}_alembic"
    url = f"{base}/{name}"
    admin = create_engine(f"{base}/postgres", isolation_level="AUTOCOMMIT")
    with admin.connect() as connection:
        connection.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        connection.execute(text(f'CREATE DATABASE "{name}"'))
    engine = create_engine(url)
    with engine.begin() as connection:
        connection.execute(text(CORE_DDL))

    monkeypatch.setenv("DATABASE_URL", url)
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "alembic"))
    command.upgrade(config, BEFORE)

    def upgrade(to: str = AFTER):
        command.upgrade(config, to)

    yield engine, upgrade
    engine.dispose()
    with admin.connect() as connection:
        connection.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
    admin.dispose()


def _script():
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "alembic"))
    return ScriptDirectory.from_config(config)


def _require_the_revision() -> None:
    """A refusal only counts if it is the migration refusing, not Alembic
    failing to find it."""
    assert AFTER in {rev.revision for rev in _script().walk_revisions()}, (
        f"revision {AFTER} does not exist yet"
    )


def _project(connection, versions: int) -> tuple[str, list[str]]:
    pid = str(uuid.uuid4())
    connection.execute(
        text("INSERT INTO core.project (pid, name, slug) VALUES (:p, 'MCAS', :s)"),
        {"p": pid, "s": f"p-{pid[:8]}"},
    )
    pids = []
    for number in range(1, versions + 1):
        spid = str(uuid.uuid4())
        connection.execute(
            text("INSERT INTO core.system (pid, project_id, number) VALUES (:s, :p, :n)"),
            {"s": spid, "p": pid, "n": number},
        )
        pids.append(spid)
    return pid, pids


def _assessment(connection, project: str) -> str:
    aid = uuid.uuid4().hex[:12]
    connection.execute(
        text(
            "INSERT INTO control_objectives.project"
            " (id, project_id, name, system_name, qualification_id, objectives_digest,"
            "  created_at, updated_at)"
            " VALUES (:id, :p, 'MCAS', 'MicroCredit', 'q1', '', now(), now())"
        ),
        {"id": aid, "p": project},
    )
    return aid


def _columns(connection) -> dict[str, str]:
    return dict(
        connection.execute(
            text(
                "SELECT column_name, is_nullable FROM information_schema.columns"
                " WHERE table_schema = 'control_objectives' AND table_name = 'project'"
            )
        ).all()
    )


def test_s7_data_backfills_the_latest_version_and_drops_the_old_link(migrated_db):
    """# S7 data steps 1, 2, 4, 5: system_id = the project's highest-numbered version."""
    engine, upgrade = migrated_db
    with engine.begin() as connection:
        project, (v1, v2) = _project(connection, 2)
        aid = _assessment(connection, project)

    upgrade()

    with engine.connect() as connection:
        columns = _columns(connection)
        assert columns.get("system_id") == "NO"
        assert "system_name" not in columns
        assert "qualification_id" not in columns
        assert connection.execute(
            text("SELECT system_id::text FROM control_objectives.project WHERE id = :id"),
            {"id": aid},
        ).scalar() == v2
        constraints = dict(
            connection.execute(
                text(
                    "SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint"
                    " WHERE conrelid = 'control_objectives.project'::regclass"
                )
            ).all()
        )
        assert constraints["uq_project_system_id"] == "UNIQUE (system_id)"
        assert "REFERENCES core.system(pid) ON DELETE CASCADE" in (
            constraints["fk_project_system_id_core_system"]
        )
        indexes = {
            row[0]
            for row in connection.execute(
                text("SELECT indexname FROM pg_indexes WHERE schemaname = 'control_objectives'"
                     " AND tablename = 'project'")
            )
        }
        assert not any("qualification_id" in name for name in indexes)


def test_s7_data_refuses_an_assessment_with_no_version(migrated_db):
    """# S7 data step 3: RAISE if a row stays NULL; assessments are never deleted."""
    engine, upgrade = migrated_db
    with engine.begin() as connection:
        project, _ = _project(connection, 0)
        _assessment(connection, project)

    _require_the_revision()
    with pytest.raises(Exception):
        upgrade()
    with engine.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM control_objectives.project")).scalar() == 1
        assert "system_id" not in _columns(connection)


def test_s7_data_refuses_two_assessments_on_one_version(migrated_db):
    """# S7 data step 3: RAISE if two rows get the same system_id."""
    engine, upgrade = migrated_db
    with engine.begin() as connection:
        project, _ = _project(connection, 1)
        _assessment(connection, project)
        _assessment(connection, project)

    _require_the_revision()
    with pytest.raises(Exception):
        upgrade()
    with engine.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM control_objectives.project")).scalar() == 2


def test_s7_6_after_the_migration_deleting_the_version_deletes_the_assessment(migrated_db):
    """# S7.6 at the schema level."""
    engine, upgrade = migrated_db
    with engine.begin() as connection:
        project, (v1,) = _project(connection, 1)
        _assessment(connection, project)

    upgrade()

    with engine.begin() as connection:
        connection.execute(text("DELETE FROM core.system WHERE pid = :p"), {"p": v1})
    with engine.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM control_objectives.project")).scalar() == 0


def test_the_revision_follows_the_live_head(migrated_db):
    """WP7 data: revision 4d2a9c1e7b60, down_revision 3b91d0e7a52c, and the head is it or
    comes after it (7c3e5a9b1d24 follows it)."""
    script = _script()
    (head,) = script.get_heads()
    assert AFTER in {rev.revision for rev in script.iterate_revisions(head, "base")}
    assert script.get_revision(AFTER).down_revision == BEFORE
