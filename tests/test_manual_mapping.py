"""A person maps a risk to its control objectives (2026-10-01).

The mapping is made by the AI (the risk mapper, "Map with AI") or by a person, risk by risk. A
person's save replaces that risk's mapping: an objective kept from the AI keeps its quote and stays
the AI's; one the person adds is theirs, with no quote. It works with no AI run at all. The
scope (what goes forward to step 4) is what the matrix then holds. Mapping with AI again
replaces the whole mapping, a person's edits included.
"""
from __future__ import annotations

from test_objective_selection import (  # noqa: F401  (fixtures)
    _api, client, graph, in_order, mapper, start,
)


def _payload(http, platform_project, assessment):
    response = http.get(_api(platform_project, assessment))
    assert response.status_code == 200, response.text
    return response.json()


def _map_by_hand(http, platform_project, assessment, risk_id, objective_ids):
    return http.post(_api(platform_project, assessment, f"/risks/{risk_id}/mapping"),
                     json={"objective_ids": objective_ids})


def _rows(body, risk_id):
    return [(o["objective_id"], o["source"], o["quote"])
            for o in body["mapping_run"]["mappings"][risk_id]["objectives"]]


def test_a_person_maps_a_risk_with_no_ai_run(client, start, platform_project):
    http, _ = client
    assessment = start()
    response = _map_by_hand(http, platform_project, assessment, "risk0", ["O10", "O5"])
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["mapped"] is True
    assert _rows(body, "risk0") == [("O5", "person", ""), ("O10", "person", "")]
    # what is in the matrix goes forward, in catalogue order
    assert body["selected"] == ["O5", "O10"]
    by_id = {p["objective_id"]: p for p in body["priorities"]}
    assert by_id["O5"]["score"] == 9 and by_id["O5"]["risk_ids"] == ["risk0"]      # unrated: 3 x 3


def test_editing_after_the_ai_keeps_its_rows_and_adds_the_persons(client, start, platform_project):
    http, _ = client
    assessment = start()
    http.post(_api(platform_project, assessment, "/map"))      # risk4: O7, O16, O11
    before = _payload(http, platform_project, assessment)
    quote_of_o7 = next(q for oid, _, q in _rows(before, "risk4") if oid == "O7")
    body = _map_by_hand(http, platform_project, assessment, "risk4", ["O7", "O30"]).json()
    assert _rows(body, "risk4") == [("O7", "ai", quote_of_o7), ("O30", "person", "")]
    # O16 left risk4 and no other risk maps it: unticked; O30 is new: ticked;
    # O11 left risk4 but risk0 still maps it: kept
    assert "O16" not in body["selected"] and "O30" in body["selected"] and "O11" in body["selected"]
    # the other risks are untouched
    assert _rows(body, "risk0") == _rows(before, "risk0")


def test_each_risk_adds_its_objectives_to_the_scope(client, start, platform_project):
    http, _ = client
    assessment = start()
    _map_by_hand(http, platform_project, assessment, "risk0", ["O5"])
    body = _map_by_hand(http, platform_project, assessment, "risk1", ["O6"]).json()
    assert body["selected"] == ["O5", "O6"]


def test_mapping_with_ai_again_replaces_the_persons_edits(client, start, platform_project):
    http, _ = client
    assessment = start()
    _map_by_hand(http, platform_project, assessment, "risk4", ["O30"])
    body = http.post(_api(platform_project, assessment, "/map")).json()
    assert {source for rid in body["mapping_run"]["mappings"]
            for _, source, _ in _rows(body, rid)} == {"ai"}
    assert "O30" not in {oid for oid, _, _ in _rows(body, "risk4")}


def test_an_empty_list_unmaps_the_risk(client, start, platform_project):
    http, _ = client
    assessment = start()
    _map_by_hand(http, platform_project, assessment, "risk0", ["O5"])
    body = _map_by_hand(http, platform_project, assessment, "risk0", []).json()
    assert _rows(body, "risk0") == [] and body["selected"] == []


def test_an_unknown_objective_or_risk_is_refused(client, start, platform_project):
    http, _ = client
    assessment = start()
    response = _map_by_hand(http, platform_project, assessment, "risk0", ["O5", "O99"])
    assert response.status_code == 422 and "O99" in response.text
    response = _map_by_hand(http, platform_project, assessment, "nope", ["O5"])
    assert response.status_code == 422 and "nope" in response.text
    assert _payload(http, platform_project, assessment)["mapped"] is False


def test_an_older_versions_mapping_is_read_only(client, start, platform_project):
    http, _ = client
    first = start(1)
    start(2)
    assert _map_by_hand(http, platform_project, first, "risk0", ["O5"]).status_code == 409


def test_the_page_form_saves_a_risks_mapping(client, start, platform_project):
    http, _ = client
    assessment = start()
    response = http.post(f"/p/{platform_project}/projects/{assessment}/risks/risk0/mapping",
                         data={"objective": ["O5", "O10"]})
    assert response.status_code == 303
    body = _payload(http, platform_project, assessment)
    assert [oid for oid, _, _ in _rows(body, "risk0")] == ["O5", "O10"]


def test_the_page_offers_both_ways_to_map(client, start, platform_project):
    http, _ = client
    assessment = start()
    _map_by_hand(http, platform_project, assessment, "risk0", ["O5"])
    page = http.get(f"/p/{platform_project}/projects/{assessment}").text
    assert "Suggest with AI" in page
    assert f'action="/p/{platform_project}/projects/{assessment}/risks/risk0/mapping"' in page
    # every objective can be ticked for a risk, grouped by dimension
    assert page.count('name="objective" value="O50"') >= 1
    assert "co-chip--person" in page
