"""A project, end to end: upload · three questions · Confirm/Refuse · map · tiers.

Driven through the HTTP surface against a real database, with fake models, so
what is tested is the flow and its gate rather than the models' judgement.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from wizard.api.app import create_app
from wizard.config import RunConfig
from wizard.models.profile import Fact, HighRiskFact, Profile
from wizard.projects import Projects
from wizard.risk_mapping import MappedObjective, Mapping

CREDIT = "Evaluates creditworthiness"
USERS = "bank customers aged 18+"
LLM = "Hosted explanation LLM"


class FakeExtractor:
    """Reads the graph the way a competent model would, quoting real spans."""

    def propose(self, ontology, findings=()):
        return Profile(
            high_risk=HighRiskFact(value="yes", quote=CREDIT, source="hasPurpose",
                                   annex_iii_point="5(b)", rationale="creditworthiness of natural persons"),
            personal_data=Fact(value="yes", quote=USERS, source="hasAIUser", rationale="applicants"),
            interacts_with_natural_persons=Fact(value="undetermined",
                                                rationale="the graph does not say who sees the chatbot"),
        )


class FakeMapper:
    BY_RISK = {
        "risk0": ["R3.1", "R5.1", "R2.5"],
        "risk1": ["R5.1", "R5.2", "R5.3"],
        "risk2": ["R1.1", "R1.4", "R1.2"],
        "risk3": ["R4.1", "R4.3", "R2.2"],
        "risk4": ["R2.3", "R3.6", "R3.1"],
    }

    def propose(self, risk, findings=()):
        quote = risk.text[:30]
        return Mapping(
            risk_id=risk.id,
            objectives=[
                MappedObjective(objective_id=oid, quote=quote, rationale="because")
                for oid in self.BY_RISK.get(risk.id, [])
            ],
        )


@pytest.fixture()
def client(repository, objectives):
    projects = Projects(repository, objectives, FakeExtractor(), FakeMapper(), model="fake/model")
    return TestClient(create_app(objectives, projects, base_config=RunConfig()))


@pytest.fixture()
def graph(fixtures_dir):
    return json.loads((fixtures_dir / "mcas.ontology.jsonld").read_text())


def _start(client, graph, name="MCAS") -> dict:
    response = client.post(f"/api/projects?name={name}", json=graph)
    assert response.status_code == 201, response.text
    return response.json()


class TestStartingAProject:
    def test_an_upload_runs_the_first_workflow(self, client, graph):
        project = _start(client, graph)
        assert project["system_name"].startswith("MicroCredit")
        assert len(project["risks"]) == 5
        assert project["profile_run"]["stop"] == "clean"
        assert project["profile_run"]["profile"]["high_risk"]["quote"] == CREDIT

    def test_what_is_not_a_graph_is_refused(self, client):
        for body in ({"hello": "world"}, "a string", [1, 2, 3]):
            assert client.post("/api/projects", json=body).status_code == 422

    def test_a_project_survives_a_restart(self, client, graph):
        project = _start(client, graph)
        again = client.get(f"/api/projects/{project['id']}").json()
        assert again["profile_run"]["profile"]["high_risk"]["annex_iii_point"] == "5(b)"

    def test_the_project_is_listed(self, client, graph):
        project = _start(client, graph)
        listed = client.get("/api/projects").json()
        assert project["id"] in {p["id"] for p in listed}


class TestTheGate:
    def test_the_second_workflow_will_not_run_before_the_answer(self, client, graph):
        project = _start(client, graph)
        assert project["can_map"] is False
        response = client.post(f"/api/projects/{project['id']}/map")
        assert response.status_code == 409
        assert "answer the three questions" in response.text

    def test_answering_opens_it(self, client, graph):
        project = _start(client, graph)
        answered = client.post(
            f"/api/projects/{project['id']}/answer",
            json={"high_risk": True, "personal_data": True,
                  "interacts_with_natural_persons": False},
        ).json()
        assert answered["can_map"] is True
        assert client.post(f"/api/projects/{project['id']}/map").status_code == 200


class TestTheCompanyHasTheFinalWord:
    def test_a_refusal_overrides_the_model(self, client, graph):
        project = _start(client, graph)
        answered = client.post(
            f"/api/projects/{project['id']}/answer",
            json={"high_risk": False, "personal_data": False,
                  "interacts_with_natural_persons": False},
        ).json()
        # the model said high-risk; the company said no, so nothing but the
        # voluntary pair is owed
        applying = {v["objective_id"] for v in answered["verdicts"] if v["applies"] == "yes"}
        assert applying == {"R6.1", "R6.2"}

    def test_the_models_proposal_survives_the_refusal(self, client, graph):
        project = _start(client, graph)
        answered = client.post(
            f"/api/projects/{project['id']}/answer",
            json={"high_risk": False, "personal_data": False,
                  "interacts_with_natural_persons": False},
        ).json()
        assert answered["profile_run"]["profile"]["high_risk"]["value"] == "yes"
        assert answered["answer"]["high_risk"] is False

    def test_an_undetermined_fact_is_settled_by_the_company(self, client, graph):
        """The model was unsure about Art. 50; the company says yes, and R4.4
        is owed."""
        project = _start(client, graph)
        assert project["profile_run"]["profile"]["interacts_with_natural_persons"]["value"] == "undetermined"
        answered = client.post(
            f"/api/projects/{project['id']}/answer",
            json={"high_risk": True, "personal_data": True,
                  "interacts_with_natural_persons": True},
        ).json()
        by_id = {v["objective_id"]: v for v in answered["verdicts"]}
        assert by_id["R4.4"]["applies"] == "yes"

    def test_verdicts_stop_being_provisional_once_answered(self, client, graph):
        project = _start(client, graph)
        assert all(v["provisional"] for v in project["verdicts"] if not v["non_binding"])
        answered = client.post(
            f"/api/projects/{project['id']}/answer",
            json={"high_risk": True, "personal_data": True,
                  "interacts_with_natural_persons": True},
        ).json()
        assert not any(v["provisional"] for v in answered["verdicts"])


class TestMappingAndTiers:
    @pytest.fixture()
    def mapped(self, client, graph):
        project = _start(client, graph)
        client.post(
            f"/api/projects/{project['id']}/answer",
            json={"high_risk": True, "personal_data": True,
                  "interacts_with_natural_persons": True},
        )
        return client.post(f"/api/projects/{project['id']}/map").json()

    def test_every_risk_is_mapped(self, mapped):
        assert set(mapped["mapping_run"]["mappings"]) == {f"risk{n}" for n in range(5)}
        assert mapped["mapping_run"]["model"] == "fake/model"

    def test_rating_the_risks_moves_the_tiers(self, client, mapped):
        oversight = client.post(
            f"/api/projects/{mapped['id']}/severity",
            json={"risk2": 5, "risk1": 5, "risk4": 1, "risk0": 1, "risk3": 1},
        ).json()
        poisoning = client.post(
            f"/api/projects/{mapped['id']}/severity",
            json={"risk2": 1, "risk1": 1, "risk4": 5, "risk3": 5, "risk0": 1},
        ).json()
        first = {p["objective_id"] for p in oversight["priorities"] if p["tier"] == 1}
        second = {p["objective_id"] for p in poisoning["priorities"] if p["tier"] == 1}
        assert "R1.1" in first and "R1.1" not in second
        assert "R2.3" in second and "R2.3" not in first

    def test_tier_one_never_exceeds_seven(self, client, mapped):
        rated = client.post(
            f"/api/projects/{mapped['id']}/severity",
            json={f"risk{n}": 5 for n in range(5)},
        ).json()
        assert sum(p["tier"] == 1 for p in rated["priorities"]) <= 7

    def test_a_rating_for_a_risk_this_graph_lacks_is_refused(self, client, mapped):
        response = client.post(
            f"/api/projects/{mapped['id']}/severity", json={"risk99": 5}
        )
        assert response.status_code == 422
        assert "risk99" in response.text

    def test_the_ratings_survive_a_reload(self, client, mapped):
        client.post(f"/api/projects/{mapped['id']}/severity", json={"risk2": 5})
        again = client.get(f"/api/projects/{mapped['id']}").json()
        assert again["severity"]["ratings"]["risk2"] == 5


class TestThePages:
    def test_the_objectives_page_is_the_catalogue(self, client):
        page = client.get("/").text
        assert page.count('class="co-obj"') == 50
        assert "projects" in page.lower()

    def test_the_projects_page_lists_them(self, client, graph):
        _start(client, graph, name="MCAS pre-market")
        page = client.get("/projects").text
        assert "MCAS pre-market" in page
        assert "Awaiting your answer" in page

    def test_the_project_page_asks_the_three_questions_with_their_quotes(self, client, graph):
        project = _start(client, graph)
        page = client.get(f"/projects/{project['id']}").text
        assert "Annex III: the three questions" in page
        assert CREDIT in page                       # the quote behind high-risk
        assert "Annex III point 5(b)" in page
        assert "The model could not tell" in page   # the undetermined one
        assert page.count('type="radio"') == 6      # yes/no for each of three

    def test_the_project_page_will_not_offer_mapping_before_the_answer(self, client, graph):
        project = _start(client, graph)
        page = client.get(f"/projects/{project['id']}").text
        assert "Map the risks to objectives" not in page
        assert "Answer the three questions first" in page

    def test_after_answering_the_page_offers_the_second_workflow(self, client, graph):
        project = _start(client, graph)
        client.post(
            f"/projects/{project['id']}/answer",
            data={"high_risk": "yes", "personal_data": "yes",
                  "interacts_with_natural_persons": "no"},
            follow_redirects=False,
        )
        page = client.get(f"/projects/{project['id']}").text
        assert "Map the risks to objectives" in page

    def test_the_finished_page_shows_the_risks_and_the_tiers(self, client, graph):
        project = _start(client, graph)
        client.post(f"/projects/{project['id']}/answer",
                    data={"high_risk": "yes", "personal_data": "yes",
                          "interacts_with_natural_persons": "yes"},
                    follow_redirects=False)
        client.post(f"/projects/{project['id']}/map", follow_redirects=False)
        page = client.get(f"/projects/{project['id']}").text
        assert "Loan officers rubber-stamp" in page          # the risk
        assert "Overreliance" in page                        # its VAIR typing
        assert 'qf-tag--tier1">Tier 1</span>' in page
        assert page.index("Start here") < page.index("Later")

    def test_an_unanswered_form_is_refused(self, client, graph):
        project = _start(client, graph)
        response = client.post(f"/projects/{project['id']}/answer",
                               data={"high_risk": "yes"})
        assert response.status_code == 400
