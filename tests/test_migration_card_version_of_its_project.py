"""Alembic revision 7c3e5a9b1d24: an assessment's version belongs to its project.

(system_id, project_id) references core.system (pid, project_id), so an
assessment cannot name project A and a card version of project B. The key needs
core.system to be unique on (pid, project_id), which only the platform can make;
where it is not there yet the revision leaves the key out, and the platform's
init/project-databases.sql adds it under the same name on its next run.

Runs the real migrations on a scratch database of its own.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

ROOT = Path(__file__).resolve().parents[1]
BEFORE = "4d2a9c1e7b60"
AFTER = "7c3e5a9b1d24"
KEY = "fk_project_system_id_project_id_core_system"

CORE_DDL = """
CREATE SCHEMA IF NOT EXISTS core;
CREATE TABLE core.project (pid uuid PRIMARY KEY, name text NOT NULL, slug text NOT NULL UNIQUE);
CREATE TABLE core.system (
    pid uuid PRIMARY KEY,
    project_id uuid REFERENCES core.project (pid) ON DELETE CASCADE,
    number int NOT NULL
);
"""
UNIQUE = "ALTER TABLE core.system ADD CONSTRAINT system_pid_project_id_key UNIQUE (pid, project_id)"


def _migrated_db(database_url, monkeypatch, *, unique: bool):
    from alembic import command
    from alembic.config import Config

    base, name = database_url.rsplit("/", 1)
    name = f"{name}_alembic_pair"
    url = f"{base}/{name}"
    admin = create_engine(f"{base}/postgres", isolation_level="AUTOCOMMIT")
    with admin.connect() as connection:
        connection.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        connection.execute(text(f'CREATE DATABASE "{name}"'))
    engine = create_engine(url)
    with engine.begin() as connection:
        connection.execute(text(CORE_DDL))
        if unique:
            connection.execute(text(UNIQUE))

    monkeypatch.setenv("DATABASE_URL", url)
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "alembic"))
    command.upgrade(config, BEFORE)

    def upgrade(to: str = AFTER):
        command.upgrade(config, to)

    def drop():
        engine.dispose()
        with admin.connect() as connection:
            connection.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin.dispose()

    return engine, upgrade, drop


@pytest.fixture()
def with_unique(database_url, monkeypatch):
    engine, upgrade, drop = _migrated_db(database_url, monkeypatch, unique=True)
    yield engine, upgrade
    drop()


@pytest.fixture()
def without_unique(database_url, monkeypatch):
    engine, upgrade, drop = _migrated_db(database_url, monkeypatch, unique=False)
    yield engine, upgrade
    drop()


def _version(connection) -> tuple[str, str]:
    project, system = str(uuid.uuid4()), str(uuid.uuid4())
    connection.execute(text("INSERT INTO core.project (pid, name, slug) VALUES (:p, 'P', :s)"),
                       {"p": project, "s": f"p-{project[:8]}"})
    connection.execute(text("INSERT INTO core.system (pid, project_id, number) VALUES (:s, :p, 1)"),
                       {"s": system, "p": project})
    return project, system


def _assessment(connection, project: str, system: str) -> None:
    connection.execute(
        text("INSERT INTO control_objectives.project"
             " (id, project_id, system_id, name, objectives_digest, created_at, updated_at)"
             " VALUES (:id, :p, :s, 'MCAS', '', now(), now())"),
        {"id": uuid.uuid4().hex[:12], "p": project, "s": system})


def _key(connection):
    return connection.execute(
        text("SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname = :k"), {"k": KEY}
    ).scalar()


def test_the_key_is_the_pair_into_core_system(with_unique):
    engine, upgrade = with_unique
    upgrade()
    with engine.connect() as connection:
        assert _key(connection) == (
            "FOREIGN KEY (system_id, project_id) REFERENCES core.system(pid, project_id) ON DELETE CASCADE")


def test_an_assessment_of_a_version_of_another_project_is_refused(with_unique):
    engine, upgrade = with_unique
    upgrade()
    with engine.begin() as connection:
        a, a_v1 = _version(connection)
        _b, b_v1 = _version(connection)
        _assessment(connection, a, a_v1)
    with pytest.raises(IntegrityError, match=KEY):
        with engine.begin() as connection:
            _assessment(connection, a, b_v1)


def test_existing_assessments_that_break_the_rule_stop_the_upgrade(with_unique):
    engine, upgrade = with_unique
    with engine.begin() as connection:
        a, _a_v1 = _version(connection)
        _b, b_v1 = _version(connection)
        _assessment(connection, a, b_v1)
    with pytest.raises(Exception, match=KEY):
        upgrade()
    with engine.connect() as connection:
        assert _key(connection) is None
        assert connection.execute(text("SELECT count(*) FROM control_objectives.project")).scalar() == 1


def test_without_the_platform_unique_the_upgrade_succeeds_and_leaves_the_key_out(without_unique):
    engine, upgrade = without_unique
    upgrade()
    with engine.connect() as connection:
        assert _key(connection) is None
        assert connection.execute(text("SELECT version_num FROM control_objectives.alembic_version")).scalar() == AFTER


def test_the_revision_follows_4d2a9c1e7b60_and_is_the_head():
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "alembic"))
    script = ScriptDirectory.from_config(config)
    assert script.get_heads() == [AFTER]
    assert script.get_revision(AFTER).down_revision == BEFORE


def test_the_model_has_the_same_key(repository, platform_project, system_version):
    """create_all (the suite's schema) and the migration agree."""
    from sqlalchemy.exc import IntegrityError as Refused

    other = str(uuid.uuid4())
    with repository._engine.begin() as connection:
        connection.execute(text("INSERT INTO core.project (pid, name, slug) VALUES (:p, 'O', :s)"),
                           {"p": other, "s": f"o-{other[:8]}"})
    theirs = system_version(other, 1)
    with pytest.raises(Refused, match=KEY):
        with repository._engine.begin() as connection:
            _assessment(connection, platform_project, theirs)
