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


#: Databases the suite must never drop and recreate (I5.7): the platform's, the
#: server's maintenance database and any project's own.
_REFUSED_NAMES = ("platform", "postgres")


def refused_database(url: str | None) -> str | None:
    """Why the suite may not drop and recreate the database `url` names, or None.

    Decided from the URL alone, before anything connects: the fixtures below
    DROP the database they are given, so a URL that names the platform, the
    maintenance database, a project database, or the running stack's port
    (5432) is refused outright, and so is no URL at all (the old default was
    the running stack's Postgres).
    """
    from urllib.parse import urlsplit

    if not url:
        return "CONTROL_OBJECTIVES_TEST_DATABASE_URL is not set; point it at a throwaway Postgres"
    parts = urlsplit(url)
    name = parts.path.rsplit("/", 1)[-1]
    if name in _REFUSED_NAMES or name.startswith("project_"):
        return (f"refusing to drop and recreate database {name!r}: the test database must be a"
                " scratch database, never platform, postgres or a project_ database")
    if (parts.port or 5432) == 5432:
        return (f"refusing to drop and recreate database {name!r} on port 5432, where the running"
                " stack's Postgres listens; use a throwaway on another port")
    return None


@pytest.fixture(scope="session")
def database_url() -> str:
    """A database of its own for the suite, on a throwaway Postgres.

    Not SQLite: testing against a different engine from production is how you
    find out `text[]` does not exist on the day you deploy. Refused before any
    connection when it names a database the suite must not drop (I5.7).
    """
    import os

    url = os.environ.get("CONTROL_OBJECTIVES_TEST_DATABASE_URL")
    why = refused_database(url)
    if why is not None:
        pytest.fail(why, pytrace=False)
    return url


#: What this service reads from outside its own schema, in the shape its owners
#: give it. `core.project` and `core.project_member` stand in for the platform
#: database (membership, in the single-database `create_app(engine=...)` tests);
#: `project.system` is the project database's list of card versions (template
#: 0006, I1.5), which an assessment points at. In the suite one database plays
#: both parts; deployed, they are two (tests/test_isolation_project_databases.py).
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
-- One saved AI card version of the project's one AI system, numbered 1, 2, ...
-- (the platform writes it; I1.5). An assessment is of exactly one of these rows.
CREATE SCHEMA IF NOT EXISTS project;
CREATE TABLE IF NOT EXISTS project.system (
    pid         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    number      integer NOT NULL CHECK (number > 0) UNIQUE,
    name        text NOT NULL,
    version     text,
    provider    text,
    description text,
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now(),
    created_by  text
);
"""


@pytest.fixture()
def repository(database_url, objectives):
    """A repository on a freshly created database, dropped afterwards.

    The database is one project's (its pid is `repository.pid`): the
    assessment carries no project of its own any more (I1.7), the database it
    is in is the project.
    """
    import uuid

    from sqlalchemy import create_engine, text

    from aisc_control_objectives.db.repository import ProjectRepository

    admin = create_engine(database_url.rsplit("/", 1)[0] + "/postgres", isolation_level="AUTOCOMMIT")
    name = database_url.rsplit("/", 1)[1]
    with admin.connect() as connection:
        connection.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        connection.execute(text(f'CREATE DATABASE "{name}"'))

    repo = ProjectRepository(database_url, objectives_digest=objectives.digest, pid=str(uuid.uuid4()))
    with repo._engine.begin() as connection:
        connection.execute(text(CORE_PROJECT_DDL))
    repo.create_all()
    yield repo
    repo._engine.dispose()
    with admin.connect() as connection:
        connection.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
    admin.dispose()


@pytest.fixture()
def platform_project(repository) -> str:
    """The platform project whose database this is: `core.project` has its row."""
    from sqlalchemy import text

    pid = repository.pid
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
    """A saved AI card version (`project.system` row) of the database's project.

    `system_version(project_pid, number)` returns the new row's pid. The
    project argument is kept so call sites read as before; the database is the
    project, so it is not stored (project.system has no project column).
    """
    import uuid

    from sqlalchemy import text

    def make(project: str, number: int) -> str:
        pid = str(uuid.uuid4())
        with repository._engine.begin() as connection:
            connection.execute(
                text("INSERT INTO project.system (pid, number, name) VALUES (:pid, :n, 'MCAS')"),
                {"pid": pid, "n": number},
            )
        return pid

    return make
