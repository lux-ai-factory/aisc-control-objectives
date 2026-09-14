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
        "WIZARD_TEST_DATABASE_URL",
        "postgresql+psycopg://aisc-postgres-user:dev-password@localhost:5432/wizard_test",
    )


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
    repo.create_all()
    yield repo
    tables.Base.metadata.drop_all(repo._engine)
