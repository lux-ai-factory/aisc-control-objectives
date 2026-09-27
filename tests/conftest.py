"""Shared fixtures: the frozen AI Card, the catalogue, and a test database."""

from pathlib import Path

import pytest

from aisc_control_objectives.control_objectives import load_control_objectives

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="session")
def fixtures_dir() -> Path:
    return FIXTURES


@pytest.fixture(scope="session")
def mcas_graph():
    """The MCAS system's filled AIRO graph, as its qualification exports it."""
    import json

    from aisc_control_objectives.models.ontology import Ontology

    return Ontology.from_jsonld(json.loads((FIXTURES / "mcas.ontology.jsonld").read_text()))


@pytest.fixture(scope="session")
def objectives():
    return load_control_objectives()


@pytest.fixture(scope="session")
def database_url() -> str:
    """A database of its own for the suite, on the platform Postgres.

    Not SQLite: testing against a different engine from production is how you
    find out `text[]` does not exist on the day you deploy.
    """
    import os

    return os.environ.get(
        "CONTROL_OBJECTIVES_TEST_DATABASE_URL",
        "postgresql+psycopg://aisc-postgres-user:dev-password@localhost:5432/control_objectives_test",
    )


#: The one table this service reads from outside its own schema. The platform
#: owns it; the test database gets the same shape, because a project row here
#: is meaningless without the project it belongs to.
CORE_PROJECT_DDL = """
CREATE SCHEMA IF NOT EXISTS core;
CREATE TABLE IF NOT EXISTS core.project (
    pid  uuid PRIMARY KEY,
    name text NOT NULL,
    slug text NOT NULL UNIQUE
);
-- Who is in a project, and as what. Written by the platform, read by every
-- module: this service reads it to decide whether the person in front of it
-- may see this assessment at all.
CREATE TABLE IF NOT EXISTS core.project_member (
    project_id uuid NOT NULL REFERENCES core.project (pid) ON DELETE CASCADE,
    subject    text NOT NULL,
    email      text,
    role       text NOT NULL CHECK (role IN ('viewer', 'editor', 'owner')),
    added_at   timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (project_id, subject)
);
-- One saved AI card version of the project's one AI system (WP7, S7.x):
-- number 1, 2, ... per project. The platform owns it; an assessment is of
-- exactly one of these rows. Only the columns this service reads.
CREATE TABLE IF NOT EXISTS core.system (
    pid        uuid PRIMARY KEY,
    project_id uuid REFERENCES core.project (pid) ON DELETE CASCADE,
    number     int NOT NULL,
    -- what an assessment's (system_id, project_id) points at
    CONSTRAINT system_pid_project_id_key UNIQUE (pid, project_id)
);
"""


@pytest.fixture()
def repository(database_url, objectives):
    """A repository on a freshly created schema, dropped afterwards."""
    from sqlalchemy import create_engine, text

    from aisc_control_objectives.db import tables
    from aisc_control_objectives.db.repository import ProjectRepository

    admin = create_engine(database_url.rsplit("/", 1)[0] + "/postgres", isolation_level="AUTOCOMMIT")
    name = database_url.rsplit("/", 1)[1]
    with admin.connect() as connection:
        connection.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        connection.execute(text(f'CREATE DATABASE "{name}"'))

    repo = ProjectRepository(database_url, objectives_digest=objectives.digest)
    with repo._engine.begin() as connection:
        connection.execute(text(CORE_PROJECT_DDL))
    repo.create_all()
    yield repo
    # project_member points at core.project, which the metadata below drops.
    # The platform owns this table in production; here the suite made it, so
    # here the suite takes it away again.
    with repo._engine.begin() as connection:
        connection.execute(text("DROP TABLE IF EXISTS core.project_member"))
        # core.system points at core.project too; CASCADE also takes the
        # assessment's foreign key into it, which drop_all removes anyway.
        connection.execute(text("DROP TABLE IF EXISTS core.system CASCADE"))
    tables.Base.metadata.drop_all(repo._engine)


@pytest.fixture()
def platform_project(repository) -> str:
    """A project on the platform, which is what an assessment belongs to."""
    import uuid

    from sqlalchemy import text

    pid = str(uuid.uuid4())
    with repository._engine.begin() as connection:
        connection.execute(
            text("INSERT INTO core.project (pid, name, slug) VALUES (:pid, :n, :s)"),
            {"pid": pid, "n": "MCAS", "s": f"mcas-{pid[:8]}"},
        )
    return pid


@pytest.fixture()
def project_member(repository):
    """A project with somebody in it, or with nobody.

    Returns its pid, its slug and the subject, because a page is opened with
    the pid and the two name the same project.
    """
    import uuid

    from sqlalchemy import text

    def make(role: str | None) -> tuple[str, str, str]:
        pid, slug, subject = str(uuid.uuid4()), f"p-{uuid.uuid4().hex[:8]}", f"s-{uuid.uuid4().hex[:8]}"
        with repository._engine.begin() as connection:
            connection.execute(
                text("INSERT INTO core.project (pid, name, slug) VALUES (:pid, :name, :slug)"),
                {"pid": pid, "name": slug, "slug": slug},
            )
            if role is not None:
                connection.execute(
                    text(
                        "INSERT INTO core.project_member (project_id, subject, role)"
                        " VALUES (:pid, :subject, :role)"
                    ),
                    {"pid": pid, "subject": subject, "role": role},
                )
        return pid, slug, subject

    return make


@pytest.fixture()
def system_version(repository):
    """A saved AI card version (`core.system` row) of a platform project.

    `system_version(project_pid, number)` returns the new row's pid.
    """
    import uuid

    from sqlalchemy import text

    def make(project: str, number: int) -> str:
        pid = str(uuid.uuid4())
        with repository._engine.begin() as connection:
            connection.execute(
                text("INSERT INTO core.system (pid, project_id, number) VALUES (:pid, :p, :n)"),
                {"pid": pid, "p": project, "n": number},
            )
        return pid

    return make
