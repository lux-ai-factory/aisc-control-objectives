"""An assessment, end to end: the AI Card, rate the risks, map, key objectives.

Driven through the HTTP surface against a real database, with a fake mapper,
so what is tested is the flow rather than a model's judgement. An assessment is
of one saved card version, so each one is made with
`Projects.create(..., system_id)` on a version of its own; how an assessment is
started is tested in test_assessment_of_a_version.py.
"""

from __future__ import annotations

import json
import re

import pytest
from fastapi.testclient import TestClient

from aisc_control_objectives.api.app import create_app
from aisc_control_objectives.config import RunConfig
from aisc_control_objectives.projects import Projects
from step2_support import FakeMapper  # noqa: F401  (imported from here by older tests)


@pytest.fixture()
def projects(repository, objectives):
    from aisc_control_objectives.library import FULL_AI_ACT

    return Projects(repository, objectives, FakeMapper(), model="fake/model", default_profile=FULL_AI_ACT)


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
            f"/p/{platform_project}/api/projects/{project['id']}/ratings",
            json={"risk2": {"impact": 5, "likelihood": 2}, "risk4": {"impact": 1}},
        )
        assert rated.status_code == 200
        assert rated.json()["severity"]["impact"] == {"risk2": 5, "risk4": 1}

    def test_a_rating_for_a_risk_this_card_lacks_is_refused(self, _starter, platform_project, client, graph):
        project = _start(client, graph, platform_project, starter=_starter)
        response = client.post(
            f"/p/{platform_project}/api/projects/{project['id']}/ratings", json={"risk99": {"impact": 5}}
        )
        assert response.status_code == 422
        assert "risk99" in response.text

    def test_the_ratings_survive_a_reload(self, _starter, platform_project, client, graph):
        project = _start(client, graph, platform_project, starter=_starter)
        client.post(f"/p/{platform_project}/api/projects/{project['id']}/ratings", json={"risk2": {"impact": 5}})
        again = client.get(f"/p/{platform_project}/api/projects/{project['id']}").json()
        assert again["severity"]["impact"]["risk2"] == 5

    def test_the_impact_only_severity_route_is_gone(self, _starter, platform_project, client, graph):
        """Ratings are impact x likelihood, through /ratings; the route that set the impact alone is retired."""
        project = _start(client, graph, platform_project, starter=_starter)
        response = client.post(f"/p/{platform_project}/api/projects/{project['id']}/severity", json={"risk2": 5})
        assert response.status_code in (404, 405)
        assert client.get(f"/p/{platform_project}/api/projects/{project['id']}").json()["severity"]["impact"] == {}


class TestMappingAndKeys:
    @pytest.fixture()
    def mapped(self, _starter, platform_project, client, graph):
        project = _start(client, graph, platform_project, starter=_starter)
        return client.post(f"/p/{platform_project}/api/projects/{project['id']}/map").json()

    def test_every_risk_is_mapped(self, platform_project, mapped):
        assert set(mapped["mapping_run"]["mappings"]) == {f"risk{n}" for n in range(5)}
        assert mapped["mapping_run"]["model"] == "fake/model"

    def test_rating_the_risks_moves_the_key_objectives(self, platform_project, client, mapped):
        def both(**parts):      # a risk counts once impact and likelihood are both rated
            return {risk: {"impact": n, "likelihood": n} for risk, n in parts.items()}

        oversight = client.post(
            f"/p/{platform_project}/api/projects/{mapped['id']}/ratings",
            json=both(risk2=5, risk1=5, risk4=1, risk0=1, risk3=1),
        ).json()
        poisoning = client.post(
            f"/p/{platform_project}/api/projects/{mapped['id']}/ratings",
            json=both(risk2=1, risk1=1, risk4=5, risk3=5, risk0=1),
        ).json()
        first = {p["objective_id"] for p in oversight["priorities"] if p["key"]}
        second = {p["objective_id"] for p in poisoning["priorities"] if p["key"]}
        assert "O1" in first and "O1" not in second
        assert "O7" in second and "O7" not in first

    def test_no_more_than_seven_are_key_by_default(self, platform_project, client, mapped):
        rated = client.post(
            f"/p/{platform_project}/api/projects/{mapped['id']}/ratings",
            json={f"risk{n}": {"impact": 5, "likelihood": 5} for n in range(5)},
        ).json()
        assert sum(p["key"] for p in rated["priorities"]) <= 7

class TestTheProjectRoot:
    """The launcher's Set control objectives card opens /p/{project}: it goes straight to the
    assessment of the latest card version (the page with the left menu), or to the assessments
    page, where one is started, when that version has none or the platform does not say."""

    @staticmethod
    def _latest_is(monkeypatch, pid):
        from aisc_control_objectives import upstream

        monkeypatch.setattr(upstream, "latest_version", lambda project, auth: pid and {"pid": pid, "number": 1})

    def test_it_opens_the_latest_versions_assessment(self, _starter, platform_project, client, graph, monkeypatch):
        projects, versions = _starter
        older = projects.create(platform_project, "MCAS", json.dumps(graph), graph, versions(platform_project))
        latest = versions(platform_project)
        newer = projects.create(platform_project, "MCAS", json.dumps(graph), graph, latest)
        assert older.record.id != newer.record.id
        self._latest_is(monkeypatch, latest)
        r = client.get(f"/p/{platform_project}", follow_redirects=False)
        assert r.status_code == 307
        assert r.headers["location"] == f"/p/{platform_project}/projects/{newer.record.id}"

    def test_with_no_assessment_of_the_latest_version_it_opens_the_assessments_page(
            self, _starter, platform_project, client, graph, monkeypatch):
        projects, versions = _starter
        projects.create(platform_project, "MCAS", json.dumps(graph), graph, versions(platform_project))
        self._latest_is(monkeypatch, versions(platform_project))   # a newer version, not assessed yet
        r = client.get(f"/p/{platform_project}", follow_redirects=False)
        assert (r.status_code, r.headers["location"]) == (307, f"/p/{platform_project}/projects")

    def test_with_no_card_yet_it_opens_the_assessments_page(self, platform_project, client, monkeypatch):
        self._latest_is(monkeypatch, None)
        r = client.get(f"/p/{platform_project}", follow_redirects=False)
        assert (r.status_code, r.headers["location"]) == (307, f"/p/{platform_project}/projects")

    def test_when_the_platform_does_not_answer_it_opens_the_assessments_page(
            self, platform_project, client, monkeypatch):
        from aisc_control_objectives import upstream

        def down(project, auth):
            raise upstream.UpstreamDown("no answer")

        monkeypatch.setattr(upstream, "latest_version", down)
        r = client.get(f"/p/{platform_project}", follow_redirects=False)
        assert (r.status_code, r.headers["location"]) == (307, f"/p/{platform_project}/projects")

class TestTheNavigation:
    def test_the_logo_links_to_the_project_on_the_launcher(self, platform_project, client):
        """Like the qualification app's: inside a project the mark goes back
        to that project's page, where the other five steps are."""
        page = client.get(f"/p/{platform_project}/objectives").text
        assert f'<a class="brand" href="http://localhost:8100/p/{platform_project}">' in page

    def test_every_page_carries_the_navigation(self, _starter, platform_project, client, graph):
        project = _start(client, graph, platform_project, starter=_starter)
        base = f"/p/{platform_project}"
        # Inside a project every page links on within it, so navigating never
        # loses which project is being assessed.
        for path in (f"{base}/objectives", f"{base}/projects",
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
        assert "Awaiting your rating" in page and "rank" not in page.lower()

    def test_the_projects_page_opens_the_latest_versions_assessment_once_there_is_one(
        self, _starter, platform_project, client, graph, versions
    ):
        """Starting again on an assessed version only opened its assessment: the page says so."""
        page = client.get(f"/p/{platform_project}/projects").text
        assert "Start assessment" in page and "Open the assessment of" not in page
        project = _start(client, graph, platform_project, starter=_starter)
        page = client.get(f"/p/{platform_project}/projects").text
        assert "Start assessment" not in page
        assert f'<a class="btn" href="/p/{platform_project}/projects/{project['id']}">Open the assessment of v1</a>' in page
        versions(platform_project)       # v2 is saved, not yet assessed
        page = client.get(f"/p/{platform_project}/projects").text
        assert "Start assessment" in page and "Open the assessment of" not in page

    def test_the_projects_page_says_which_assessment_may_still_change(
        self, _starter, platform_project, client, graph
    ):
        """Only the latest card version's assessment is rated and mapped; the older ones are kept as they were."""
        _start(client, graph, platform_project, name="First", starter=_starter)
        _start(client, graph, platform_project, name="Second", starter=_starter)
        page = client.get(f"/p/{platform_project}/projects").text
        listed = re.findall(r'>(First|Second)</a>.*?<p class="co-reason">\s*(v\d) \u00b7 (latest|read-only) \u00b7', page, re.S)
        assert sorted(listed) == [("First", "v1", "read-only"), ("Second", "v2", "latest")]

    def test_the_project_page_opens_on_the_risks(self, _starter, platform_project, client, graph):
        """No questions, no gate: the card's risks and a rating for each."""
        project = _start(client, graph, platform_project, starter=_starter)
        page = client.get(f"/p/{platform_project}/projects/{project['id']}").text
        assert "Rate the risks" in page
        assert "Loan officers rubber-stamp" in page          # the risk itself
        assert "Overreliance" in page                        # its VAIR typing, folded in its row
        assert page.count("<select") == 11                   # impact and likelihood per risk, and the profile
        assert "Suggest with AI" in page

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
        assert "Link objectives to risks" in page and 'class="co-chip ' in page

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


def test_each_change_to_an_assessment_moves_its_updated_at(projects, versions, graph, platform_project,
                                                           repository):
    """Ratings, keys and mappings write only child rows, so `updated_at` never moved and the
    projects page showed the start time and the wrong order."""
    a = projects.create(platform_project, "MCAS", json.dumps(graph), graph, versions(platform_project)).record.id

    def changes(step) -> bool:
        was = repository.get(a).updated_at
        step()
        return repository.get(a).updated_at > was

    assert changes(lambda: projects.rate(a, {"risk1": 4}, {"risk1": 2}))
    assert changes(lambda: projects.map_risks_of(a))
    assert changes(lambda: projects.map_by_hand(a, "risk1", ["O1"]))
    assert changes(lambda: projects.set_keys(a, {"O1": True}))
    assert not changes(lambda: projects.rate(a, {"risk1": 4}, {"risk1": 2}))   # nothing changed
