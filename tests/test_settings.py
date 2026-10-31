"""Where the databases are: the URLs SQLAlchemy is handed, from DATABASE_URL and
PROJECT_DATABASE_URL."""

import pytest

from aisc_control_objectives.settings import (
    DEFAULT_URL,
    database_url,
    project_database_url,
)


def test_no_database_url_yields_the_default():
    assert database_url({}) == DEFAULT_URL


def test_a_plain_postgresql_url_gets_the_installed_driver():
    url = database_url({"DATABASE_URL": "postgresql://user:pw@db:5432/platform"})
    assert url == "postgresql+psycopg://user:pw@db:5432/platform"


def test_a_url_that_names_its_driver_is_kept():
    url = "postgresql+psycopg://user:pw@db:5432/platform"
    assert database_url({"DATABASE_URL": url}) == url


def test_a_query_string_is_dropped():
    url = database_url({"DATABASE_URL": "postgresql://user:pw@db:5432/platform?schema=public"})
    assert url == "postgresql+psycopg://user:pw@db:5432/platform"


def test_the_project_template_is_derived_from_the_platform_url():
    env = {"DATABASE_URL": "postgresql://user:pw@db:5432/platform?schema=public"}
    assert project_database_url(env) == "postgresql+psycopg://user:pw@db:5432/{database}"


def test_an_explicit_project_template_wins_and_is_normalised():
    env = {
        "DATABASE_URL": "postgresql://user:pw@db:5432/platform",
        "PROJECT_DATABASE_URL": "postgresql://other:pw@projects:5433/{database}?sslmode=disable",
    }
    assert project_database_url(env) == "postgresql+psycopg://other:pw@projects:5433/{database}"


def test_a_project_template_without_a_database_slot_is_refused():
    env = {"PROJECT_DATABASE_URL": "postgresql://user:pw@db:5432/project_fixed"}
    with pytest.raises(ValueError, match=r"\{database\}"):
        project_database_url(env)
