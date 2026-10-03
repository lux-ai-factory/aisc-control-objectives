"""One database per project: the checks that need no database."""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from isolation_support import EXAMPLE_DB, EXAMPLE_PID

APP = Path(__file__).resolve().parents[1]
SRC = APP / "src" / "aisc_control_objectives"
VERSIONS = APP / "alembic" / "versions"
BASELINE = "20260926000000_project_database"


def _projectdb():
    # imported per test, so a missing module fails each test on its own rather
    # than stopping the suite from collecting
    from aisc_control_objectives import projectdb

    return projectdb


# One naming rule


def test_I1_8_the_example_pid_names_its_database():
    assert _projectdb().database_name(EXAMPLE_PID) == EXAMPLE_DB
    assert _projectdb().database_name(EXAMPLE_PID.upper()) == EXAMPLE_DB


@pytest.mark.parametrize(
    "bad",
    ["platform", "postgres", "mcas", "../x", EXAMPLE_PID + "x", EXAMPLE_PID.replace("-", ""),
     "3f2b8c1e-0d4a-4e7b-9a55-1c2d3e4f5a6g", f"{EXAMPLE_PID}; DROP DATABASE platform", ""],
)
def test_I1_8_I5_2_anything_but_a_pid_never_becomes_a_database_name(bad):
    with pytest.raises(ValueError):
        _projectdb().database_name(bad)


# The only way into a project database


def test_I5_1_create_engine_appears_only_in_projectdb():
    """Every engine, the platform one and each project one, is made in one
    module, so no code path can reach a project database without the door
    (`ProjectDatabases.open`) deciding membership first."""
    assert (SRC / "projectdb.py").is_file(), "aisc_control_objectives/projectdb.py is missing"
    offenders = [
        str(path.relative_to(APP))
        for path in SRC.rglob("*.py")
        if "create_engine(" in path.read_text() and path.name != "projectdb.py"
    ]
    assert offenders == []


def test_I5_1_the_door_is_ProjectDatabases_open():
    projectdb = _projectdb()
    assert hasattr(projectdb, "ProjectDatabases")
    assert callable(getattr(projectdb.ProjectDatabases, "open", None))


# Version numbers come from project.system of the same database


def test_I5_3_the_repository_reads_project_system_not_core():
    source = (SRC / "db" / "repository.py").read_text()
    assert "core.system" not in source
    assert "project.system" in source


def test_I5_3_I1_7_the_assessment_has_no_project_id_column():
    """control_objectives.project has no project_id column: the database is the project."""
    from aisc_control_objectives.db import tables

    assert tables.Project.__tablename__ == "project"
    assert "project_id" not in tables.Project.__table__.columns
    assert not any(t.schema == "core" for t in tables.Base.metadata.tables.values())


def test_I5_1_nothing_in_src_names_core_system():
    offenders = [
        str(path.relative_to(APP))
        for path in SRC.rglob("*.py")
        if re.search(r"core\.system\b", path.read_text())
    ]
    assert offenders == []


# One baseline revision, nothing outside the project database


def _revision_files() -> list[Path]:
    return sorted(p for p in VERSIONS.glob("*.py") if p.name != "__init__.py")


def test_I5_4_one_baseline_revision():
    """One baseline revision; every later revision sits on it, in order."""
    assert [p.stem for p in _revision_files()] == [
        BASELINE, "20261001000000_selection", "20261001100000_objective_ids", "20261001110000_mapping_source",
        "20261001120000_severity_comment", "20261002000000_objective_sets", "20261002100000_rcm",
        "20261003000000_ledger_history"]
    roots = [p.stem for p in _revision_files()
             if re.search(r"^down_revision\s*=\s*None", p.read_text(), re.M)]
    assert roots == [BASELINE], "the baseline, and only it, revises nothing"


def test_I5_4_no_revision_names_core():
    offenders = [p.name for p in _revision_files() if re.search(r"core\.", p.read_text())]
    assert offenders == []
    assert _revision_files(), "no revision at all"


def test_I5_4_the_baseline_points_system_id_at_project_system():
    text = (VERSIONS / f"{BASELINE}.py").read_text()
    assert "project.system" in text
    assert "CASCADE" in text.upper()
    for reader in ("report_ro", "dashboard_ro"):
        assert reader in text, f"I2.6: the baseline grants {reader} its reads"


def test_I5_4_env_sets_the_search_path_to_its_own_schema_only():
    text = (APP / "alembic" / "env.py").read_text()
    paths = re.findall(r"SET search_path TO ([^\"')]+)", text)
    assert paths, "env.py sets no search_path"
    for path in paths:
        names = [n.strip() for n in path.replace("{SCHEMA}", "control_objectives").split(",")]
        assert names == ["control_objectives"], path


# The migrate one-shot exists


def test_I5_5_migrate_projects_is_a_module_with_main():
    from aisc_control_objectives import migrate_projects

    assert callable(migrate_projects.main)


# Routes


@pytest.fixture()
def routes(monkeypatch):
    """The route table of the app as composed. Nothing connects: engines are
    lazy, and the database URLs point at a closed port."""
    from aisc_control_objectives import server

    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://x:y@127.0.0.1:1/platform")
    monkeypatch.setenv("PROJECT_DATABASE_URL", "postgresql+psycopg://x:y@127.0.0.1:1/{database}")
    monkeypatch.setenv("BAF_LLM_PROVIDER", "ollama")
    monkeypatch.setenv("BAF_LLM_MODEL", "mistral:latest")
    monkeypatch.setenv("BAF_LLM_BASE_URL", "http://127.0.0.1:1")
    monkeypatch.setattr(server, "_build_completer", lambda config: (lambda *a, **k: ""))
    app = server.build_app()
    return {(m, r.path) for r in app.routes for m in (getattr(r, "methods", None) or ())}


def test_I5_2_no_route_under_the_old_api_projects(routes):
    assert [r for r in routes if r[1].startswith("/api/projects")] == []


@pytest.mark.parametrize(
    "method,shape",
    [
        ("GET", r"^/p/\{[^}]+\}/api/projects$"),
        ("GET", r"^/p/\{[^}]+\}/api/projects/\{[^}]+\}$"),
        ("DELETE", r"^/p/\{[^}]+\}/api/projects/\{[^}]+\}$"),
        ("POST", r"^/p/\{[^}]+\}/api/projects/\{[^}]+\}/map$"),
        ("POST", r"^/p/\{[^}]+\}/api/projects/\{[^}]+\}/severity$"),
    ],
)
def test_I5_2_the_json_api_is_under_the_project(routes, method, shape):
    assert any(m == method and re.match(shape, path) for m, path in routes), (method, shape)


def test_I5_2_the_public_catalogue_routes_stay(routes):
    for path in ("/objectives", "/api/control-objectives", "/api/macro-requirements",
                 "/api/config", "/health"):
        assert ("GET", path) in routes, path


# The test database fixture refuses the wrong database


@pytest.mark.parametrize(
    "database",
    ["platform", "postgres", "project_3f2b8c1e0d4a4e7b9a551c2d3e4f5a6b", "project_anything"],
)
def test_I5_7_the_drop_and_recreate_fixture_refuses(database):
    """The `repository` fixture drops and recreates the database its URL names.
    Pointed at platform, postgres or a project database it must refuse before
    connecting. Proven on a closed port (127.0.0.1:1): nothing can be dropped
    even if it did not refuse, and a connection attempt shows as
    OperationalError, which a refusal never produces."""
    env = dict(os.environ)
    env["CONTROL_OBJECTIVES_TEST_DATABASE_URL"] = (
        f"postgresql+psycopg://nobody:nothing@127.0.0.1:1/{database}"
    )
    run = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-x",
         "tests/test_repository.py", "-k", "test_", "--maxfail=1"],
        cwd=APP, env=env, capture_output=True, text=True, timeout=300,
    )
    out = run.stdout + run.stderr
    assert run.returncode != 0, "a test ran against a database the fixture must refuse"
    assert "OperationalError" not in out, "the fixture tried to connect instead of refusing"
    assert database in out, "the refusal names the database it refused"
