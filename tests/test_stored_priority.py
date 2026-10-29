"""The priority objectives are stored on the selection, where the report reads them.

`objective_selection.priority_ids` holds the objectives of the selection that are priority: the
assessor's choices in `objective_key` over the default rule (the first seven by rank whose highest risk
is High or Critical; a voluntary objective never by default). Every change that can move them writes
them again, in the change's own transaction, so what is stored is always what the page shows. The
scores stay computed on every read.
"""
from __future__ import annotations

import json

import pytest
from sqlalchemy import text

from aisc_control_objectives.library import FULL_AI_ACT, Library
from aisc_control_objectives.projects import Projects
from step2_support import FakeMapper
from test_library import fields


@pytest.fixture()
def projects(repository, objectives):
    return Projects(repository, objectives, FakeMapper(), model="fake/model", default_profile=FULL_AI_ACT)


@pytest.fixture()
def graph(fixtures_dir):
    return json.loads((fixtures_dir / "mcas.ontology.jsonld").read_text())


def stored(repository, assessment) -> list[str] | None:
    with repository.engine.connect() as connection:
        return connection.execute(text(
            "SELECT priority_ids FROM control_objectives.objective_selection WHERE project_id = :a"),
            {"a": assessment}).scalar()


def shown(projects, assessment) -> list[str]:
    """The selected objectives the page marks priority, in the selection's order."""
    view = projects.view(assessment)
    key = {p.objective_id for p in view.priorities if p.key}
    return [oid for oid in view.record.selected or [] if oid in key]


def test_every_change_stores_what_the_page_shows(projects, repository, objectives, graph, platform_project,
                                                 system_version):
    a = projects.create(platform_project, "MCAS", json.dumps(graph), graph,
                        system_version(platform_project, 1)).record.id
    assert stored(repository, a) is None                  # nothing mapped: no selection yet

    def same_after(step) -> list[str]:
        step()
        assert stored(repository, a) == shown(projects, a)
        return stored(repository, a)

    assert same_after(lambda: projects.map_risks_of(a)) == []          # mapped, nothing rated
    rated = same_after(lambda: projects.rate(a, {f"risk{n}": 5 for n in range(5)},
                                             {f"risk{n}": 4 for n in range(5)}))
    assert rated and len(rated) <= 7
    chosen = same_after(lambda: projects.set_keys(a, {rated[0]: False, "O9": True}))
    assert rated[0] not in chosen and "O9" in chosen
    assert same_after(lambda: projects.reset_keys(a)) == rated
    same_after(lambda: projects.rate(a, {"risk2": None}, {}))           # half rated: counts nothing
    same_after(lambda: projects.map_by_hand(a, "risk1", ["O1"]))

    changed = [node for node in graph if "#risk4" not in node["@id"]]
    same_after(lambda: projects.update_card(a, json.dumps(changed), changed))

    library = Library(repository.engine, objectives)
    made = library.create_set("BNK", "Bank policies", "", who="alice")
    library.add_objective(made.id, fields())
    library.publish(made.id, who="alice")
    profile = library.create_profile("Bank", "", ["O1", "O7", "BNK1"], who="alice")
    assert set(same_after(lambda: projects.use_profile(a, profile.id))) <= {"O1", "O7", "BNK1"}


def test_a_new_card_versions_assessment_stores_the_priority_it_carried(projects, repository, graph,
                                                                       platform_project, system_version):
    first = projects.create(platform_project, "MCAS", json.dumps(graph), graph,
                            system_version(platform_project, 1)).record.id
    projects.map_risks_of(first)
    projects.rate(first, {f"risk{n}": 5 for n in range(5)}, {f"risk{n}": 5 for n in range(5)})
    second = projects.create(platform_project, "MCAS", json.dumps(graph), graph,
                             system_version(platform_project, 2)).record.id
    assert stored(repository, second) == shown(projects, second) == stored(repository, first) != []


def test_the_report_may_read_it(migrated_with_readers):
    """report_ro (and dashboard_ro) read the selection, the new column with it."""
    with migrated_with_readers.connect() as connection:
        for role in ("report_ro", "dashboard_ro"):
            assert connection.execute(text(
                "SELECT has_column_privilege(:r, 'control_objectives.objective_selection', 'priority_ids',"
                " 'SELECT')"), {"r": role}).scalar(), role


@pytest.fixture()
def migrated_with_readers(database_url):
    """A scratch project database migrated to head on a cluster that has the readers' roles, as the
    platform's has: the ones missing here are made for the test and dropped after it."""
    from sqlalchemy import create_engine

    from aisc_control_objectives import projectdb
    from test_migration_project_database import _scratch

    admin = create_engine(database_url.rsplit("/", 1)[0] + "/postgres", isolation_level="AUTOCOMMIT")
    made = []
    with admin.connect() as connection:
        for role in ("report_ro", "dashboard_ro"):
            if not connection.execute(text("SELECT 1 FROM pg_roles WHERE rolname = :r"), {"r": role}).first():
                connection.execute(text(f"CREATE ROLE {role} NOLOGIN"))
                made.append(role)
    engine, drop = _scratch(database_url, "alembic_priority_readers")
    try:
        projectdb.migrate(engine)
        yield engine
    finally:
        drop()
        with admin.connect() as connection:
            for role in made:
                connection.execute(text(f"DROP ROLE {role}"))
        admin.dispose()
