"""A project, end to end: upload the AI Card · rank the risks · map · tiers.

Driven through the HTTP surface against a real database, with a fake mapper,
so what is tested is the flow rather than a model's judgement.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from wizard.api.app import create_app
from wizard.config import RunConfig
from wizard.projects import Projects
from wizard.risk_mapping import MappedObjective, Mapping


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
    projects = Projects(repository, objectives, FakeMapper(), model="fake/model")
    return TestClient(create_app(objectives, projects, base_config=RunConfig()))


@pytest.fixture()
def graph(fixtures_dir):
    return json.loads((fixtures_dir / "mcas.ontology.jsonld").read_text())


def _start(client, graph, name="MCAS") -> dict:
    response = client.post(f"/api/projects?name={name}", json=graph)
    assert response.status_code == 201, response.text
    return response.json()


class TestStartingAProject:
    def test_an_upload_brings_in_the_cards_risks(self, client, graph):
        project = _start(client, graph)
        assert project["system_name"].startswith("MicroCredit")
        assert [risk["id"] for risk in project["risks"]] == [f"risk{n}" for n in range(5)]
        assert project["mapping_run"] is None      # nothing agentic has run yet

    def test_the_tiers_exist_before_anything_is_mapped(self, client, graph):
        """All 50 are owed from the first page load; they are simply not
        ordered by anything yet."""
        project = _start(client, graph)
        assert len(project["priorities"]) == 50
        assert all(p["tier"] == 3 for p in project["priorities"])

    def test_what_is_not_an_ai_card_is_refused(self, client):
        for body in ({"hello": "world"}, "a string", [1, 2, 3]):
            response = client.post("/api/projects", json=body)
            assert response.status_code == 422, body
            # the message says what is wanted and where to get it
            assert "AI Card" in response.text
            assert "ai-card.json" in response.text

    def test_an_ai_card_json_is_accepted(self, client, graph):
        """The export that wraps the graph with the form's facts, which is what
        the qualification app's Download JSON gives you."""
        card = {
            "system_name": "MicroCredit Assist Score (MCAS)",
            "qualification_id": "cmtvvrnw50000jzyvas8gmn3u",
            "ontology": {"chains": [], "rows": []},
            "ontology_graph": graph,
        }
        project = client.post("/api/projects?name=from+card", json=card).json()
        assert len(project["risks"]) == 5

    def test_a_project_survives_a_restart(self, client, graph):
        project = _start(client, graph)
        again = client.get(f"/api/projects/{project['id']}").json()
        assert again["digest"] == project["digest"]
        assert len(again["risks"]) == 5

    def test_the_project_is_listed(self, client, graph):
        project = _start(client, graph)
        listed = client.get("/api/projects").json()
        assert project["id"] in {p["id"] for p in listed}


class TestRankingTheRisks:
    def test_ranking_needs_nothing_but_the_card(self, client, graph):
        """The point of dropping the first workflow: an assessor can rank the
        moment the card is in, with no model call in between."""
        project = _start(client, graph)
        rated = client.post(
            f"/api/projects/{project['id']}/severity", json={"risk2": 5, "risk4": 1}
        )
        assert rated.status_code == 200
        assert rated.json()["severity"]["ratings"] == {"risk2": 5, "risk4": 1}

    def test_a_rating_for_a_risk_this_card_lacks_is_refused(self, client, graph):
        project = _start(client, graph)
        response = client.post(
            f"/api/projects/{project['id']}/severity", json={"risk99": 5}
        )
        assert response.status_code == 422
        assert "risk99" in response.text

    def test_the_ratings_survive_a_reload(self, client, graph):
        project = _start(client, graph)
        client.post(f"/api/projects/{project['id']}/severity", json={"risk2": 5})
        again = client.get(f"/api/projects/{project['id']}").json()
        assert again["severity"]["ratings"]["risk2"] == 5


class TestMappingAndTiers:
    @pytest.fixture()
    def mapped(self, client, graph):
        project = _start(client, graph)
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

    def test_a_corrected_card_keeps_the_ranking_and_drops_the_mapping(
        self, client, mapped, graph
    ):
        """The mapping was bought against the card being replaced; the ranking
        is the assessor's, and every risk it names is still on the new card."""
        client.post(f"/api/projects/{mapped['id']}/severity", json={"risk2": 5})
        again = client.post(f"/api/projects/{mapped['id']}/card", json=graph).json()
        assert again["severity"]["ratings"] == {"risk2": 5}
        assert again["mapping_run"] is None


class TestTheHomepage:
    def test_the_root_leads_to_both_halves(self, client):
        page = client.get("/").text
        assert "/objectives" in page
        assert "/projects" in page
        assert "50" in page                       # what is behind each door
        assert page.count('class="co-obj"') == 0  # it is a way in, not the catalogue

    def test_it_counts_the_systems_under_assessment(self, client, graph):
        assert "No systems under assessment yet" in client.get("/").text
        _start(client, graph)
        assert "1 system under assessment" in client.get("/").text

    def test_every_page_carries_the_navigation(self, client, graph):
        project = _start(client, graph)
        for path in ("/", "/objectives", "/projects", f"/projects/{project['id']}"):
            page = client.get(path).text
            assert 'href="/objectives"' in page, path
            assert 'href="/projects"' in page, path

    def test_the_current_page_is_marked_in_the_navigation(self, client):
        assert 'class="nav-here"' in client.get("/objectives").text
        assert 'class="nav-here"' in client.get("/projects").text


class TestThePages:
    def test_the_objectives_page_is_the_catalogue(self, client):
        page = client.get("/objectives").text
        assert page.count('class="co-obj"') == 50

    def test_the_projects_page_lists_them(self, client, graph):
        _start(client, graph, name="MCAS pre-market")
        page = client.get("/projects").text
        assert "MCAS pre-market" in page
        assert "Awaiting your ranking" in page

    def test_the_project_page_opens_on_the_risks(self, client, graph):
        """No questions, no gate: the card's risks and a rating for each."""
        project = _start(client, graph)
        page = client.get(f"/projects/{project['id']}").text
        assert "Rank the risks on the card" in page
        assert "Loan officers rubber-stamp" in page          # the risk itself
        assert "Overreliance" in page                        # its VAIR typing
        assert page.count("<select") == 5                    # one per risk
        assert "Map the risks to objectives" in page

    def test_nothing_asks_the_three_questions(self, client, graph):
        """"Annex III" itself still occurs: it is in the objectives' own text.
        What must be gone are the question form and its answers."""
        project = _start(client, graph)
        page = client.get(f"/projects/{project['id']}").text
        for gone in ("the three questions", 'type="radio"', "/answer", "Confirm"):
            assert gone not in page, gone

    def test_ranking_from_the_page_re_tiers_it(self, client, graph):
        project = _start(client, graph)
        client.post(f"/projects/{project['id']}/map", follow_redirects=False)
        client.post(
            f"/projects/{project['id']}/severity",
            data={f"risk{n}": "5" if n == 2 else "1" for n in range(5)},
            follow_redirects=False,
        )
        page = client.get(f"/projects/{project['id']}").text
        assert 'qf-tag--tier1">Tier 1</span>' in page
        assert page.index("Start here") < page.index("Later")

    def test_a_rating_that_is_not_a_number_is_refused(self, client, graph):
        project = _start(client, graph)
        response = client.post(
            f"/projects/{project['id']}/severity", data={"risk2": "high"}
        )
        assert response.status_code == 400
