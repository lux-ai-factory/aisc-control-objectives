"""Which control objectives the project takes forward (evidence links plan 2026-09-30, step A).

The assessor ticks objectives on the assessment page; only the ticked ones reach
step 4, where tests and controls are linked to them. Decisions D1 and D2:

- D1: once the risks are mapped, every objective the mapping linked to a risk
  starts ticked; the assessor can untick them and tick any other one.
- D2: a new card version starts from the previous version's selection. An
  objective the previous mapping had and this one lost is unticked; one this
  mapping adds is ticked; everything else (ticked or not) is kept as it was.
  Mapping the same assessment again follows the same rule.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from aisc_control_objectives.api.app import create_app
from aisc_control_objectives.config import RunConfig
from aisc_control_objectives.projects import Projects
from aisc_control_objectives.risk_mapping import MappedObjective, Mapping
from test_projects import FakeMapper


class SwitchableMapper:
    """FakeMapper whose answers the test can change between two mappings."""

    def __init__(self):
        self.by_risk = {rid: list(oids) for rid, oids in FakeMapper.BY_RISK.items()}

    def propose(self, risk, findings=()):
        return Mapping(
            risk_id=risk.id,
            objectives=[
                MappedObjective(objective_id=oid, quote=risk.text[:30], rationale="because")
                for oid in self.by_risk.get(risk.id, [])
            ],
        )


def in_order(ids) -> list[str]:
    """Catalogue order, as the selection is kept: O9 before O10."""
    return sorted(ids, key=lambda oid: int(oid[1:]))


MAPPED = in_order({oid for oids in FakeMapper.BY_RISK.values() for oid in oids})


@pytest.fixture()
def mapper():
    return SwitchableMapper()


@pytest.fixture()
def client(repository, objectives, mapper):
    projects = Projects(repository, objectives, mapper, model="fake/model")
    return TestClient(create_app(objectives, projects, base_config=RunConfig()),
                      follow_redirects=False), projects


@pytest.fixture()
def graph(fixtures_dir):
    return json.loads((fixtures_dir / "mcas.ontology.jsonld").read_text())


@pytest.fixture()
def start(client, graph, platform_project, system_version):
    """An assessment of card version `number`, as its id."""
    _, projects = client

    def make(number: int = 1) -> str:
        version = system_version(platform_project, number)
        return projects.create(platform_project, "MCAS", json.dumps(graph), graph, version).record.id

    return make


def _api(platform_project, assessment, suffix=""):
    return f"/p/{platform_project}/api/projects/{assessment}{suffix}"


def _selected(http, platform_project, assessment) -> list[str]:
    response = http.get(_api(platform_project, assessment))
    assert response.status_code == 200, response.text
    return response.json()["selected"]


def test_nothing_is_selected_before_the_mapping(client, start, platform_project):
    http, _ = client
    assert _selected(http, platform_project, start()) == []


def test_d1_the_mapping_ticks_every_mapped_objective(client, start, platform_project):
    http, _ = client
    assessment = start()
    http.post(_api(platform_project, assessment, "/map"))
    assert _selected(http, platform_project, assessment) == MAPPED


def test_saving_a_selection_replaces_it(client, start, platform_project):
    http, _ = client
    assessment = start()
    http.post(_api(platform_project, assessment, "/map"))
    response = http.post(_api(platform_project, assessment, "/selection"),
                         json={"objective_ids": ["O1", "O24"]})
    assert response.status_code == 200, response.text
    assert response.json()["selected"] == ["O1", "O24"]
    assert _selected(http, platform_project, assessment) == ["O1", "O24"]


def test_an_unticked_selection_stays_empty(client, start, platform_project, mapper):
    """Saving nothing is a choice: mapping again does not tick the old ones back."""
    http, _ = client
    assessment = start()
    http.post(_api(platform_project, assessment, "/map"))
    http.post(_api(platform_project, assessment, "/selection"), json={"objective_ids": []})
    http.post(_api(platform_project, assessment, "/map"))
    assert _selected(http, platform_project, assessment) == []


def test_an_objective_the_catalogue_lacks_is_refused(client, start, platform_project):
    http, _ = client
    assessment = start()
    response = http.post(_api(platform_project, assessment, "/selection"),
                         json={"objective_ids": ["O1", "R99.9"]})
    assert response.status_code == 422
    assert "R99.9" in response.json()["detail"]
    assert _selected(http, platform_project, assessment) == []


def test_a_selection_can_be_saved_before_any_mapping(client, start, platform_project):
    http, _ = client
    assessment = start()
    http.post(_api(platform_project, assessment, "/selection"), json={"objective_ids": ["O24"]})
    assert _selected(http, platform_project, assessment) == ["O24"]


def test_mapping_again_keeps_the_choices_and_follows_the_mapping(
    client, start, platform_project, mapper
):
    """O1 unticked by hand stays unticked; O24 ticked by hand stays; O16, no
    longer mapped, is unticked; O26, newly mapped, is ticked."""
    http, _ = client
    assessment = start()
    http.post(_api(platform_project, assessment, "/map"))
    chosen = [oid for oid in MAPPED if oid != "O1"] + ["O24"]
    http.post(_api(platform_project, assessment, "/selection"), json={"objective_ids": chosen})

    mapper.by_risk["risk4"] = ["O7", "O11", "O26"]          # O16 out, O26 in
    http.post(_api(platform_project, assessment, "/map"))

    expected = in_order((set(chosen) - {"O16"}) | {"O26"})
    assert _selected(http, platform_project, assessment) == expected


def test_d2_a_new_version_starts_from_the_previous_selection(
    client, start, platform_project, mapper
):
    http, _ = client
    first = start(1)
    http.post(_api(platform_project, first, "/map"))
    chosen = [oid for oid in MAPPED if oid != "O1"] + ["O24"]
    http.post(_api(platform_project, first, "/selection"), json={"objective_ids": chosen})

    second = start(2)
    mapper.by_risk["risk4"] = ["O7", "O11", "O26"]
    http.post(_api(platform_project, second, "/map"))

    assert _selected(http, platform_project, second) == in_order((set(chosen) - {"O16"}) | {"O26"})
    assert _selected(http, platform_project, first) == in_order(chosen)


def test_an_older_versions_selection_is_read_only(client, start, platform_project):
    http, _ = client
    first = start(1)
    start(2)
    response = http.post(_api(platform_project, first, "/selection"), json={"objective_ids": []})
    assert response.status_code == 409


class TestThePage:
    def test_each_objective_has_a_checkbox_ticked_as_selected(self, client, start, platform_project):
        http, _ = client
        assessment = start()
        http.post(_api(platform_project, assessment, "/map"))
        http.post(_api(platform_project, assessment, "/selection"), json={"objective_ids": ["O1"]})
        page = http.get(f"/p/{platform_project}/projects/{assessment}").text
        assert f'action="/p/{platform_project}/projects/{assessment}/selection"' in page
        assert 'name="objective" value="O1" checked' in page
        assert 'name="objective" value="O4">' in page
        assert "Save selection" in page

    def test_the_form_saves_the_ticked_objectives(self, client, start, platform_project):
        http, _ = client
        assessment = start()
        http.post(_api(platform_project, assessment, "/map"))
        response = http.post(f"/p/{platform_project}/projects/{assessment}/selection",
                             data={"objective": ["O4", "O24"]})
        assert response.status_code == 303
        assert _selected(http, platform_project, assessment) == ["O4", "O24"]

    def test_an_older_version_shows_no_checkboxes(self, client, start, platform_project):
        http, _ = client
        first = start(1)
        http.post(_api(platform_project, first, "/map"))
        start(2)
        page = http.get(f"/p/{platform_project}/projects/{first}").text
        assert 'name="objective"' not in page
        assert "Save selection" not in page


# ── each objective carries its trustworthiness dimension as a tag (2026-10-01) ──

def test_each_objective_shows_its_dimension_as_a_tag(client, start, platform_project, objectives):
    import re
    http, _ = client
    assessment = start()
    http.post(_api(platform_project, assessment, "/map"))
    page = http.get(f"/p/{platform_project}/projects/{assessment}")
    assert page.status_code == 200, page.text
    articles = re.findall(r'<article class="co-obj">(.*?)</article>', page.text, re.S)
    assert articles
    by_id = {o.id: o for o in objectives}
    for article in articles:
        oid = re.search(r'<span class="co-obj-id">([^<]+)</span>', article).group(1)
        o = by_id[oid]
        n = o.macro_id.lstrip("R")
        assert (f'<span class="co-dim co-dim--r{n}" title="Trustworthiness dimension">'
                f'{o.macro_id} · {o.macro_title}</span>') in article, oid
        assert 'class="co-family"' not in article


def test_every_dimension_has_its_colour():
    from pathlib import Path
    import aisc_control_objectives
    base = (Path(aisc_control_objectives.__file__).parent / "templates/_base.html.j2").read_text()
    for n in range(1, 12):
        assert f".co-dim--r{n} " in base, n
