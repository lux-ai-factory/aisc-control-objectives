"""An assessment, end to end: the AI Card, rate the risks, map, key objectives.

Driven through the HTTP surface against a real database, with a fake mapper,
so what is tested is the flow rather than a model's judgement. An assessment is
of one saved card version, so each one is made with
`Projects.create(..., system_id)` on a version of its own; how an assessment is
started is tested in test_assessment_of_a_version.py.
"""

from __future__ import annotations

import re

import json

import pytest
from fastapi.testclient import TestClient

from aisc_control_objectives.api.app import create_app
from aisc_control_objectives.config import RunConfig
from aisc_control_objectives.projects import Projects
from aisc_control_objectives.risk_mapping import MappedObjective, Mapping


class FakeMapper:
    BY_RISK = {
        "risk0": ["O11", "O21", "O9"],
        "risk1": ["O21", "O22", "O23"],
        "risk2": ["O1", "O4", "O2"],
        "risk3": ["O17", "O19", "O6"],
        "risk4": ["O7", "O16", "O11"],
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
def projects(repository, objectives):
    return Projects(repository, objectives, FakeMapper(), model="fake/model")


@pytest.fixture()
def client(projects, objectives):
    return TestClient(create_app(objectives, projects, base_config=RunConfig()))


@pytest.fixture()
def graph(fixtures_dir):
    return json.loads((fixtures_dir / "mcas.ontology.jsonld").read_text())


@pytest.fixture()
def versions(system_version):
    """The next card version of a project, numbered 1, 2, ...: one per assessment."""
    made: dict[str, int] = {}

    def next_of(project: str) -> str:
        made[project] = made.get(project, 0) + 1
        return system_version(project, made[project])

    return next_of


@pytest.fixture()
def _starter(projects, versions):
    return projects, versions


def _start(client, graph, project, name="MCAS", *, starter) -> dict:
    """An assessment of the project's next card version, read back through the API."""
    projects, versions = starter
    view = projects.create(project, name, json.dumps(graph), graph, versions(project))
    response = client.get(f"/p/{project}/api/projects/{view.record.id}")
    assert response.status_code == 200, response.text
    return response.json()


class TestStartingAProject:
    def test_an_assessment_brings_in_the_cards_risks(self, _starter, platform_project, client, graph):
        project = _start(client, graph, platform_project, starter=_starter)
        assert project["system_name"].startswith("MicroCredit")
        assert [risk["id"] for risk in project["risks"]] == [f"risk{n}" for n in range(5)]
        assert project["mapping_run"] is None      # nothing agentic has run yet

    def test_nothing_is_key_before_anything_is_mapped(self, _starter, platform_project, client, graph):
        """Every objective is owed from the first page load; none is key until a risk drives it."""
        project = _start(client, graph, platform_project, starter=_starter)
        assert len(project["priorities"]) == 50
        assert not any(p["key"] for p in project["priorities"])

    def test_a_project_survives_a_restart(self, _starter, platform_project, client, graph):
        project = _start(client, graph, platform_project, starter=_starter)
        again = client.get(f"/p/{platform_project}/api/projects/{project['id']}").json()
        assert again["digest"] == project["digest"]
        assert len(again["risks"]) == 5

    def test_the_project_is_listed(self, _starter, platform_project, client, graph):
        project = _start(client, graph, platform_project, starter=_starter)
        listed = client.get(f"/p/{platform_project}/api/projects").json()
        assert project["id"] in {p["id"] for p in listed}


class TestRankingTheRisks:
    def test_ranking_needs_nothing_but_the_card(self, _starter, platform_project, client, graph):
        """An assessor can rank the moment the card is in, with no model call
        in between."""
        project = _start(client, graph, platform_project, starter=_starter)
        rated = client.post(
            f"/p/{platform_project}/api/projects/{project['id']}/severity", json={"risk2": 5, "risk4": 1}
        )
        assert rated.status_code == 200
        # the /severity route sets the impact only
        assert rated.json()["severity"]["impact"] == {"risk2": 5, "risk4": 1}

    def test_a_rating_for_a_risk_this_card_lacks_is_refused(self, _starter, platform_project, client, graph):
        project = _start(client, graph, platform_project, starter=_starter)
        response = client.post(
            f"/p/{platform_project}/api/projects/{project['id']}/severity", json={"risk99": 5}
        )
        assert response.status_code == 422
        assert "risk99" in response.text

    def test_the_ratings_survive_a_reload(self, _starter, platform_project, client, graph):
        project = _start(client, graph, platform_project, starter=_starter)
        client.post(f"/p/{platform_project}/api/projects/{project['id']}/severity", json={"risk2": 5})
        again = client.get(f"/p/{platform_project}/api/projects/{project['id']}").json()
        assert again["severity"]["impact"]["risk2"] == 5


class TestMappingAndKeys:
    @pytest.fixture()
    def mapped(self, _starter, platform_project, client, graph):
        project = _start(client, graph, platform_project, starter=_starter)
        return client.post(f"/p/{platform_project}/api/projects/{project['id']}/map").json()

    def test_every_risk_is_mapped(self, platform_project, mapped):
        assert set(mapped["mapping_run"]["mappings"]) == {f"risk{n}" for n in range(5)}
        assert mapped["mapping_run"]["model"] == "fake/model"

    def test_rating_the_risks_moves_the_key_objectives(self, platform_project, client, mapped):
        oversight = client.post(
            f"/p/{platform_project}/api/projects/{mapped['id']}/severity",
            json={"risk2": 5, "risk1": 5, "risk4": 1, "risk0": 1, "risk3": 1},
        ).json()
        poisoning = client.post(
            f"/p/{platform_project}/api/projects/{mapped['id']}/severity",
            json={"risk2": 1, "risk1": 1, "risk4": 5, "risk3": 5, "risk0": 1},
        ).json()
        first = {p["objective_id"] for p in oversight["priorities"] if p["key"]}
        second = {p["objective_id"] for p in poisoning["priorities"] if p["key"]}
        assert "O1" in first and "O1" not in second
        assert "O7" in second and "O7" not in first

    def test_no_more_than_seven_are_key_by_default(self, platform_project, client, mapped):
        rated = client.post(
            f"/p/{platform_project}/api/projects/{mapped['id']}/severity",
            json={f"risk{n}": 5 for n in range(5)},
        ).json()
        assert sum(p["key"] for p in rated["priorities"]) <= 7

class TestTheHomepage:
    def test_the_root_leads_to_both_halves(self, platform_project, client):
        page = client.get(f"/p/{platform_project}").text
        assert "/objectives" in page
        assert "/projects" in page
        assert "50" in page                       # what is behind each door
        assert page.count('class="co-obj"') == 0  # it is a way in, not the catalogue

    def test_the_logo_links_to_the_project_on_the_launcher(self, platform_project, client):
        """Like the qualification app's: inside a project the mark goes back
        to that project's page, where the other five steps are."""
        page = client.get(f"/p/{platform_project}/objectives").text
        assert f'<a class="brand" href="http://localhost:8100/p/{platform_project}">' in page

    def test_it_counts_the_systems_under_assessment(self, _starter, platform_project, client, graph):
        assert "No systems under assessment yet" in client.get(f"/p/{platform_project}").text
        _start(client, graph, platform_project, starter=_starter)
        assert "1 system under assessment" in client.get(f"/p/{platform_project}").text

    def test_every_page_carries_the_navigation(self, _starter, platform_project, client, graph):
        project = _start(client, graph, platform_project, starter=_starter)
        base = f"/p/{platform_project}"
        # Inside a project every page links on within it, so navigating never
        # loses which project is being assessed.
        for path in (base, f"{base}/objectives", f"{base}/projects",
                     f"{base}/projects/{project['id']}"):
            page = client.get(path).text
            assert f'href="{base}/objectives"' in page, path
            assert f'href="{base}/projects"' in page, path
        # and outside one, the catalogue still reads on its own
        page = client.get("/objectives").text
        assert 'href="/objectives"' in page
        assert 'href="/projects"' not in page

    def test_the_current_page_is_marked_in_the_navigation(self, platform_project, client):
        assert 'class="nav-here"' in client.get("/objectives").text
        assert 'class="nav-here"' in client.get(f"/p/{platform_project}/projects").text


class TestThePages:
    def test_the_objectives_page_is_the_catalogue(self, platform_project, client):
        page = client.get("/objectives").text
        assert page.count('class="co-obj"') == 50

    def test_the_projects_page_lists_them(self, _starter, platform_project, client, graph):
        _start(client, graph, platform_project, name="MCAS pre-market", starter=_starter)
        page = client.get(f"/p/{platform_project}/projects").text
        assert "MCAS pre-market" in page
        assert "Awaiting your ranking" in page

    def test_the_project_page_opens_on_the_risks(self, _starter, platform_project, client, graph):
        """No questions, no gate: the card's risks and a rating for each."""
        project = _start(client, graph, platform_project, starter=_starter)
        page = client.get(f"/p/{platform_project}/projects/{project['id']}").text
        assert "Risk register" in page
        assert "Loan officers rubber-stamp" in page          # the risk itself
        assert "Overreliance" in page                        # its VAIR typing, folded in its row
        assert page.count("<select") == 11                   # impact and likelihood per risk, and the profile
        assert "Suggest with AI" in page

    def test_nothing_asks_the_three_questions(self, _starter, platform_project, client, graph):
        """"Annex III" itself still occurs: it is in the objectives' own text.
        What must be gone are the question form and its answers."""
        project = _start(client, graph, platform_project, starter=_starter)
        page = client.get(f"/p/{platform_project}/projects/{project['id']}").text
        for gone in ("the three questions", 'type="radio"', "/answer", "Confirm"):
            assert gone not in page, gone

    def test_the_matrix_is_empty_before_the_mapping(self, _starter, platform_project, client, graph):
        """Nothing is in the matrix until a risk is mapped; the list of 50 is what /objectives is for."""
        project = _start(client, graph, platform_project, starter=_starter)
        page = client.get(f"/p/{platform_project}/projects/{project['id']}").text
        assert page.count("No objective yet.") == 5
        for gone in ("Tier 1", 'id="tier-1"', 'data-objective="'):
            assert gone not in page, gone

    def test_the_matrix_fills_once_it_has(self, _starter, platform_project, client, graph):
        project = _start(client, graph, platform_project, starter=_starter)
        client.post(f"/p/{platform_project}/projects/{project['id']}/map", follow_redirects=False)
        page = client.get(f"/p/{platform_project}/projects/{project['id']}").text
        assert "Risk and control matrix" in page and 'class="co-chip ' in page

    def test_rating_from_the_page_reorders_the_matrix(self, _starter, platform_project, client, graph):
        project = _start(client, graph, platform_project, starter=_starter)
        client.post(f"/p/{platform_project}/projects/{project['id']}/map", follow_redirects=False)
        data = {}
        for n in range(5):
            data[f"impact:risk{n}"] = "5" if n == 2 else "1"
            data[f"likelihood:risk{n}"] = "5" if n == 2 else "1"
        client.post(f"/p/{platform_project}/projects/{project['id']}/severity", data=data, follow_redirects=False)
        page = client.get(f"/p/{platform_project}/projects/{project['id']}").text
        rows = re.findall(r'<tr class="co-mrow[^"]*" data-risk="(\w+)"', page)
        assert rows[0] == "risk2"
        assert '<span class="co-rating co-rating--critical">25</span>' in page

    def test_a_rating_that_is_not_a_number_is_refused(self, _starter, platform_project, client, graph):
        project = _start(client, graph, platform_project, starter=_starter)
        response = client.post(
            f"/p/{platform_project}/projects/{project['id']}/severity",
            data={"impact:risk2": "high"},
        )
        assert response.status_code == 400


class TestReachedWithoutAProject:
    """The project is chosen once, on the launcher. This service does not ask
    again: it sends the reader to the one place that answers it."""

    def test_the_way_in_is_the_launcher(self, client):
        response = client.get("/", follow_redirects=False)
        assert response.status_code == 307
        assert response.headers["location"].startswith("http")

    def test_the_catalogue_still_reads_without_a_project(self, client):
        # The objectives are the same for everyone, so they are not behind a
        # project and not behind the redirect either.
        assert client.get("/objectives").status_code == 200
