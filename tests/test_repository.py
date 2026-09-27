"""Projects, persisted.

Against a real Postgres, on the qualification app's blueprint: a table per
thing, the uploaded graph kept as the bytes that were uploaded, and everything
derivable left out so it cannot go stale.

The tests run in a transaction that is rolled back, so they are isolated
without truncating anything.

(Rewritten for WP7, pipeline 2026-09-23: every assessment is of one saved card
version, `system_id`, one version per assessment. The replaced-card case was
about the removed upload route.)
"""

from __future__ import annotations

import json

import pytest

from aisc_control_objectives.models.ontology import Ontology
from aisc_control_objectives.risk_mapping import Finding, MappedObjective, Mapping, MappingRun


@pytest.fixture()
def graph_json(fixtures_dir):
    return (fixtures_dir / "mcas.ontology.jsonld").read_text()


@pytest.fixture()
def ontology(graph_json):
    return Ontology.from_jsonld(json.loads(graph_json))


class TestCreatingAProject:
    def test_a_project_starts_from_an_uploaded_graph(self, system_version, repository, platform_project, ontology, graph_json):
        project = repository.create(project=platform_project, name="MCAS", ontology=ontology, jsonld=graph_json, system_id=system_version(platform_project, 1))
        assert project.id
        assert project.name == "MCAS"
        assert project.system_name.startswith("MicroCredit")
        assert project.qualification_id == ontology.qualification_id

    def test_the_graph_is_kept_as_the_bytes_that_were_uploaded(
        self, system_version, repository, platform_project, ontology, graph_json
    ):
        """Their KnowledgeGraph rule: exports serve these bytes, so the file
        someone was given and the row are the same document."""
        project = repository.create(project=platform_project, name="MCAS", ontology=ontology, jsonld=graph_json, system_id=system_version(platform_project, 1))
        stored = repository.get(project.id)
        assert stored.jsonld == graph_json

    def test_the_graph_has_an_identity(self, system_version, repository, platform_project, ontology, graph_json):
        first = repository.create(project=platform_project, name="one", ontology=ontology, jsonld=graph_json, system_id=system_version(platform_project, 1))
        second = repository.create(project=platform_project, name="two", ontology=ontology, jsonld=graph_json, system_id=system_version(platform_project, 2))
        assert first.digest == second.digest
        assert len(first.digest) == 64

    def test_the_risks_become_rows(self, system_version, repository, platform_project, ontology, graph_json):
        project = repository.create(project=platform_project, name="MCAS", ontology=ontology, jsonld=graph_json, system_id=system_version(platform_project, 1))
        stored = repository.get(project.id)
        assert len(stored.ontology.risks) == 5
        risk = stored.ontology.by_id("risk2")
        assert risk.text.startswith("Loan officers rubber-stamp")
        assert risk.vair_terms          # the typing survives the round trip
        assert risk.areas == ["Fundamental rights", "Freedom"]


class TestSurvivingARestart:
    def test_everything_worth_money_comes_back(self, system_version, repository, platform_project, ontology, graph_json):
        """The mapping cost five model calls; losing it to a restart means
        paying again for a slightly different answer. The ranking is worth
        more still: a person made it."""
        project = repository.create(project=platform_project, name="MCAS", ontology=ontology, jsonld=graph_json, system_id=system_version(platform_project, 1))
        repository.save_mapping_run(
            project.id,
            MappingRun(
                mappings={
                    "risk2": Mapping(
                        risk_id="risk2",
                        objectives=[MappedObjective(objective_id="R1.1", quote="q", rationale="why")],
                    )
                },
                stop="clean",
                attempts=1,
            ),
            model="openai/gpt-4o",
        )
        repository.rate(project.id, {"risk2": 5, "risk4": 1})

        # a fresh repository, as a restart would give
        stored = repository.reopened().get(project.id)
        assert stored.mapping_run.mappings["risk2"].objectives[0].objective_id == "R1.1"
        assert stored.mapping_run.model == "openai/gpt-4o"
        assert stored.severity.ratings == {"risk2": 5, "risk4": 1}

    def test_a_run_that_struggled_keeps_its_findings(self, system_version, repository, platform_project, ontology, graph_json):
        project = repository.create(project=platform_project, name="MCAS", ontology=ontology, jsonld=graph_json, system_id=system_version(platform_project, 1))
        repository.save_mapping_run(
            project.id,
            MappingRun(
                mappings={"risk2": Mapping(risk_id="risk2", stop="cap")},
                findings=[Finding(risk_id="risk2", flag="quote-not-in-risk", detail="x")],
                stop="cap",
                attempts=3,
            ),
        )
        stored = repository.reopened().get(project.id)
        assert stored.mapping_run.stop == "cap"
        assert [f.flag for f in stored.mapping_run.findings] == ["quote-not-in-risk"]

    def test_a_second_run_replaces_the_first(self, system_version, repository, platform_project, ontology, graph_json):
        """Re-mapping buys a new answer; keeping both would leave the page
        showing objectives the latest run did not claim."""
        project = repository.create(project=platform_project, name="MCAS", ontology=ontology, jsonld=graph_json, system_id=system_version(platform_project, 1))
        for objective_id in ("R1.1", "R2.3"):
            repository.save_mapping_run(
                project.id,
                MappingRun(mappings={"risk2": Mapping(
                    risk_id="risk2",
                    objectives=[MappedObjective(objective_id=objective_id, quote="q", rationale="w")],
                )}),
            )
        stored = repository.reopened().get(project.id)
        claimed = [o.objective_id for o in stored.mapping_run.mappings["risk2"].objectives]
        assert claimed == ["R2.3"]


class TestListingAndReplacing:
    def test_projects_list_newest_first(self, system_version, repository, platform_project, ontology, graph_json):
        first = repository.create(project=platform_project, name="one", ontology=ontology, jsonld=graph_json, system_id=system_version(platform_project, 1))
        second = repository.create(project=platform_project, name="two", ontology=ontology, jsonld=graph_json, system_id=system_version(platform_project, 2))
        listed = [p.id for p in repository.list()]
        assert listed.index(second.id) < listed.index(first.id)

    def test_deleting_a_project_takes_its_rows_with_it(self, system_version, repository, platform_project, ontology, graph_json):
        project = repository.create(project=platform_project, name="MCAS", ontology=ontology, jsonld=graph_json, system_id=system_version(platform_project, 1))
        repository.rate(project.id, {"risk2": 5})
        repository.delete(project.id)
        assert repository.get(project.id) is None
        assert repository.orphan_rows() == 0      # the cascade actually cascades

    def test_an_unknown_project_is_none_rather_than_an_error(self, repository, platform_project):
        assert repository.get("nope") is None


class TestTheCatalogueStamp:
    def test_a_project_records_which_catalogue_it_was_assessed_against(
        self, system_version, repository, platform_project, ontology, graph_json, objectives
    ):
        """If the CSV is re-exported, an old project's tiers would silently
        change. The stamp is what lets the page say so."""
        project = repository.create(project=platform_project, name="MCAS", ontology=ontology, jsonld=graph_json, system_id=system_version(platform_project, 1))
        assert project.objectives_digest == objectives.digest
        assert len(objectives.digest) == 64


class TestOneDatabase:
    """This service owns a schema in its project's database, and the database
    is the project: rows carry no project of their own (isolation, I1.7), and
    an assessment's card version must be a row of that database's
    project.system (I5.3)."""

    def test_its_tables_are_in_its_own_schema(self, repository, platform_project):
        from sqlalchemy import inspect

        inspector = inspect(repository._engine)
        assert set(inspector.get_table_names(schema="control_objectives")) >= {
            "project", "graph", "risk", "mapped_objective", "mapping_run",
        }
        # and nothing of this service's is left in the database's front room
        assert not set(inspector.get_table_names(schema="public")) & {"project", "risk"}

    def test_an_assessment_belongs_to_a_project_on_the_platform(
        self, system_version, repository, platform_project, ontology, graph_json
    ):
        created = repository.create(
            project=platform_project, name="MCAS", ontology=ontology, jsonld=graph_json, system_id=system_version(platform_project, 1))
        assert created.project == platform_project
        assert repository.get(created.id).project == platform_project

    def test_the_database_refuses_an_assessment_of_a_version_absent_from_project_system(
        self, repository, platform_project, ontology, graph_json
    ):
        """(Isolation, S-D13: replaces "refuses an assessment of a project that
        does not exist"; the assessment has no project column any more, the
        key that remains is system_id into project.system.)"""
        import uuid

        from sqlalchemy.exc import IntegrityError

        with pytest.raises(IntegrityError):
            repository.create(
                project=platform_project, name="nobody's", ontology=ontology, jsonld=graph_json,
                system_id=str(uuid.uuid4()),
            )
