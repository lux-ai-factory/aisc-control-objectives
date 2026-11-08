"""The alembic baseline of a project database, 20260926000000_project_database.

The baseline makes the assessment tables without a `project_id` column, with
`system_id` pointing at `project.system(pid)` ON DELETE CASCADE. The later
revisions sit on it; these tests check the head, the cascade from a deleted
version, the data each revision rewrites, and create_all agreeing with the
migrations.

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
#: The objective selection, on top of the baseline.
SELECTION = "20261001000000_selection"
#: The objective ids O1 ... O50, on top of the selection.
RENAME = "20261001100000_objective_ids"
#: Who mapped a risk to an objective, the AI or a person, on top of the rename.
SOURCE = "20261001110000_mapping_source"
#: An optional comment on a risk's severity, on top of the source.
COMMENT = "20261001120000_severity_comment"
#: Objective sets and profiles, on top of the comment.
SETS = "20261002000000_objective_sets"
#: The risk and control matrix: impact x likelihood, key objectives, scope = the matrix.
RCM = "20261002100000_rcm"
#: The ledger: mapping changes keep what they replace; authors by subject.
LEDGER = "20261003000000_ledger_history"
#: Which risks a person mapped, so an AI run leaves them alone.
BY_HAND = "20261018000000_mapped_by_hand"
#: What an assessment took from the previous card version's.
CARRIED = "20261018100000_carried"
#: An update to a changed card keeps what it replaces.
CARD = "20261018200000_card_archive"
#: The built-in short profile's new name.
PROFILE_NAME = "20261018300000_profile_name"
#: The stored card no longer keeps a count of its risks, which nothing read.
GRAPH_RISK_COUNT = "20261029000000_graph_risk_count"
#: The selection stores which of its objectives are priority, for the report.
PRIORITY = "20261030000000_priority_ids"
#: The provider's measures leave step 2: the risk's two columns, and the stored cards' RiskControl nodes.
HEAD = "20261108000000_no_risk_measures"
TABLES = ("project", "graph", "risk", "mapped_objective", "mapping_run", "objective_selection",
          "objective_set", "objective_draft", "objective_set_version", "objective_set_version_item",
          "objective_profile", "objective_profile_version", "objective_profile_version_item", "objective_key",
          "mapping_archive")


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


def test_upgrading_to_head_gives_the_head_and_the_tables(migrated):
    with migrated.connect() as connection:
        assert connection.execute(
            text("SELECT version_num FROM control_objectives.alembic_version")
        ).scalars().all() == [HEAD]
        tables = set(connection.execute(text(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'control_objectives'"
        )).scalars())
    assert tables == set(TABLES) | {"alembic_version"}


def test_the_chain_is_baseline_selection_rename_source():
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    from aisc_control_objectives import projectdb

    config = Config(str(projectdb.ALEMBIC_INI))
    config.set_main_option("script_location", str(projectdb.ALEMBIC_DIR))
    script = ScriptDirectory.from_config(config)
    assert script.get_heads() == [HEAD]
    assert script.get_revision(HEAD).down_revision == PRIORITY
    assert script.get_revision(PRIORITY).down_revision == GRAPH_RISK_COUNT
    assert script.get_revision(GRAPH_RISK_COUNT).down_revision == PROFILE_NAME
    assert script.get_revision(PROFILE_NAME).down_revision == CARD
    assert script.get_revision(CARD).down_revision == CARRIED
    assert script.get_revision(CARRIED).down_revision == BY_HAND
    assert script.get_revision(BY_HAND).down_revision == LEDGER
    assert script.get_revision(LEDGER).down_revision == RCM
    assert script.get_revision(RCM).down_revision == SETS
    assert script.get_revision(SETS).down_revision == COMMENT
    assert script.get_revision(COMMENT).down_revision == SOURCE
    assert script.get_revision(SOURCE).down_revision == RENAME
    assert script.get_revision(RENAME).down_revision == SELECTION
    assert script.get_revision(SELECTION).down_revision == BASELINE
    assert script.get_revision(BASELINE).down_revision is None


def test_the_readers_read_the_selection_and_the_platform_is_granted_nothing(migrated):
    """Step 4 is a platform page that reads as report_ro: platform_rw gets nothing here."""
    with migrated.connect() as connection:
        def can(role, table, what="SELECT"):
            return connection.execute(
                text("SELECT has_table_privilege(:r, :t, :w)"),
                {"r": role, "t": f"control_objectives.{table}", "w": what},
            ).scalar()

        assert can("dashboard_ro", "objective_selection")
        assert not can("dashboard_ro", "objective_selection", "INSERT")
        assert not can("platform_rw", "objective_selection")


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
    """The key into project.system cascades."""
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
            " source, vulnerability, consequence, impact, stakeholder,"
            " areas, vair_terms, provenance) VALUES ('a1', 'risk0', 0, 't', '', '', '', '', '', '',"
            " '{}', '{}', 'form')"))
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
    # one key into project.system; the other is the profile version it runs on
    assert sorted(tuple(k) for k in keys) == [
        ("fk_project_profile_version_id",
         "FOREIGN KEY (profile_version_id) REFERENCES objective_profile_version(id) ON DELETE RESTRICT"),
        ("fk_project_system_id_project_system",
         "FOREIGN KEY (system_id) REFERENCES project.system(pid) ON DELETE CASCADE"),
    ]
    assert columns == ["id", "name", "objectives_digest", "created_at", "updated_at", "system_id",
                       "profile_version_id", "carried"]


def test_the_model_and_the_migration_agree(migrated, repository):
    """create_all (the suite's schema) and the baseline give the same keys,
    indexes and columns."""
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


def test_an_assessment_mapped_before_the_selection_gets_its_mapped_objectives(database_url):
    """An assessment already mapped when the selection arrives starts
    with every objective its mapping linked ticked; one not yet mapped gets no selection."""
    from alembic import command
    from alembic.config import Config

    from aisc_control_objectives import projectdb

    engine, drop = _scratch(database_url, "alembic_backfill")
    try:
        config = Config(str(projectdb.ALEMBIC_INI))
        config.set_main_option("script_location", str(projectdb.ALEMBIC_DIR))
        with engine.begin() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, BASELINE)
        v1, v2 = str(uuid.uuid4()), str(uuid.uuid4())
        with engine.begin() as connection:
            connection.execute(text("INSERT INTO project.system (pid, number, name) VALUES (:a, 1, 'S'), (:b, 2, 'S')"),
                               {"a": v1, "b": v2})
            connection.execute(text(
                "INSERT INTO control_objectives.project (id, name, objectives_digest, created_at, updated_at, system_id)"
                " VALUES ('mapped', 'S', '', now(), now(), :a), ('fresh', 'S', '', now(), now(), :b)"),
                {"a": v1, "b": v2})
            for assessment, risk in (("mapped", "risk0"), ("mapped", "risk1"), ("fresh", "risk0")):
                connection.execute(text(
                    "INSERT INTO control_objectives.risk (project_id, risk_id, position, text, short_label, source,"
                    " vulnerability, consequence, impact, stakeholder, control, follow_up_control, areas, vair_terms,"
                    " provenance) VALUES (:p, :r, 0, 't', '', '', '', '', '', '', '', '', '{}', '{}', 'form')"),
                    {"p": assessment, "r": risk})
            connection.execute(text(
                "INSERT INTO control_objectives.mapped_objective (risk_row_id, objective_id, quote, rationale)"
                " SELECT r.id, o, '', '' FROM control_objectives.risk r,"
                " unnest(CASE r.risk_id WHEN 'risk0' THEN ARRAY['R1.1', 'R2.3'] ELSE ARRAY['R2.3', 'R6.1'] END) o"
                " WHERE r.project_id = 'mapped'"))
            connection.execute(text(
                "INSERT INTO control_objectives.mapping_run (project_id, findings, stops, stop, attempts, error,"
                " model, ran_at) VALUES ('mapped', '[]', '{}', 'clean', 1, '', 'm', now())"))
        projectdb.migrate(engine)
        with engine.connect() as connection:
            rows = connection.execute(text(
                "SELECT project_id, objective_ids FROM control_objectives.objective_selection")).all()
        assert [tuple(r) for r in rows] == [("mapped", ["O1", "O7", "O24"])]
    finally:
        drop()


def test_the_stored_ids_become_o_ids_in_catalogue_order(database_url):
    """Every stored objective id is renamed by objective_id_renames.csv; a selection is
    kept in catalogue order (O7 before O24); an id the table does not know is left as it is."""
    from alembic import command
    from alembic.config import Config

    from aisc_control_objectives import projectdb

    engine, drop = _scratch(database_url, "alembic_rename")
    try:
        config = Config(str(projectdb.ALEMBIC_INI))
        config.set_main_option("script_location", str(projectdb.ALEMBIC_DIR))
        with engine.begin() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, SELECTION)
        v1 = str(uuid.uuid4())
        with engine.begin() as connection:
            connection.execute(text("INSERT INTO project.system (pid, number, name) VALUES (:a, 1, 'S')"), {"a": v1})
            connection.execute(text(
                "INSERT INTO control_objectives.project (id, name, objectives_digest, created_at, updated_at, system_id)"
                " VALUES ('a', 'S', '', now(), now(), :a)"), {"a": v1})
            connection.execute(text(
                "INSERT INTO control_objectives.risk (project_id, risk_id, position, text, short_label, source,"
                " vulnerability, consequence, impact, stakeholder, control, follow_up_control, areas, vair_terms,"
                " provenance) VALUES ('a', 'risk0', 0, 't', '', '', '', '', '', '', '', '', '{}', '{}', 'form')"))
            connection.execute(text(
                "INSERT INTO control_objectives.mapped_objective (risk_row_id, objective_id, quote, rationale)"
                " SELECT r.id, o, '', '' FROM control_objectives.risk r, unnest(ARRAY['R11.4', 'R2.3', 'R77.1']) o"))
            connection.execute(text(
                "INSERT INTO control_objectives.objective_selection (project_id, objective_ids, updated_at)"
                " VALUES ('a', ARRAY['R6.1', 'R11.4', 'R2.3'], now())"))
        # up to the rename only: from 20261002100000_rcm on, the selection is the matrix
        with engine.begin() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, RENAME)
        with engine.connect() as connection:
            mapped = connection.execute(text(
                "SELECT objective_id FROM control_objectives.mapped_objective ORDER BY id")).scalars().all()
            selected = connection.execute(text(
                "SELECT objective_ids FROM control_objectives.objective_selection")).scalar_one()
        assert mapped == ["O50", "O7", "R77.1"]
        assert selected == ["O7", "O24", "O50"]
    finally:
        drop()


def test_every_row_mapped_before_is_the_ais(migrated):
    with migrated.connect() as connection:
        column = connection.execute(text(
            "SELECT is_nullable, column_default FROM information_schema.columns"
            " WHERE table_schema = 'control_objectives' AND table_name = 'mapped_objective'"
            "   AND column_name = 'source'")).one()
    assert column[0] == "NO" and "'ai'" in column[1]


def test_every_risk_starts_with_no_comment(migrated):
    with migrated.connect() as connection:
        column = connection.execute(text(
            "SELECT is_nullable, column_default FROM information_schema.columns"
            " WHERE table_schema = 'control_objectives' AND table_name = 'risk'"
            "   AND column_name = 'severity_comment'")).one()
    assert column[0] == "NO" and "''" in column[1]


def test_the_readers_read_the_sets_and_profiles(migrated):
    """The report and the platform's step 4 page name a user's objectives from these (as report_ro)."""
    with migrated.connect() as connection:
        for table in ("objective_set", "objective_set_version", "objective_set_version_item",
                      "objective_profile", "objective_profile_version", "objective_profile_version_item", "objective_key"):
            for role in ("report_ro", "dashboard_ro"):
                if connection.execute(text("SELECT 1 FROM pg_roles WHERE rolname = :r"), {"r": role}).first():
                    assert connection.execute(text(
                        "SELECT has_table_privilege(:r, :t, 'SELECT')"),
                        {"r": role, "t": f"control_objectives.{table}"}).scalar(), (role, table)


def test_severity_becomes_impact_and_the_scope_is_the_matrix(database_url):
    """A risk's severity is its impact, its likelihood starts unrated; what an assessment
    takes forward becomes what its matrix holds, in catalogue order."""
    from alembic import command
    from alembic.config import Config

    from aisc_control_objectives import projectdb

    engine, drop = _scratch(database_url, "alembic_matrix")
    try:
        config = Config(str(projectdb.ALEMBIC_INI))
        config.set_main_option("script_location", str(projectdb.ALEMBIC_DIR))
        with engine.begin() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, SETS)
        v1 = str(uuid.uuid4())
        with engine.begin() as connection:
            connection.execute(text("INSERT INTO project.system (pid, number, name) VALUES (:a, 1, 'S')"), {"a": v1})
            connection.execute(text(
                "INSERT INTO control_objectives.project (id, name, objectives_digest, created_at, updated_at, system_id)"
                " VALUES ('a', 'S', '', now(), now(), :a)"), {"a": v1})
            connection.execute(text(
                "INSERT INTO control_objectives.risk (project_id, risk_id, position, text, short_label, source,"
                " vulnerability, consequence, impact, stakeholder, control, follow_up_control, areas, vair_terms,"
                " provenance, severity) VALUES ('a', 'risk0', 0, 't', '', '', '', '', '', '', '', '', '{}', '{}',"
                " 'form', 4)"))
            connection.execute(text(
                "INSERT INTO control_objectives.mapped_objective (risk_row_id, objective_id, quote, rationale)"
                " SELECT r.id, o, '', '' FROM control_objectives.risk r, unnest(ARRAY['O10', 'O2']) o"))
            connection.execute(text(
                "INSERT INTO control_objectives.objective_selection (project_id, objective_ids, updated_at)"
                " VALUES ('a', ARRAY['O2', 'O24'], now())"))
        projectdb.migrate(engine)
        with engine.connect() as connection:
            rating = connection.execute(text(
                "SELECT rating_impact, rating_likelihood FROM control_objectives.risk")).one()
            selected = connection.execute(text(
                "SELECT objective_ids FROM control_objectives.objective_selection")).scalar_one()
        assert tuple(rating) == (4, None)
        assert selected == ["O2", "O10"]
    finally:
        drop()


@pytest.mark.parametrize("change", [
    "UPDATE control_objectives.mapping_archive SET reason = 'by_hand'",
    "DELETE FROM control_objectives.mapping_archive",
    "TRUNCATE control_objectives.mapping_archive",
])
def test_the_mapping_archive_is_append_only(migrated, change):
    """What a change replaced is kept; no row is changed or removed,
    not even by truncating the table. (Its owner can still drop the trigger: the ledger's frozen copies
    are the check on that.)"""
    from sqlalchemy.exc import DBAPIError

    with migrated.begin() as connection:
        connection.execute(text("INSERT INTO control_objectives.mapping_archive (project_id, reason, rows)"
                                " VALUES ('a1', 'ai_run', '[]')"))
    with pytest.raises(DBAPIError, match="immutable|append-only"):
        with migrated.begin() as connection:
            connection.execute(text(change))


def test_a_risk_with_a_persons_row_is_marked_mapped_by_hand(database_url):
    """Before the mark, a person's row was the only sign of their mapping: those risks get it."""
    from alembic import command
    from alembic.config import Config

    from aisc_control_objectives import projectdb

    engine, drop = _scratch(database_url, "alembic_by_hand")
    try:
        config = Config(str(projectdb.ALEMBIC_INI))
        config.set_main_option("script_location", str(projectdb.ALEMBIC_DIR))
        with engine.begin() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, LEDGER)
        v1 = str(uuid.uuid4())
        with engine.begin() as connection:
            connection.execute(text("INSERT INTO project.system (pid, number, name) VALUES (:a, 1, 'S')"), {"a": v1})
            connection.execute(text(
                "INSERT INTO control_objectives.project (id, name, objectives_digest, created_at, updated_at, system_id)"
                " VALUES ('a', 'S', '', now(), now(), :a)"), {"a": v1})
            for rid in ("risk0", "risk1", "risk2"):
                connection.execute(text(
                    "INSERT INTO control_objectives.risk (project_id, risk_id, position, text, short_label, source,"
                    " vulnerability, consequence, impact, stakeholder, control, follow_up_control, areas, vair_terms,"
                    " provenance) VALUES ('a', :r, 0, 't', '', '', '', '', '', '', '', '', '{}', '{}', 'form')"),
                    {"r": rid})
            connection.execute(text(
                "INSERT INTO control_objectives.mapped_objective (risk_row_id, objective_id, quote, rationale, source)"
                " SELECT r.id, 'O1', '', '', CASE r.risk_id WHEN 'risk0' THEN 'person' ELSE 'ai' END"
                " FROM control_objectives.risk r WHERE r.risk_id IN ('risk0', 'risk1')"))
        projectdb.migrate(engine)
        with engine.connect() as connection:
            marked = dict(connection.execute(text(
                "SELECT risk_id, mapped_by_hand FROM control_objectives.risk")).all())
        assert marked == {"risk0": True, "risk1": False, "risk2": False}
    finally:
        drop()


def test_an_existing_assessment_carried_nothing(migrated):
    with migrated.connect() as connection:
        column = next(c for c in _columns(connection) if c[:2] == ("project", "carried"))
    assert column[2:4] == ("jsonb", "YES") and column[4] is None


def test_the_archive_keeps_what_a_card_update_replaced(migrated):
    with migrated.begin() as connection:
        connection.execute(text("INSERT INTO control_objectives.mapping_archive (project_id, reason, rows)"
                                " VALUES ('a1', 'card', '[]')"))


def test_the_stored_short_profile_is_renamed_and_keeps_its_id(database_url):
    """A project database that stored the built-in short profile under its old name gets the new one; a
    profile of the project's own with that name is left alone."""
    from alembic import command
    from alembic.config import Config

    from aisc_control_objectives import projectdb

    engine, drop = _scratch(database_url, "alembic_profile_name")
    try:
        config = Config(str(projectdb.ALEMBIC_INI))
        config.set_main_option("script_location", str(projectdb.ALEMBIC_DIR))
        with engine.begin() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, CARD)
        with engine.begin() as connection:
            connection.execute(text(
                "INSERT INTO control_objectives.objective_profile (id, name, description, created_at, created_by)"
                " VALUES ('fairness-oversight', 'Annex IV reduced', '', now(), 'built-in'),"
                " ('a1b2', 'Annex IV reduced', '', now(), 'alice')"))
        projectdb.migrate(engine)
        with engine.connect() as connection:
            names = dict(connection.execute(text("SELECT id, name FROM control_objectives.objective_profile")).all())
        assert names == {"fairness-oversight": "Fairness and oversight", "a1b2": "Annex IV reduced"}
    finally:
        drop()


def test_the_stored_card_keeps_no_count_of_its_risks(database_url):
    """The count was written and never read: the risk rows are the count. A database that has the
    column loses it; one without it migrates all the same."""
    from alembic import command
    from alembic.config import Config

    from aisc_control_objectives import projectdb
    from aisc_control_objectives.db import tables

    assert "risks" not in tables.Graph.__table__.columns
    engine, drop = _scratch(database_url, "alembic_graph_risks")
    try:
        config = Config(str(projectdb.ALEMBIC_INI))
        config.set_main_option("script_location", str(projectdb.ALEMBIC_DIR))
        with engine.begin() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, PROFILE_NAME)
        with engine.connect() as connection:
            assert ("graph", "risks") in [c[:2] for c in _columns(connection)]
        projectdb.migrate(engine)
        with engine.connect() as connection:
            assert ("graph", "risks") not in [c[:2] for c in _columns(connection)]
        with engine.begin() as connection:
            connection.execute(text("UPDATE control_objectives.alembic_version SET version_num = :v"),
                               {"v": PROFILE_NAME})
        projectdb.migrate(engine)
    finally:
        drop()


def test_an_existing_assessment_gets_its_priority_objectives_stored(database_url, objectives, fixtures_dir):
    """The migration works out, by the service's own rule, which selected objectives of each existing
    assessment are priority, whether or not the database has the column already."""
    import json

    from aisc_control_objectives import projectdb
    from aisc_control_objectives.db.repository import ProjectRepository
    from aisc_control_objectives.projects import Projects
    from step2_support import FakeMapper

    engine, drop = _scratch(database_url, "alembic_priority")
    try:
        projectdb.migrate(engine)
        system = str(uuid.uuid4())
        with engine.begin() as connection:
            connection.execute(text("INSERT INTO project.system (pid, number, name) VALUES (:p, 1, 'MCAS')"),
                               {"p": system})
        projects = Projects(ProjectRepository(engine, pid=str(uuid.uuid4())), objectives, FakeMapper())
        graph = json.loads((fixtures_dir / "mcas.ontology.jsonld").read_text())
        a = projects.create(None, "MCAS", json.dumps(graph), graph, system).record.id
        projects.use_profile(a, "full-ai-act")
        projects.map_risks_of(a)
        projects.rate(a, {f"risk{n}": 5 for n in range(5)}, {f"risk{n}": 4 for n in range(5)})
        projects.set_keys(a, {"O9": True})
        view = projects.view(a)
        expected = [oid for oid in view.record.selected
                    if oid in {p.objective_id for p in view.priorities if p.key}]
        assert "O9" in expected and len(expected) > 1

        def priority_after_migrating() -> list[str]:
            with engine.begin() as connection:
                connection.execute(text("UPDATE control_objectives.alembic_version SET version_num = :v"),
                                   {"v": GRAPH_RISK_COUNT})
            projectdb.migrate(engine)
            with engine.connect() as connection:
                return connection.execute(text(
                    "SELECT priority_ids FROM control_objectives.objective_selection")).scalar()

        assert priority_after_migrating() == expected                 # the column is there already
        with engine.begin() as connection:
            connection.execute(text("ALTER TABLE control_objectives.objective_selection DROP COLUMN priority_ids"))
        assert priority_after_migrating() == expected                 # a database from before it
    finally:
        drop()


def test_the_providers_measures_leave_the_stored_assessments(database_url, fixtures_dir):
    """The risk loses its two copies of the provider's measure, and each stored card loses its
    RiskControl nodes and gets the digest of what is left, so the card qualification now serves, which
    has none, is the same card. A card that names no measure is left as it was, byte for byte; the
    migration runs again on a database that is already there."""
    import hashlib
    import json

    from alembic import command
    from alembic.config import Config

    from aisc_control_objectives import projectdb
    from aisc_control_objectives.db import tables
    from aisc_control_objectives.models.ontology import Ontology, same_card

    assert not {"control", "follow_up_control"} & set(tables.Risk.__table__.columns.keys())
    old_card = (fixtures_dir / "mcas.ontology.jsonld").read_text()
    plain_card = json.dumps([n for n in json.loads(old_card)
                             if "https://w3id.org/airo#RiskControl" not in (n.get("@type") or [])])
    engine, drop = _scratch(database_url, "alembic_measures")
    try:
        config = Config(str(projectdb.ALEMBIC_INI))
        config.set_main_option("script_location", str(projectdb.ALEMBIC_DIR))
        with engine.begin() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, PRIORITY)
        v1, v2 = str(uuid.uuid4()), str(uuid.uuid4())
        with engine.begin() as connection:
            connection.execute(text("INSERT INTO project.system (pid, number, name) VALUES (:a, 1, 'S'), (:b, 2, 'S')"),
                               {"a": v1, "b": v2})
            connection.execute(text(
                "INSERT INTO control_objectives.project (id, name, objectives_digest, created_at, updated_at, system_id)"
                " VALUES ('old', 'S', '', now(), now(), :a), ('plain', 'S', '', now(), now(), :b)"),
                {"a": v1, "b": v2})
            for assessment, card in (("old", old_card), ("plain", plain_card)):
                connection.execute(text(
                    "INSERT INTO control_objectives.graph (project_id, jsonld, digest, uploaded_at)"
                    " VALUES (:p, :j, :d, now())"),
                    {"p": assessment, "j": card, "d": hashlib.sha256(card.encode()).hexdigest()})
            connection.execute(text(
                "INSERT INTO control_objectives.risk (project_id, risk_id, position, text, short_label, source,"
                " vulnerability, consequence, impact, stakeholder, control, follow_up_control, areas, vair_terms,"
                " provenance) VALUES ('old', 'risk2', 2, 't', '', '', '', '', '', '', 'Officer override rates',"
                " 'Withdraw approval rights', '{}', '{}', 'form')"))
        projectdb.migrate(engine)
        with engine.connect() as connection:
            columns = [c[:2] for c in _columns(connection)]
            stored = {p: (j, d) for p, j, d in connection.execute(text(
                "SELECT project_id, jsonld, digest FROM control_objectives.graph")).all()}
            assert connection.execute(text("SELECT count(*) FROM control_objectives.risk")).scalar() == 1
        assert ("risk", "control") not in columns and ("risk", "follow_up_control") not in columns
        jsonld, digest = stored["old"]
        assert "RiskControl" not in jsonld and "Officer override rates" not in jsonld
        assert digest == hashlib.sha256(jsonld.encode()).hexdigest()
        assert same_card(jsonld, plain_card) and same_card(jsonld, old_card)
        assert Ontology.from_jsonld(json.loads(jsonld)) == Ontology.from_jsonld(json.loads(old_card))
        assert stored["plain"] == (plain_card, hashlib.sha256(plain_card.encode()).hexdigest())
        with engine.begin() as connection:
            connection.execute(text("UPDATE control_objectives.alembic_version SET version_num = :v"),
                               {"v": PRIORITY})
        projectdb.migrate(engine)
        with engine.connect() as connection:
            again = dict(connection.execute(text(
                "SELECT project_id, digest FROM control_objectives.graph")).all())
        assert again == {p: d for p, (_j, d) in stored.items()}
    finally:
        drop()
