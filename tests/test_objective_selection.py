"""Which control objectives the project takes forward to step 4.

What goes forward is what the risk and control matrix holds: every objective
mapped to at least one risk, in catalogue order, whoever mapped it. The step 2 fixtures (mapper,
client, graph, start) are in conftest.py, their helpers in step2_support.py.
"""

from __future__ import annotations

from step2_support import FakeMapper, SwitchableMapper, _api, in_order  # noqa: F401

MAPPED = in_order({oid for oids in FakeMapper.BY_RISK.values() for oid in oids})


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


# Each objective carries its trustworthiness dimension as a tag

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


class _MeanwhileMapper(SwitchableMapper):
    """Does something to the assessment while the model 'thinks', once, on the first risk."""

    def __init__(self, meanwhile):
        super().__init__()
        self.meanwhile, self.done = meanwhile, False

    def propose(self, risk, findings=()):
        if not self.done:
            self.done = True
            self.meanwhile()
        return super().propose(risk, findings)


def test_a_run_saves_nothing_when_a_person_mapped_by_hand_meanwhile(client, start, platform_project):
    """A run takes minutes; it read the assessment before its model calls and saved over whatever
    happened meanwhile (code review 2026-10-05). It now checks again before saving, and stops."""
    http, projects = client
    a = start()
    projects._mapper = _MeanwhileMapper(lambda: projects.map_by_hand(a, "risk0", ["O5"]))
    r = http.post(_api(platform_project, a, "/map"))
    assert r.status_code == 409 and "changed" in r.text
    after = http.get(_api(platform_project, a)).json()
    assert [o["objective_id"] for o in after["mapping_run"]["mappings"]["risk0"]["objectives"]] == ["O5"]


def test_a_run_saves_nothing_when_a_newer_card_version_came_meanwhile(client, start, platform_project,
                                                                      system_version):
    http, projects = client
    a = start()
    projects._mapper = _MeanwhileMapper(lambda: system_version(platform_project, 2))
    r = http.post(_api(platform_project, a, "/map"))
    assert r.status_code == 409
    assert http.get(_api(platform_project, a)).json()["mapping_run"] is None


def test_the_home_page_counts_assessments_without_deriving_them(client, start, platform_project, monkeypatch):
    """The home page shows how many assessments there are; it derived every one (card parse, scores,
    profile queries) to count them (code review 2026-10-05). A count is one query."""
    http, projects = client
    start(1)
    start(2)
    assert projects.count() == 2

    def no_list(*_a, **_k):
        raise AssertionError("the home page derived every assessment")

    monkeypatch.setattr(type(projects), "list", no_list)
    assert http.get(f"/p/{platform_project}").status_code == 200
