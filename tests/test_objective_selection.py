"""Which control objectives the project takes forward to step 4.

Until 2026-10-01 the assessor ticked them (evidence links plan 2026-09-30, step A, D1 and D2).
Since the risk and control matrix, what goes forward is what the matrix holds: every objective
mapped to at least one risk, in catalogue order, whoever mapped it. The fixtures here are shared
by the other step 2 tests.
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


def test_nothing_is_taken_forward_before_the_mapping(client, start, platform_project):
    http, _ = client
    assert _selected(http, platform_project, start()) == []


def test_the_mapped_objectives_are_taken_forward(client, start, platform_project):
    http, _ = client
    assessment = start()
    http.post(_api(platform_project, assessment, "/map"))
    assert _selected(http, platform_project, assessment) == MAPPED


def test_mapping_again_takes_forward_what_the_new_matrix_holds(client, start, platform_project, mapper):
    """O16 leaves the matrix, O26 enters: the scope follows."""
    http, _ = client
    assessment = start()
    http.post(_api(platform_project, assessment, "/map"))
    mapper.by_risk["risk4"] = ["O7", "O11", "O26"]          # O16 out, O26 in
    http.post(_api(platform_project, assessment, "/map"))
    assert _selected(http, platform_project, assessment) == in_order((set(MAPPED) - {"O16"}) | {"O26"})


def test_a_new_version_takes_forward_its_own_matrix(client, start, platform_project, mapper):
    http, _ = client
    first = start(1)
    http.post(_api(platform_project, first, "/map"))
    second = start(2)
    assert _selected(http, platform_project, second) == []
    mapper.by_risk = {"risk0": ["O3"]}
    http.post(_api(platform_project, second, "/map"))
    assert _selected(http, platform_project, second) == ["O3"]
    assert _selected(http, platform_project, first) == MAPPED


def test_there_is_no_selection_to_save_any_more(client, start, platform_project):
    http, _ = client
    assessment = start()
    assert http.post(_api(platform_project, assessment, "/selection"), json={"objective_ids": []}).status_code == 404
    assert http.post(f"/p/{platform_project}/projects/{assessment}/selection").status_code in (404, 405)


def test_an_older_version_shows_its_matrix_without_ticks(client, start, platform_project):
    http, _ = client
    first = start(1)
    http.post(_api(platform_project, first, "/map"))
    start(2)
    page = http.get(f"/p/{platform_project}/projects/{first}").text
    assert 'name="key"' not in page and "co-chip--" in page


# ── each objective carries its trustworthiness dimension as a tag (2026-10-01) ──

def test_each_objective_in_the_matrix_shows_its_dimension(client, start, platform_project, objectives):
    import re
    http, _ = client
    assessment = start()
    http.post(_api(platform_project, assessment, "/map"))
    page = http.get(f"/p/{platform_project}/projects/{assessment}").text
    # each chip's pop-up names the objective's dimension
    cells = re.findall(r'<div id="pop-\w+-(\w+)" class="co-chip-pop" popover>.*?<span class="co-dim co-dim--r(\d+)">(R\d+) ·', page, re.S)
    assert cells
    by_id = {o.id: o for o in objectives}
    for oid, n, macro in cells:
        assert by_id[oid].macro_id == macro == f"R{n}", oid


def test_every_dimension_has_its_colour():
    from pathlib import Path
    import aisc_control_objectives
    base = (Path(aisc_control_objectives.__file__).parent / "templates/_base.html.j2").read_text()
    for n in range(1, 12):
        assert f".co-dim--r{n} " in base, n
