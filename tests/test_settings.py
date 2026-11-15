"""Where the databases are: the URLs SQLAlchemy is handed, from DATABASE_URL and
PROJECT_DATABASE_URL."""

import pytest

from aisc_control_objectives.settings import (
    database_url,
    project_database_url,
)


def test_a_plain_postgresql_url_gets_the_installed_driver():
    url = database_url({"DATABASE_URL": "postgresql://user:pw@db:5432/platform"})
    assert url == "postgresql+psycopg://user:pw@db:5432/platform"


def test_a_url_that_names_its_driver_is_kept():
    url = "postgresql+psycopg://user:pw@db:5432/platform"
    assert database_url({"DATABASE_URL": url}) == url


def test_the_prisma_schema_parameter_is_dropped():
    url = database_url({"DATABASE_URL": "postgresql://user:pw@db:5432/platform?schema=public"})
    assert url == "postgresql+psycopg://user:pw@db:5432/platform"


def test_the_project_template_is_derived_from_the_platform_url():
    env = {"DATABASE_URL": "postgresql://user:pw@db:5432/platform?schema=public"}
    assert project_database_url(env) == "postgresql+psycopg://user:pw@db:5432/{database}"


def test_an_explicit_project_template_wins_and_is_normalised():
    env = {
        "DATABASE_URL": "postgresql://user:pw@db:5432/platform",
        "PROJECT_DATABASE_URL": "postgresql://other:pw@projects:5433/{database}?schema=controls",
    }
    assert project_database_url(env) == "postgresql+psycopg://other:pw@projects:5433/{database}"


def test_a_project_template_without_a_database_slot_is_refused():
    env = {"PROJECT_DATABASE_URL": "postgresql://user:pw@db:5432/project_fixed"}
    with pytest.raises(ValueError, match=r"\{database\}"):
        project_database_url(env)


def test_the_migrations_have_a_template_of_their_own():
    from aisc_control_objectives.settings import project_migrate_database_url

    env = {"PROJECT_MIGRATE_DATABASE_URL": "postgresql://control_objectives_migrator:pw@db:5432/{database}?x=1"}
    assert project_migrate_database_url(env) == (
        "postgresql+psycopg://control_objectives_migrator:pw@db:5432/{database}")


@pytest.mark.parametrize("env", [{}, {"PROJECT_MIGRATE_DATABASE_URL": "postgresql://m:pw@db:5432/platform"}])
def test_the_migration_template_is_required_and_names_the_database(env):
    from aisc_control_objectives.settings import project_migrate_database_url

    with pytest.raises(ValueError, match="PROJECT_MIGRATE_DATABASE_URL"):
        project_migrate_database_url(env)

