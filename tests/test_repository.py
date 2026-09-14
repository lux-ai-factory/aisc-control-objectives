"""Projects, persisted.

Against a real Postgres, on the qualification app's blueprint: a table per
thing, the uploaded graph kept as the bytes that were uploaded, and everything
derivable left out so it cannot go stale.

The tests run in a transaction that is rolled back, so they are isolated
without truncating anything.
"""

from __future__ import annotations

import json

import pytest

from wizard.models.ontology import Ontology
from wizard.risk_mapping import Finding, MappedObjective, Mapping, MappingRun


@pytest.fixture()
def graph_json(fixtures_dir):
    return (fixtures_dir / "mcas.ontology.jsonld").read_text()


@pytest.fixture()
def ontology(graph_json):
    return Ontology.from_jsonld(json.loads(graph_json))


class TestCreatingAProject:
    def test_a_project_starts_from_an_uploaded_graph(self, repository, ontology, graph_json):
        project = repository.create(name="MCAS", ontology=ontology, jsonld=graph_json)
        assert project.id
        assert project.name == "MCAS"
        assert project.system_name.startswith("MicroCredit")
        assert project.qualification_id == ontology.qualification_id

    def test_the_graph_is_kept_as_the_bytes_that_were_uploaded(
        self, repository, ontology, graph_json
    ):
        """Their KnowledgeGraph rule: exports serve these bytes, so the file
        someone was given and the row are the same document."""
        project = repository.create(name="MCAS", ontology=ontology, jsonld=graph_json)
        stored = repository.get(project.id)
        assert stored.jsonld == graph_json

    def test_the_graph_has_an_identity(self, repository, ontology, graph_json):
        first = repository.create(name="one", ontology=ontology, jsonld=graph_json)
        second = repository.create(name="two", ontology=ontology, jsonld=graph_json)
        assert first.digest == second.digest
        assert len(first.digest) == 64

    def test_the_risks_become_rows(self, repository, ontology, graph_json):
        project = repository.create(name="MCAS", ontology=ontology, jsonld=graph_json)
        stored = repository.get(project.id)
        assert len(stored.ontology.risks) == 5
        risk = stored.ontology.by_id("risk2")
        assert risk.text.startswith("Loan officers rubber-stamp")
        assert risk.vair_terms          # the typing survives the round trip
        assert risk.areas == ["Fundamental rights", "Freedom"]


class TestSurvivingARestart:
    def test_everything_worth_money_comes_back(self, repository, ontology, graph_json):
        """The mapping cost five model calls; losing it to a restart means
        paying again for a slightly different answer. The ranking is worth
        more still: a person made it."""
        project = repository.create(name="MCAS", ontology=ontology, jsonld=graph_json)
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

    def test_a_run_that_struggled_keeps_its_findings(self, repository, ontology, graph_json):
        project = repository.create(name="MCAS", ontology=ontology, jsonld=graph_json)
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

    def test_a_second_run_replaces_the_first(self, repository, ontology, graph_json):
        """Re-mapping buys a new answer; keeping both would leave the page
        showing objectives the latest run did not claim."""
        project = repository.create(name="MCAS", ontology=ontology, jsonld=graph_json)
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
    def test_projects_list_newest_first(self, repository, ontology, graph_json):
        first = repository.create(name="one", ontology=ontology, jsonld=graph_json)
        second = repository.create(name="two", ontology=ontology, jsonld=graph_json)
        listed = [p.id for p in repository.list()]
        assert listed.index(second.id) < listed.index(first.id)

    def test_a_re_upload_replaces_the_graph_and_keeps_the_project(
        self, repository, ontology, graph_json
    ):
        project = repository.create(name="MCAS", ontology=ontology, jsonld=graph_json)
        repository.rate(project.id, {"risk2": 5, "risk4": 4})
        smaller_raw = [n for n in json.loads(graph_json) if not str(n.get("@id", "")).endswith("risk4")]
        smaller = Ontology.from_jsonld(smaller_raw)
        repository.replace_ontology(project.id, ontology=smaller, jsonld=json.dumps(smaller_raw))

        stored = repository.reopened().get(project.id)
        assert stored.id == project.id
        assert len(stored.ontology.risks) == 4
        # the rating for a risk the new graph does not have goes with it
        assert stored.severity.ratings == {"risk2": 5}

    def test_deleting_a_project_takes_its_rows_with_it(self, repository, ontology, graph_json):
        project = repository.create(name="MCAS", ontology=ontology, jsonld=graph_json)
        repository.rate(project.id, {"risk2": 5})
        repository.delete(project.id)
        assert repository.get(project.id) is None
        assert repository.orphan_rows() == 0      # the cascade actually cascades

    def test_an_unknown_project_is_none_rather_than_an_error(self, repository):
        assert repository.get("nope") is None


class TestTheCatalogueStamp:
    def test_a_project_records_which_catalogue_it_was_assessed_against(
        self, repository, ontology, graph_json, objectives
    ):
        """If the CSV is re-exported, an old project's tiers would silently
        change. The stamp is what lets the page say so."""
        project = repository.create(name="MCAS", ontology=ontology, jsonld=graph_json)
        assert project.objectives_digest == objectives.digest
        assert len(objectives.digest) == 64
